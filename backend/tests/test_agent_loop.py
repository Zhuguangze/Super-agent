"""Seam A3：agent_loop——多 Turn、工具执行、Steer 消费、Abort 与事件全集。"""

import asyncio
import threading

import pytest

from super_agent.agent.hooks import Hooks, ToolCallBlock
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


async def test_before_hook_failure_degrades_and_run_continues() -> None:
    """钩子抛异常 → 降级为该工具的错误结果，整轮不中止（会话完整性优先）。"""
    provider = ScriptedLoopProvider(
        [
            _tool_turn([ToolUseBlock(id="t1", name="echo", input={"text": "hi"})]),
            _text_turn("done"),
        ]
    )
    tool = EchoTool()
    hooks = Hooks()

    async def broken(name, params, call_id):
        raise RuntimeError("钩子内部炸了")

    hooks.on_before_tool_call(broken)

    events, end = await _collect(
        lambda on_event: agent_loop(
            provider, "claude-opus-5", [tool],
            [Message(role="user", content=[TextBlock("q")])], on_event, hooks=hooks,
        )
    )

    tool_end = next(e for e in events if isinstance(e, ToolExecutionEnd))
    assert tool_end.result.is_error is True
    assert "钩子故障" in tool_end.result.content
    assert "RuntimeError" in tool_end.result.content
    assert tool.calls == []  # 钩子故障时工具不执行
    # 关键：run 没有中止——第二个 Turn 正常跑完并发出 agent_end
    assert end.stop_reason == "end_turn"
    assert any(isinstance(e, AgentEnd) for e in events)


async def test_after_hook_failure_preserves_tool_output() -> None:
    """工具已执行、副作用已生效——后处理钩子故障必须保留真实输出。"""
    provider = ScriptedLoopProvider(
        [
            _tool_turn([ToolUseBlock(id="t1", name="echo", input={"text": "hi"})]),
            _text_turn("done"),
        ]
    )
    tool = EchoTool()
    hooks = Hooks()

    async def broken(call_id, result):
        raise RuntimeError("后处理炸了")

    hooks.on_after_tool_call(broken)

    events, end = await _collect(
        lambda on_event: agent_loop(
            provider, "claude-opus-5", [tool],
            [Message(role="user", content=[TextBlock("q")])], on_event, hooks=hooks,
        )
    )

    tool_end = next(e for e in events if isinstance(e, ToolExecutionEnd))
    assert "echo: hi" in tool_end.result.content  # 工具真实输出保留
    assert "后处理钩子故障" in tool_end.result.content  # 故障有标注
    assert tool_end.result.is_error is False  # 工具本身没失败
    assert tool.calls != []  # 工具确实跑了
    assert end.stop_reason == "end_turn"


async def test_event_channel_failure_degrades_without_aborting() -> None:
    """run_one 兜底：事件发射故障也不中止整轮，且回填结果不留悬空 tool_use。"""
    provider = ScriptedLoopProvider(
        [
            _tool_turn([ToolUseBlock(id="t1", name="echo", input={"text": "hi"})]),
            _text_turn("done"),
        ]
    )
    tool = EchoTool()
    events: list = []

    async def on_event(ev) -> None:
        if isinstance(ev, ToolExecutionStart):
            raise RuntimeError("事件通道故障")
        events.append(ev)

    end = await agent_loop(
        provider, "claude-opus-5", [tool],
        [Message(role="user", content=[TextBlock("q")])], on_event,
    )

    appended = next(e for e in events if isinstance(e, MessagesAppended))
    block = appended.messages[0].content[0]
    assert block.is_error is True
    assert "工具管道故障" in block.content
    assert end.stop_reason == "end_turn"


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


async def test_finish_turn_end_forces_stop() -> None:
    """finish_turn 钩子返回 "end" → 强制结束，不再跑后续轮次。"""
    provider = ScriptedLoopProvider(
        [
            _tool_turn([ToolUseBlock(id="t1", name="echo", input={"text": "hi"})]),
            _text_turn("不该跑的轮次"),
        ]
    )
    tool = EchoTool()
    hooks = Hooks()

    async def force_end(turn: int) -> str | None:
        return "end"

    hooks.on_finish_turn(force_end)

    events, end = await _collect(
        lambda on_event: agent_loop(
            provider, "claude-opus-5", [tool],
            [Message(role="user", content=[TextBlock("q")])], on_event, hooks=hooks,
        )
    )
    assert end.stop_reason == "finish_turn_end"
    assert [e.type for e in events].count("turn_start") == 1
    assert tool.calls != []  # 第一轮工具确实执行了，end 是之后生效


async def test_finish_turn_continue_extends_loop() -> None:
    """finish_turn 钩子返回 "continue" → 即使下一轮 end_turn，也强制再跑一轮。"""
    provider = ScriptedLoopProvider(
        [
            _tool_turn([ToolUseBlock(id="t1", name="echo", input={"text": "hi"})]),
            _text_turn("第二轮"),
            _text_turn("continue 后的第三轮"),
        ]
    )
    tool = EchoTool()
    hooks = Hooks()

    async def force_continue(turn: int) -> str | None:
        return "continue" if turn == 1 else None

    hooks.on_finish_turn(force_continue)

    events, end = await _collect(
        lambda on_event: agent_loop(
            provider, "claude-opus-5", [tool],
            [Message(role="user", content=[TextBlock("q")])], on_event, hooks=hooks,
        )
    )
    assert [e.type for e in events].count("turn_start") == 3
    assert end.stop_reason == "end_turn"


class FollowUpProvider(ScriptedLoopProvider):
    """在第 after_turn 轮 stream 时往队列放 follow-up，模拟 run 中途/结束时到达的消息。"""

    def __init__(self, turns, queue, followup_text, after_turn):
        super().__init__(turns)
        self.queue = queue
        self.followup_text = followup_text
        self.after_turn = after_turn
        self.stream_count = 0

    async def stream(self, messages, model_id, *, system=None, effort="high", tools=None):
        self.stream_count += 1
        self.contexts.append([m for m in messages])
        if self.stream_count == self.after_turn:
            self.queue.put_nowait(self.followup_text)
        for ev in self._turns.pop(0):
            yield ev


async def test_follow_up_continues_loop_after_end_turn() -> None:
    """run 本应结束（end_turn）后，loop 自己 drain follow-up 并续跑一轮。"""
    queue: asyncio.Queue[str] = asyncio.Queue()
    provider = FollowUpProvider(
        [
            _tool_turn([ToolUseBlock(id="t1", name="echo", input={"text": "hi"})]),
            _text_turn("第一段"),
            _text_turn("回复 follow-up"),
        ],
        queue,
        "follow-up 问题",
        after_turn=2,  # 第 2 轮（end_turn）stream 时到达，成为 follow-up 而非 steer
    )
    tool = EchoTool()

    events, end = await _collect(
        lambda on_event: agent_loop(
            provider, "claude-opus-5", [tool],
            [Message(role="user", content=[TextBlock("q")])], on_event,
            steer_queue=queue,
        )
    )
    # follow-up 触发第 3 轮，且第 3 轮的上下文里包含 follow-up 消息
    assert [e.type for e in events].count("turn_start") == 3
    assert any("follow-up 问题" in m.text() for m in provider.contexts[2])
    assert end.stop_reason == "end_turn"


async def test_one_at_a_time_drains_single_steer_per_turn() -> None:
    """one-at-a-time 排空（默认）：工具后每轮只注入一条 steer，剩余留到下一轮。"""
    provider = ScriptedLoopProvider(
        [
            _tool_turn([ToolUseBlock(id="t1", name="echo", input={"text": "1"})]),
            _tool_turn([ToolUseBlock(id="t2", name="echo", input={"text": "2"})]),
            _text_turn("done"),
        ]
    )
    queue: asyncio.Queue[str] = asyncio.Queue()
    queue.put_nowait("steer-a")
    queue.put_nowait("steer-b")

    await _collect(
        lambda on_event: agent_loop(
            provider, "claude-opus-5", [EchoTool()],
            [Message(role="user", content=[TextBlock("q")])], on_event,
            steer_queue=queue,
        )
    )

    # 第 2 轮上下文只含 steer-a（one-at-a-time），第 3 轮才含 steer-b
    second_texts = [m.text() for m in provider.contexts[1] if m.role == "user"]
    third_texts = [m.text() for m in provider.contexts[2] if m.role == "user"]
    assert second_texts == ["q", "steer-a"]
    assert third_texts == ["q", "steer-a", "steer-b"]


async def test_all_mode_drains_all_steer_at_once() -> None:
    """all 排空模式：工具后一次性注入全部 steer。"""
    provider = ScriptedLoopProvider(
        [
            _tool_turn([ToolUseBlock(id="t1", name="echo", input={"text": "1"})]),
            _tool_turn([ToolUseBlock(id="t2", name="echo", input={"text": "2"})]),
            _text_turn("done"),
        ]
    )
    queue: asyncio.Queue[str] = asyncio.Queue()
    queue.put_nowait("steer-a")
    queue.put_nowait("steer-b")

    await _collect(
        lambda on_event: agent_loop(
            provider, "claude-opus-5", [EchoTool()],
            [Message(role="user", content=[TextBlock("q")])], on_event,
            steer_queue=queue, queue_mode="all",
        )
    )

    # all 模式：第 2 轮上下文一次性含两条 steer
    second_texts = [m.text() for m in provider.contexts[1] if m.role == "user"]
    assert second_texts == ["q", "steer-a", "steer-b"]


async def test_truncated_response_fails_tool_calls_without_executing() -> None:
    """stop_reason 为 max_tokens（截断）时，该消息的工具调用全部标失败、不执行。"""
    provider = ScriptedLoopProvider(
        [
            _turn(
                [TextDelta("截断的输出")],
                "max_tokens",
                Usage(input_tokens=5, output_tokens=4),
                [TextBlock("截断的输出"), ToolUseBlock(id="t1", name="echo", input={"text": "hi"})],
            ),
            _text_turn("重发后的回复"),
        ]
    )
    tool = EchoTool()

    events, end = await _collect(
        lambda on_event: agent_loop(
            provider, "claude-opus-5", [tool],
            [Message(role="user", content=[TextBlock("q")])], on_event,
        )
    )
    # 截断的工具调用不执行，收到失败结果，模型重发后正常结束
    assert tool.calls == []
    tool_end = next(e for e in events if isinstance(e, ToolExecutionEnd))
    assert tool_end.result.is_error is True
    assert end.stop_reason == "end_turn"


async def test_terminate_all_blocked_batch_stops_loop() -> None:
    """整批所有工具都被拒且都设 terminate → 批次终止，不再跑下一轮。"""
    provider = ScriptedLoopProvider(
        [
            _tool_turn(
                [
                    ToolUseBlock(id="t1", name="echo", input={"text": "a"}),
                    ToolUseBlock(id="t2", name="echo", input={"text": "b"}),
                ]
            ),
            _text_turn("不该跑的轮次"),
        ]
    )
    tool = EchoTool()
    hooks = Hooks()

    async def block_terminate(name, params, call_id):
        return ToolCallBlock(reason="全拒", terminate=True)

    hooks.on_before_tool_call(block_terminate)

    events, end = await _collect(
        lambda on_event: agent_loop(
            provider, "claude-opus-5", [tool],
            [Message(role="user", content=[TextBlock("q")])], on_event, hooks=hooks,
        )
    )
    assert tool.calls == []
    assert [e.type for e in events].count("turn_start") == 1
    assert end.stop_reason == "end_turn"


async def test_single_terminate_does_not_stop_batch() -> None:
    """只有一个工具 terminate（非全票）→ 批次继续，跑下一轮。"""
    provider = ScriptedLoopProvider(
        [
            _tool_turn(
                [
                    ToolUseBlock(id="t1", name="echo", input={"text": "a"}),
                    ToolUseBlock(id="t2", name="echo", input={"text": "b"}),
                ]
            ),
            _text_turn("继续"),
        ]
    )
    tool = EchoTool()
    hooks = Hooks()

    async def block_one(name, params, call_id):
        if params.get("text") == "a":
            return ToolCallBlock(reason="拒 a", terminate=True)
        return None

    hooks.on_before_tool_call(block_one)

    events, end = await _collect(
        lambda on_event: agent_loop(
            provider, "claude-opus-5", [tool],
            [Message(role="user", content=[TextBlock("q")])], on_event, hooks=hooks,
        )
    )
    # a 被拒（terminate），b 执行；非全票 → 不终止，继续第二轮
    assert any(c[1].get("text") == "b" for c in tool.calls)
    assert [e.type for e in events].count("turn_start") == 2
    assert end.stop_reason == "end_turn"
