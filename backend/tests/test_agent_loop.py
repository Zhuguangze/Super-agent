"""Seam A3：agent_loop——多 Turn、工具执行、Steer 消费、Abort 与事件全集。"""

import asyncio
import threading

import pytest

from super_agent.agent.hooks import Hooks
from super_agent.agent.loop import agent_loop
from super_agent.agent.types import (
    AgentEnd,
    AgentStart,
    AgentToolResult,
    MessagesAppended,
    ToolExecutionEnd,
    ToolExecutionStart,
    ToolExecutionUpdate,
    TurnEnd,
    TurnStart,
)
from super_agent.ai.provider import Provider
from super_agent.ai.types import (
    Message,
    MessageEnd,
    MessageStart,
    TextBlock,
    TextDelta,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
)


class EchoTool:
    """假工具：原样返回输入（票 05 的协议验证工具）。"""

    name = "echo"
    description = "原样返回输入"
    parameters = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }
    execution_mode = "parallel"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, tool_call_id, params, signal, on_update):
        self.calls.append((tool_call_id, params))
        if on_update is not None:
            await on_update("处理中…")
        return AgentToolResult(content=f"echo: {params.get('text', '')}")


class SequentialTool(EchoTool):
    name = "seq"
    execution_mode = "sequential"


class ScriptedLoopProvider(Provider):
    """每轮返回一组预置 StreamEvent；记录每轮收到的上下文。"""

    id = "scripted-loop"

    def __init__(self, turns: list[list]):
        self._turns = list(turns)
        self.contexts: list[list[Message]] = []

    async def stream(self, messages, model_id, *, system=None, effort="high", tools=None):
        self.contexts.append([m for m in messages])
        for ev in self._turns.pop(0):
            yield ev


def _turn(events, stop_reason, usage, content) -> list:
    return [
        MessageStart(model="claude-opus-5", usage=Usage(input_tokens=usage.input_tokens)),
        *events,
        MessageEnd(stop_reason=stop_reason, usage=usage, message=Message(role="assistant", content=content)),
    ]


def _tool_turn(tool_uses: list[ToolUseBlock], text: str = "我调用工具") -> list:
    return _turn(
        [TextDelta(text)],
        "tool_use",
        Usage(input_tokens=5, output_tokens=4),
        [TextBlock(text), *tool_uses],
    )


def _text_turn(text: str, input_tokens: int = 9) -> list:
    return _turn(
        [TextDelta(text)],
        "end_turn",
        Usage(input_tokens=input_tokens, output_tokens=6),
        [TextBlock(text)],
    )


async def _collect(loop_coro):
    events: list = []

    async def on_event(ev) -> None:
        events.append(ev)

    result = await loop_coro(on_event)
    return events, result


async def test_multi_turn_loop_runs_tools_until_done() -> None:
    provider = ScriptedLoopProvider(
        [
            _tool_turn([ToolUseBlock(id="t1", name="echo", input={"text": "hi"})]),
            _text_turn("答案是 hi"),
        ]
    )
    tool = EchoTool()
    events, end = await _collect(
        lambda on_event: agent_loop(
            provider, "claude-opus-5", [tool],
            [Message(role="user", content=[TextBlock("q")])], on_event,
        )
    )

    types = [e.type for e in events]
    assert types == [
        "agent_start",
        "turn_start",
        "message_start",
        "text_delta",
        "message_end",
        "tool_execution_start",
        "tool_execution_update",
        "tool_execution_end",
        "messages_appended",
        "turn_end",
        "turn_start",
        "message_start",
        "text_delta",
        "message_end",
        "turn_end",
        "agent_end",
    ]
    # 工具相位事件字段
    start = events[5]
    assert isinstance(start, ToolExecutionStart)
    assert (start.tool_call_id, start.name, start.params) == ("t1", "echo", {"text": "hi"})
    tool_end = events[7]
    assert isinstance(tool_end, ToolExecutionEnd)
    assert tool_end.result.content == "echo: hi"
    assert tool_end.result.is_error is False
    # 结果回填进上下文
    appended = events[8]
    assert isinstance(appended, MessagesAppended)
    assert appended.messages[0].role == "toolResult"
    assert appended.messages[0].content == [ToolResultBlock(tool_use_id="t1", content="echo: hi")]
    # 第二轮上下文 = user + assistant(tool_use) + toolResult
    second_turn_ctx = provider.contexts[1]
    assert [m.role for m in second_turn_ctx] == ["user", "assistant", "toolResult"]
    # usage 跨轮累计
    assert isinstance(end, AgentEnd)
    assert end.stop_reason == "end_turn"
    assert end.usage == Usage(input_tokens=14, output_tokens=10)
    assert end.message is not None and end.message.text() == "答案是 hi"


async def test_tool_result_blocks_flow_through_loop() -> None:
    """票 23：AgentToolResult.blocks → ToolResultBlock 透传（富结果链路起点）。"""
    provider = ScriptedLoopProvider(
        [
            _tool_turn([ToolUseBlock(id="t1", name="img", input={})]),
            _text_turn("看到图了"),
        ]
    )

    class ImageTool(EchoTool):
        name = "img"
        parameters = {"type": "object", "properties": {}}

        async def execute(self, tool_call_id, params, signal, on_update):
            return AgentToolResult(
                content="[图片: chart.png]",
                blocks=[{"type": "image", "media_type": "image/png", "data": "aGk="}],
            )

    events, _end = await _collect(
        lambda on_event: agent_loop(
            provider, "claude-opus-5", [ImageTool()],
            [Message(role="user", content=[TextBlock("q")])], on_event,
        )
    )
    appended = next(e for e in events if e.type == "messages_appended")
    block = appended.messages[0].content[0]
    assert block.content == "[图片: chart.png]"
    assert block.blocks == [{"type": "image", "media_type": "image/png", "data": "aGk="}]


async def test_tool_exception_carries_exception_type() -> None:
    """工具异常回填带异常类型名——空消息异常（如 NotImplementedError()）可辨。"""
    provider = ScriptedLoopProvider(
        [
            _tool_turn([ToolUseBlock(id="t1", name="boom", input={})]),
            _text_turn("收到错误"),
        ]
    )

    class BoomTool(EchoTool):
        name = "boom"
        parameters = {"type": "object", "properties": {}}

        async def execute(self, tool_call_id, params, signal, on_update):
            raise NotImplementedError()  # str() 为空——正是线上"工具执行异常:"（空）的形态

    events, _end = await _collect(
        lambda on_event: agent_loop(
            provider, "claude-opus-5", [BoomTool()],
            [Message(role="user", content=[TextBlock("q")])], on_event,
        )
    )
    tool_end = next(e for e in events if e.type == "tool_execution_end")
    assert tool_end.result.is_error is True
    assert "NotImplementedError" in tool_end.result.content


async def test_unknown_tool_and_bad_params_yield_error_results() -> None:
    provider = ScriptedLoopProvider(
        [
            _tool_turn(
                [
                    ToolUseBlock(id="t1", name="nope", input={}),
                    ToolUseBlock(id="t2", name="echo", input={}),  # 缺 required text
                ]
            ),
            _text_turn("ok"),
        ]
    )
    tool = EchoTool()
    events, _ = await _collect(
        lambda on_event: agent_loop(
            provider, "claude-opus-5", [tool],
            [Message(role="user", content=[TextBlock("q")])], on_event,
        )
    )
    ends = [e for e in events if isinstance(e, ToolExecutionEnd)]
    assert [(e.result.is_error, "未知工具" in e.result.content) for e in ends][0] == (True, True)
    assert [(e.result.is_error, "参数校验失败" in e.result.content) for e in ends][1] == (True, True)
    # 错误结果照样回填（toolResult is_error 传给 API）
    appended = next(e for e in events if isinstance(e, MessagesAppended))
    assert [b.is_error for b in appended.messages[0].content] == [True, True]
    assert tool.calls == []  # 校验失败的调用不会执行


async def test_hooks_can_intercept_and_replace() -> None:
    provider = ScriptedLoopProvider(
        [
            _tool_turn([ToolUseBlock(id="t1", name="echo", input={"text": "hi"})]),
            _text_turn("done"),
        ]
    )
    tool = EchoTool()
    hooks = Hooks()

    async def deny_bash(name, params, call_id):
        return "涉及危险操作" if name == "echo" else None

    async def replace(call_id, result):
        return AgentToolResult(content=f"[替换] {result.content}")

    hooks.on_before_tool_call(deny_bash)
    hooks.on_after_tool_call(replace)

    events, _ = await _collect(
        lambda on_event: agent_loop(
            provider, "claude-opus-5", [tool],
            [Message(role="user", content=[TextBlock("q")])], on_event, hooks=hooks,
        )
    )
    tool_end = next(e for e in events if isinstance(e, ToolExecutionEnd))
    assert tool_end.result.is_error is True
    assert "被拦截: 涉及危险操作" in tool_end.result.content
    assert tool.calls == []


async def test_steer_consumed_after_tool_execution() -> None:
    provider = ScriptedLoopProvider(
        [
            _tool_turn([ToolUseBlock(id="t1", name="echo", input={"text": "hi"})]),
            _text_turn("done"),
        ]
    )
    queue: asyncio.Queue[str] = asyncio.Queue()
    queue.put_nowait("等等，先别急")

    async def run(on_event):
        return await agent_loop(
            provider, "claude-opus-5", [EchoTool()],
            [Message(role="user", content=[TextBlock("q")])], on_event,
            steer_queue=queue,
        )

    await _collect(run)

    second_ctx = provider.contexts[1]
    assert second_ctx[-1].role == "user"
    assert second_ctx[-1].text() == "等等，先别急"


async def test_abort_cancels_and_skips_agent_end() -> None:
    gate = threading.Event()

    class GatedProvider(Provider):
        id = "gated"

        async def stream(self, messages, model_id, *, system=None, effort="high", tools=None):
            yield MessageStart(model="m", usage=Usage())
            while not gate.is_set():
                await asyncio.sleep(0.01)
            yield MessageEnd(stop_reason="end_turn", usage=Usage(), message=Message(role="assistant", content=[TextBlock("late")]))

    events: list = []

    async def on_event(ev) -> None:
        events.append(ev)

    task = asyncio.create_task(
        agent_loop(
            GatedProvider(), "m", [],
            [Message(role="user", content=[TextBlock("q")])], on_event,
        )
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # abort 不发 agent_end（app 层按 aborted 收尾）
    assert all(e.type != "agent_end" for e in events)


async def test_max_turns_stops_the_loop() -> None:
    provider = ScriptedLoopProvider(
        [
            _tool_turn([ToolUseBlock(id="t1", name="echo", input={"text": "a"})]),
            _tool_turn([ToolUseBlock(id="t2", name="echo", input={"text": "b"})], text="再调一次"),
        ]
    )
    events, end = await _collect(
        lambda on_event: agent_loop(
            provider, "claude-opus-5", [EchoTool()],
            [Message(role="user", content=[TextBlock("q")])], on_event,
            max_turns=2,
        )
    )
    assert end.stop_reason == "max_turns"
    assert [e.type for e in events].count("turn_start") == 2


async def test_sequential_tool_forces_serial_execution() -> None:
    order: list[str] = []

    class SlowTool(EchoTool):
        def __init__(self, tag: str, mode: str) -> None:
            self.name = f"slow_{tag}"
            self.execution_mode = mode
            self.tag = tag

        async def execute(self, tool_call_id, params, signal, on_update):
            order.append(f"{self.tag}:start")
            await asyncio.sleep(0.02)
            order.append(f"{self.tag}:end")
            return AgentToolResult(content=self.tag)

    provider = ScriptedLoopProvider(
        [
            _tool_turn(
                [
                    ToolUseBlock(id="t1", name="slow_a", input={"text": "x"}),
                    ToolUseBlock(id="t2", name="slow_b", input={"text": "y"}),
                ]
            ),
            _text_turn("done"),
        ]
    )

    async def run(on_event):
        return await agent_loop(
            provider, "claude-opus-5", [SlowTool("a", "parallel"), SlowTool("b", "sequential")],
            [Message(role="user", content=[TextBlock("q")])], on_event,
        )

    await _collect(run)
    # 一个 sequential 工具参与 → 整体串行：a 完成后 b 才开始
    assert order == ["a:start", "a:end", "b:start", "b:end"]
