"""工具执行——批量执行、单工具执行、工具定义。"""

import asyncio
import logging

from super_agent.ai.types import ToolDef, ToolResultBlock, ToolUseBlock
from super_agent.agent.hooks import Hooks, ToolCallBlock
from super_agent.agent.tools import validate_params
from super_agent.agent.types import (
    AgentEventEmitter,
    AgentTool,
    AgentToolResult,
    ToolExecutionEnd,
    ToolExecutionStart,
    ToolExecutionUpdate,
)

logger = logging.getLogger(__name__)


def _tool_def(tool: AgentTool) -> ToolDef:
    return ToolDef(name=tool.name, description=tool.description, parameters=tool.parameters)


async def _execute_tools(
    tool_uses: list[ToolUseBlock],
    tools_by_name: dict[str, AgentTool],
    hooks: Hooks,
    signal: asyncio.Event,
    on_event: AgentEventEmitter,
) -> tuple[list[ToolResultBlock], bool]:
    """默认并行（asyncio.gather）；任一 sequential 工具参与则整体串行。

    返回 (tool_result 块列表, should_terminate)——should_terminate 仅当整批所有结果
    都设了 terminate（全票）时才为 True。
    """

    async def run_one(tool_use: ToolUseBlock) -> tuple[ToolResultBlock, bool]:
        tool = tools_by_name.get(tool_use.name)
        result: AgentToolResult
        try:
            await on_event(
                ToolExecutionStart(
                    tool_call_id=tool_use.id, name=tool_use.name, params=tool_use.input
                )
            )
            result = await _run_single(tool, tool_use, hooks, signal, on_event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # 兜底降级：单个工具管道（含钩子、事件发射、落盘）的任何故障都只影响
            # 这一个工具，不中止整轮——中止会留下没有 tool_result 的悬空 tool_use。
            logger.exception("工具执行管道故障: %s", tool_use.name)
            result = AgentToolResult(
                content=f"工具管道故障 {type(exc).__name__}: {exc}",
                is_error=True,
            )
        try:
            await on_event(ToolExecutionEnd(tool_call_id=tool_use.id, result=result))
        except asyncio.CancelledError:
            raise
        except Exception:
            # 事件通道本身故障，无处上报；记日志即可，结果仍照常回填给 LLM
            logger.exception("tool_execution_end 发送失败: %s", tool_use.name)
        block = ToolResultBlock(
            tool_use_id=tool_use.id,
            content=result.content,
            is_error=result.is_error,
            blocks=result.blocks,
        )
        return block, result.terminate

    used = [tools_by_name[u.name] for u in tool_uses if u.name in tools_by_name]
    if any(getattr(t, "execution_mode", "parallel") == "sequential" for t in used):
        results = [await run_one(u) for u in tool_uses]
    else:
        results = list(await asyncio.gather(*(run_one(u) for u in tool_uses)))
    blocks = [b for b, _ in results]
    should_terminate = bool(results) and all(t for _, t in results)
    return blocks, should_terminate


async def _fail_truncated_tools(
    tool_uses: list[ToolUseBlock], on_event: AgentEventEmitter
) -> list[ToolResultBlock]:
    """响应被截断（stop_reason="max_tokens"）时的整批工具：全部标失败、不执行。"""
    blocks: list[ToolResultBlock] = []
    for tool_use in tool_uses:
        await on_event(
            ToolExecutionStart(
                tool_call_id=tool_use.id, name=tool_use.name, params=tool_use.input
            )
        )
        result = AgentToolResult(
            content="响应被截断，工具调用未执行（参数可能不完整）",
            is_error=True,
        )
        await on_event(ToolExecutionEnd(tool_call_id=tool_use.id, result=result))
        blocks.append(
            ToolResultBlock(
                tool_use_id=tool_use.id,
                content=result.content,
                is_error=True,
                blocks=None,
            )
        )
    return blocks


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

    try:
        reason = await hooks.run_before_tool_call(tool.name, tool_use.input, tool_use.id)
    except Exception as exc:
        # 钩子故障降级：只拦这一个工具，不中止整轮（会话完整性优先）。
        # 与"钩子主动拒绝"区分标注，便于排查是策略拦截还是钩子坏了。
        logger.exception("before_tool_call 钩子故障: %s", tool.name)
        return AgentToolResult(
            content=f"调用被拦截: 钩子故障 {type(exc).__name__}: {exc}",
            is_error=True,
        )
    if reason is not None:
        if isinstance(reason, ToolCallBlock):
            return AgentToolResult(
                content=f"调用被拦截: {reason.reason}",
                is_error=True,
                terminate=reason.terminate,
            )
        return AgentToolResult(content=f"调用被拦截: {reason}", is_error=True)

    async def on_update(delta: str) -> None:
        await on_event(ToolExecutionUpdate(tool_call_id=tool_use.id, delta=delta))

    try:
        result = await tool.execute(tool_use.id, tool_use.input, signal, on_update)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # 工具异常不拖垮 loop，以错误结果回填（带类型名——空消息异常可辨）
        return AgentToolResult(content=f"工具执行异常: {type(exc).__name__}: {exc}", is_error=True)
    try:
        return await hooks.run_after_tool_call(tool_use.id, result)
    except Exception as exc:
        # 工具已执行、副作用已生效——保留真实输出，只追加故障标注。
        # 丢弃输出会让 LLM 以为工具没跑，可能重试一个已经生效的写操作。
        logger.exception("after_tool_call 钩子故障: %s", tool.name)
        return AgentToolResult(
            content=f"{result.content}\n\n[后处理钩子故障 {type(exc).__name__}: {exc}]",
            is_error=result.is_error,
            details=result.details,
            blocks=result.blocks,
        )
