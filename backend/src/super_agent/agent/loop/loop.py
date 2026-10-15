"""Agent Loop——按 Turn 运行：LLM 流式响应 → 并行执行工具 → 结果回填 → 继续或结束（spec §4.4）。

事件经 on_event 透传（provider 流事件原样、loop 相位事件用 agent.types 的 dataclass）。
双层循环：内层消费 tool 调用与 Steer（工具执行后，CONTEXT.md 的 Steer 语义），
外层消费 Follow-up（loop 本会停止后，CONTEXT.md 的 Follow-up 语义）。
取消（Abort）依赖消费方 task.cancel，CancelledError 直接向外传播。
"""

import asyncio
from collections.abc import Sequence

from super_agent.ai.provider import DEFAULT_EFFORT, Effort, Provider
from super_agent.ai.types import (
    Message,
    MessageEnd,
    MessageStart,
    TextBlock,
    TextDelta,
    ThinkingDelta,
    ToolUseBlock,
    Usage,
)
from super_agent.agent.execution import _execute_tools, _fail_truncated_tools, _tool_def
from super_agent.agent.hooks import Hooks
from super_agent.agent.queue import _drain
from super_agent.agent.types import (
    AgentEnd,
    AgentEventEmitter,
    AgentStart,
    AgentTool,
    MessagesAppended,
    TurnEnd,
    TurnStart,
)

DEFAULT_MAX_TURNS = 32


def _add_usage(total: Usage, delta: Usage) -> Usage:
    return Usage(
        input_tokens=total.input_tokens + delta.input_tokens,
        output_tokens=total.output_tokens + delta.output_tokens,
        cache_read_tokens=total.cache_read_tokens + delta.cache_read_tokens,
        cache_write_tokens=total.cache_write_tokens + delta.cache_write_tokens,
    )


async def agent_loop(
    provider: Provider,
    model_id: str,
    tools: Sequence[AgentTool],
    messages: list[Message],
    on_event: AgentEventEmitter,
    *,
    system: str | None = None,
    effort: Effort = DEFAULT_EFFORT,
    hooks: Hooks | None = None,
    steer_queue: asyncio.Queue[str] | None = None,
    queue_mode: str = "one-at-a-time",
    max_turns: int = DEFAULT_MAX_TURNS,
    signal: asyncio.Event | None = None,
) -> AgentEnd:
    """运行完整的 Agent Loop，返回 AgentEnd（带总 usage 与最后一条 assistant 消息）。"""
    hooks = hooks or Hooks()
    tool_defs = [_tool_def(t) for t in tools]
    tools_by_name = {t.name: t for t in tools}
    context = list(messages)
    usage_total = Usage()
    last_assistant: Message | None = None
    abort_signal = signal or asyncio.Event()

    await on_event(AgentStart())
    stop_reason = "end_turn"
    turn = 0
    try:
        # 外层循环：Follow-up 消息续跑（CONTEXT.md 的 Follow-up 语义）
        while True:
            explicit_continue = False
            # 内层循环：tool 调用与 Steer 插话
            while True:
                turn += 1
                await on_event(TurnStart(turn=turn))

                assistant: Message | None = None
                assistant_stop_reason: str | None = None
                stream = provider.stream(
                    context,
                    model_id,
                    system=system,
                    effort=effort,
                    tools=tool_defs or None,
                )
                async for ev in stream:
                    if isinstance(ev, (MessageStart, TextDelta, ThinkingDelta, MessageEnd)):
                        await on_event(ev)
                    if isinstance(ev, MessageEnd):
                        assistant = ev.message
                        assistant_stop_reason = ev.stop_reason
                        usage_total = _add_usage(usage_total, ev.usage)

                if assistant is None:  # provider 未产出消息（异常路径，防御）
                    stop_reason = "error"
                    break
                last_assistant = assistant
                context.append(assistant)

                tool_uses = [b for b in assistant.content if isinstance(b, ToolUseBlock)]
                if not tool_uses:
                    await on_event(TurnEnd(turn=turn, stop_reason="end_turn"))
                    if explicit_continue:
                        # 上一轮 finish_turn 请求续跑：强制再来一轮（context-only turn）
                        explicit_continue = False
                        continue
                    break

                if assistant_stop_reason == "max_tokens":
                    # 响应被截断：该消息的工具参数可能不完整，整批标失败、不执行
                    tool_results = await _fail_truncated_tools(tool_uses, on_event)
                    should_terminate = False
                else:
                    tool_results, should_terminate = await _execute_tools(
                        tool_uses, tools_by_name, hooks, abort_signal, on_event
                    )
                result_message = Message(role="toolResult", content=tool_results)
                context.append(result_message)

                # Steer：当前 Turn 的工具执行完成后消费
                steered = _drain(steer_queue, mode=queue_mode)
                steer_messages = [
                    Message(role="user", content=[TextBlock(text)]) for text in steered
                ]
                context.extend(steer_messages)
                appended = [result_message, *steer_messages]
                await on_event(MessagesAppended(messages=appended))

                await on_event(TurnEnd(turn=turn, stop_reason="tool_use"))
                decision = await hooks.run_finish_turn(turn)
                if decision == "end":
                    stop_reason = "finish_turn_end"
                    break
                explicit_continue = decision == "continue"
                if should_terminate:
                    # 整批被拒且全票请求终止：不再跑下一轮（finish_turn 已调用）
                    break
                if turn >= max_turns:
                    stop_reason = "max_turns"
                    break

            # 内层循环结束：按 Follow-up 语义续跑队列剩余消息
            if stop_reason != "end_turn":
                break
            followups = _drain(steer_queue, mode=queue_mode)
            if not followups:
                break
            followup_messages = [
                Message(role="user", content=[TextBlock(text)]) for text in followups
            ]
            context.extend(followup_messages)
            await on_event(MessagesAppended(messages=followup_messages))
    except asyncio.CancelledError:
        # Abort：不发 AgentEnd（app 层按 aborted 语义收尾），错误直接向外传播
        raise

    await on_event(AgentEnd(stop_reason=stop_reason, usage=usage_total, message=last_assistant))
    return AgentEnd(stop_reason=stop_reason, usage=usage_total, message=last_assistant)
