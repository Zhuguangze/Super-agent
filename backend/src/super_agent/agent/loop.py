"""Agent Loop——按 Turn 运行：LLM 流式响应 → 并行执行工具 → 结果回填 → 继续或结束（spec §4.4）。

事件经 on_event 透传（provider 流事件原样、loop 相位事件用 agent.types 的 dataclass）。
Steer 队列在当前 Turn 的工具执行完成后被消费（CONTEXT.md 的 Steer 语义）；
loop 结束后队列若仍有剩余，由调用方（app 层）按 Follow-up 语义续跑。
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
    ToolDef,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
)
from super_agent.agent.hooks import Hooks
from super_agent.agent.tools import validate_params
from super_agent.agent.types import (
    AgentEnd,
    AgentEventEmitter,
    AgentStart,
    AgentTool,
    AgentToolResult,
    MessagesAppended,
    ToolExecutionEnd,
    ToolExecutionStart,
    ToolExecutionUpdate,
    TurnEnd,
    TurnStart,
)

DEFAULT_MAX_TURNS = 32


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
        while True:
            turn += 1
            await on_event(TurnStart(turn=turn))

            assistant: Message | None = None
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
                    usage_total = _add_usage(usage_total, ev.usage)

            if assistant is None:  # provider 未产出消息（异常路径，防御）
                stop_reason = "error"
                break
            last_assistant = assistant
            context.append(assistant)

            tool_uses = [b for b in assistant.content if isinstance(b, ToolUseBlock)]
            if not tool_uses:
                await on_event(TurnEnd(turn=turn, stop_reason="end_turn"))
                break

            tool_results = await _execute_tools(
                tool_uses, tools_by_name, hooks, abort_signal, on_event
            )
            result_message = Message(role="toolResult", content=tool_results)
            context.append(result_message)

            # Steer：当前 Turn 的工具执行完成后消费
            steered = _drain(steer_queue)
            steer_messages = [
                Message(role="user", content=[TextBlock(text)]) for text in steered
            ]
            context.extend(steer_messages)
            appended = [result_message, *steer_messages]
            await on_event(MessagesAppended(messages=appended))

            await on_event(TurnEnd(turn=turn, stop_reason="tool_use"))
            await hooks.run_finish_turn(turn)
            if turn >= max_turns:
                stop_reason = "max_turns"
                break
    except asyncio.CancelledError:
        # Abort：不发 AgentEnd（app 层按 aborted 语义收尾），错误直接向外传播
        raise

    await on_event(AgentEnd(stop_reason=stop_reason, usage=usage_total, message=last_assistant))
    return AgentEnd(stop_reason=stop_reason, usage=usage_total, message=last_assistant)


async def _execute_tools(
    tool_uses: list[ToolUseBlock],
    tools_by_name: dict[str, AgentTool],
    hooks: Hooks,
    signal: asyncio.Event,
    on_event: AgentEventEmitter,
) -> list[ToolResultBlock]:
    """默认并行（asyncio.gather）；任一 sequential 工具参与则整体串行。"""

    async def run_one(tool_use: ToolUseBlock) -> ToolResultBlock:
        tool = tools_by_name.get(tool_use.name)
        await on_event(
            ToolExecutionStart(
                tool_call_id=tool_use.id, name=tool_use.name, params=tool_use.input
            )
        )
        result = await _run_single(tool, tool_use, hooks, signal, on_event)
        await on_event(ToolExecutionEnd(tool_call_id=tool_use.id, result=result))
        return ToolResultBlock(
            tool_use_id=tool_use.id,
            content=result.content,
            is_error=result.is_error,
            blocks=result.blocks,
        )

    used = [tools_by_name[u.name] for u in tool_uses if u.name in tools_by_name]
    if any(getattr(t, "execution_mode", "parallel") == "sequential" for t in used):
        results = [await run_one(u) for u in tool_uses]
    else:
        results = list(await asyncio.gather(*(run_one(u) for u in tool_uses)))
    return results


async def _run_single(
    tool: AgentTool | None,
    tool_use: ToolUseBlock,
    hooks: Hooks,
    signal: asyncio.Event,
    on_event: AgentEventEmitter,
) -> AgentToolResult:
    if tool is None:
        return AgentToolResult(content=f"未知工具: {tool_use.name}", is_error=True)

    error = validate_params(tool_use.input, tool.parameters)
    if error is not None:
        return AgentToolResult(content=f"参数校验失败: {error}", is_error=True)

    reason = await hooks.run_before_tool_call(tool.name, tool_use.input, tool_use.id)
    if reason is not None:
        return AgentToolResult(content=f"调用被拦截: {reason}", is_error=True)

    async def on_update(delta: str) -> None:
        await on_event(ToolExecutionUpdate(tool_call_id=tool_use.id, delta=delta))

    try:
        result = await tool.execute(tool_use.id, tool_use.input, signal, on_update)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # 工具异常不拖垮 loop，以错误结果回填（带类型名——空消息异常可辨）
        return AgentToolResult(content=f"工具执行异常: {type(exc).__name__}: {exc}", is_error=True)
    return await hooks.run_after_tool_call(tool_use.id, result)


def _tool_def(tool: AgentTool) -> ToolDef:
    return ToolDef(name=tool.name, description=tool.description, parameters=tool.parameters)


def _drain(queue: asyncio.Queue[str] | None) -> list[str]:
    if queue is None:
        return []
    items: list[str] = []
    while True:
        try:
            items.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            return items


def _add_usage(total: Usage, delta: Usage) -> Usage:
    return Usage(
        input_tokens=total.input_tokens + delta.input_tokens,
        output_tokens=total.output_tokens + delta.output_tokens,
        cache_read_tokens=total.cache_read_tokens + delta.cache_read_tokens,
        cache_write_tokens=total.cache_write_tokens + delta.cache_write_tokens,
    )
