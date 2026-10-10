"""Seam A2：AnthropicProvider——SDK 流事件映射与错误映射（SDK client 以假身注入）。"""

from types import SimpleNamespace

import anthropic
import pytest

from super_agent.ai.provider import (
    DEFAULT_MAX_TOKENS,
    ProviderAuthError,
    ProviderError,
    ProviderRateLimitError,
)
from super_agent.ai.providers.anthropic import AnthropicProvider
from super_agent.ai.types import (
    Message,
    MessageEnd,
    MessageStart,
    TextBlock,
    TextDelta,
    ThinkingBlock,
    ThinkingDelta,
    ToolDef,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
)


class FakeSdkStream:
    """duck-type SDK 的 messages.stream() 上下文对象。"""

    def __init__(self, events, final):
        self._events = list(events)
        self._final = final

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return None

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._events:
            raise StopAsyncIteration
        return self._events.pop(0)

    def get_final_message(self):
        return self._final


class FakeSdkClient:
    """duck-type anthropic.AsyncAnthropic——只实现 provider 用到的 messages.stream。"""

    def __init__(self, stream=None, error: Exception | None = None):
        self._stream = stream
        self._error = error
        self.captured: dict = {}

    @property
    def messages(self):
        outer = self

        class _Messages:
            def stream(self, **kwargs):
                outer.captured = kwargs
                if outer._error is not None:
                    raise outer._error
                return outer._stream

        return _Messages()


def _sdk_events():
    return [
        SimpleNamespace(
            type="message_start",
            message=SimpleNamespace(
                model="claude-opus-5",
                usage=SimpleNamespace(
                    input_tokens=12, output_tokens=0, cache_read_input_tokens=0,
                    cache_creation_input_tokens=0,
                ),
            ),
        ),
        SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="thinking_delta", thinking="想")),
        SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text="你")),
        SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text="好")),
        SimpleNamespace(type="content_block_stop"),  # 未识别事件应被丢弃
    ]


def _sdk_final():
    return SimpleNamespace(
        role="assistant",
        model="claude-opus-5",
        stop_reason="end_turn",
        usage=SimpleNamespace(
            input_tokens=12, output_tokens=34, cache_read_input_tokens=5,
            cache_creation_input_tokens=2,
        ),
        content=[
            SimpleNamespace(type="thinking", thinking="想一下", signature="sig-1"),
            SimpleNamespace(type="text", text="你好！"),
        ],
    )


def _collect(provider: AnthropicProvider, system: str | None = None):
    async def run():
        return [
            ev
            async for ev in provider.stream(
                [Message(role="user", content=[TextBlock("hi")])],
                "claude-opus-5",
                system=system,
            )
        ]

    return run()


async def test_stream_maps_sdk_events() -> None:
    client = FakeSdkClient(stream=FakeSdkStream(_sdk_events(), _sdk_final()))
    events = await _collect(AnthropicProvider(client=client))

    assert [type(e) for e in events] == [
        MessageStart,
        ThinkingDelta,
        TextDelta,
        TextDelta,
        MessageEnd,
    ]
    assert events[0].model == "claude-opus-5"
    assert events[1].delta == "想"
    assert events[2].delta == "你"
    assert events[-1].stop_reason == "end_turn"
    assert events[-1].usage == Usage(input_tokens=12, output_tokens=34, cache_read_tokens=5, cache_write_tokens=2)
    # 最终消息：thinking 签名保留（重放与落盘的凭据）
    assert events[-1].message.content == [
        ThinkingBlock(text="想一下", signature="sig-1"),
        TextBlock(text="你好！"),
    ]


async def test_stream_request_shape() -> None:
    client = FakeSdkClient(stream=FakeSdkStream([], _sdk_final()))
    await _collect(AnthropicProvider(client=client), system="sys-prompt")

    kwargs = client.captured
    assert kwargs["model"] == "claude-opus-5"
    assert kwargs["max_tokens"] == DEFAULT_MAX_TOKENS
    assert kwargs["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert kwargs["output_config"] == {"effort": "high"}
    # 票 16：system 转为带缓存断点的块列表（字符串形式无法携带 cache_control）
    assert kwargs["system"] == [
        {"type": "text", "text": "sys-prompt", "cache_control": {"type": "ephemeral"}}
    ]
    assert kwargs["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}}]}
    ]


async def test_request_marks_history_breakpoint_on_last_message() -> None:
    """票 16：最后一条消息末块打历史断点——本轮输入纳入缓存前缀，下一轮起全部命中。

    断点位置选最后一条消息（而非倒数第二条）：本轮 input 立即写入缓存；
    且 loop 中间态与重放场景下最后一条恒为 user/toolResult，天然可承载断点。
    """
    client = FakeSdkClient(stream=FakeSdkStream([], _sdk_final()))
    messages = [
        Message(role="user", content=[TextBlock("第一问")]),
        Message(role="assistant", content=[ThinkingBlock(text="想", signature="sig"), TextBlock("第一答")]),
        Message(role="user", content=[TextBlock("第二问")]),
    ]

    async def run():
        return [ev async for ev in AnthropicProvider(client=client).stream(messages, "claude-opus-5")]

    await run()
    msgs = client.captured["messages"]
    # 早期消息不重复打点（已是稳定前缀，由更深的断点覆盖）
    assert msgs[0]["content"] == [{"type": "text", "text": "第一问"}]
    # 最后一条消息末块带断点
    assert msgs[-1]["content"] == [
        {"type": "text", "text": "第二问", "cache_control": {"type": "ephemeral"}}
    ]


async def test_request_marks_cache_breakpoints_on_system_and_tools() -> None:
    """票 16：稳定前缀打缓存断点——system 与最后一个工具跨轮不变，第二轮起命中 prompt cache。"""
    client = FakeSdkClient(stream=FakeSdkStream([], _sdk_final()))
    tools = [
        ToolDef(name="read", description="读文件", parameters={"type": "object", "properties": {}}),
        ToolDef(name="bash", description="跑命令", parameters={"type": "object", "properties": {}}),
    ]

    async def run():
        return [
            ev
            async for ev in AnthropicProvider(client=client).stream(
                [Message(role="user", content=[TextBlock("hi")])],
                "claude-opus-5",
                system="sys-prompt",
                tools=tools,
            )
        ]

    await run()
    kwargs = client.captured
    assert kwargs["system"] == [
        {"type": "text", "text": "sys-prompt", "cache_control": {"type": "ephemeral"}}
    ]
    # 断点只打在最后一个工具上（前缀缓存语义：前面的工具全部落在其覆盖范围内）
    assert kwargs["tools"][0] == {
        "name": "read",
        "description": "读文件",
        "input_schema": {"type": "object", "properties": {}},
    }
    assert kwargs["tools"][-1]["cache_control"] == {"type": "ephemeral"}


def _status_error(cls, status_code: int) -> Exception:
    response = SimpleNamespace(status_code=status_code, headers={}, request=SimpleNamespace())
    return cls(f"boom-{status_code}", response=response, body=None)


async def test_auth_error_maps_to_provider_auth_error() -> None:
    client = FakeSdkClient(error=_status_error(anthropic.AuthenticationError, 401))
    with pytest.raises(ProviderAuthError, match="API key"):
        await _collect(AnthropicProvider(client=client))


async def test_rate_limit_error_maps_to_provider_rate_limit_error(monkeypatch) -> None:
    monkeypatch.setattr("super_agent.ai.provider.RETRY_DELAYS", (0.0, 0.0, 0.0))
    client = FakeSdkClient(error=_status_error(anthropic.RateLimitError, 429))
    with pytest.raises(ProviderRateLimitError):
        await _collect(AnthropicProvider(client=client))


async def test_other_status_error_maps_to_provider_error(monkeypatch) -> None:
    monkeypatch.setattr("super_agent.ai.provider.RETRY_DELAYS", (0.0, 0.0, 0.0))
    client = FakeSdkClient(error=_status_error(anthropic.APIStatusError, 500))
    with pytest.raises(ProviderError, match="500"):
        await _collect(AnthropicProvider(client=client))


# ---- ticket 15：429 / 5xx 指数退避重试 ----


class FlakySdkClient:
    """前 N 次抛可重试错误，之后成功——验证重试路径。"""

    def __init__(self, failures: int, error_factory) -> None:
        self._failures = failures
        self._error_factory = error_factory
        self.calls = 0

    @property
    def messages(self):
        outer = self

        class _Messages:
            def stream(self, **kwargs):
                outer.calls += 1
                if outer.calls <= outer._failures:
                    raise outer._error_factory()
                return FakeSdkStream(_sdk_events(), _sdk_final())

        return _Messages()


async def test_tool_result_blocks_map_to_api_content_list() -> None:
    """票 23：tool_result 富块 → API content 列表（text + image，模型能看图）。"""
    client = FakeSdkClient(stream=FakeSdkStream([], _sdk_final()))
    messages = [
        Message(role="user", content=[TextBlock("q")]),
        Message(role="assistant", content=[ToolUseBlock(id="t1", name="read", input={})]),
        Message(
            role="toolResult",
            content=[
                ToolResultBlock(
                    tool_use_id="t1",
                    content="[图片: chart.png]",
                    blocks=[
                        {"type": "text", "text": "图片 chart.png："},
                        {"type": "image", "media_type": "image/png", "data": "aGk="},
                    ],
                )
            ],
        ),
    ]

    async def run():
        return [ev async for ev in AnthropicProvider(client=client).stream(messages, "claude-opus-5")]

    await run()
    content = client.captured["messages"][-1]["content"][0]
    assert content["type"] == "tool_result"
    assert content["content"] == [
        {"type": "text", "text": "图片 chart.png："},
        # 存储的扁平 image 块 → API 的嵌套 source 形态
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "aGk="}},
    ]


async def test_tool_result_without_blocks_stays_plain_string() -> None:
    """存量兼容：无富块的 tool_result 维持字符串 content（现状零变化）。"""
    client = FakeSdkClient(stream=FakeSdkStream([], _sdk_final()))
    messages = [
        Message(role="user", content=[TextBlock("q")]),
        Message(role="assistant", content=[ToolUseBlock(id="t1", name="read", input={})]),
        Message(
            role="toolResult",
            content=[ToolResultBlock(tool_use_id="t1", content="纯文本结果")],
        ),
    ]

    async def run():
        return [ev async for ev in AnthropicProvider(client=client).stream(messages, "claude-opus-5")]

    await run()
    content = client.captured["messages"][-1]["content"][0]
    assert content["content"] == "纯文本结果"


async def test_retry_on_rate_limit_then_succeed(monkeypatch) -> None:
    monkeypatch.setattr("super_agent.ai.provider.RETRY_DELAYS", (0.0, 0.0, 0.0))
    client = FlakySdkClient(2, lambda: _status_error(anthropic.RateLimitError, 429))
    events = await _collect(AnthropicProvider(client=client))
    assert [type(e) for e in events][-1] is MessageEnd
    assert client.calls == 3


async def test_retry_requests_carry_cache_breakpoints(monkeypatch) -> None:
    """票 16 验收：429 重试的每次请求携带相同缓存断点（断点在重试循环外构建）。"""
    monkeypatch.setattr("super_agent.ai.provider.RETRY_DELAYS", (0.0, 0.0, 0.0))

    class CapturingFlakyClient:
        """第一次 429，之后成功；逐次捕获请求参数。"""

        def __init__(self) -> None:
            self.calls = 0
            self.kwargs_list: list[dict] = []

        @property
        def messages(self):
            outer = self

            class _Messages:
                def stream(self, **kwargs):
                    outer.calls += 1
                    outer.kwargs_list.append(kwargs)
                    if outer.calls == 1:
                        raise _status_error(anthropic.RateLimitError, 429)
                    return FakeSdkStream(_sdk_events(), _sdk_final())

            return _Messages()

    client = CapturingFlakyClient()
    tools = [ToolDef(name="read", description="读", parameters={"type": "object"})]

    async def run():
        return [
            ev
            async for ev in AnthropicProvider(client=client).stream(
                [Message(role="user", content=[TextBlock("hi")])],
                "claude-opus-5",
                system="sys-prompt",
                tools=tools,
            )
        ]

    await run()
    assert client.calls == 2
    for kwargs in client.kwargs_list:  # 每次尝试都带齐三个断点
        assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert kwargs["tools"][-1]["cache_control"] == {"type": "ephemeral"}
        assert kwargs["messages"][-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}


async def test_retry_exhausted_raises(monkeypatch) -> None:
    monkeypatch.setattr("super_agent.ai.provider.RETRY_DELAYS", (0.0, 0.0, 0.0))
    client = FlakySdkClient(99, lambda: _status_error(anthropic.RateLimitError, 429))
    with pytest.raises(ProviderRateLimitError):
        await _collect(AnthropicProvider(client=client))
    assert client.calls == 4  # 首次 + 3 次重试


async def test_no_retry_after_stream_started(monkeypatch) -> None:
    """流已产出事件后失败——不重试（避免重复输出），直接抛错。"""
    monkeypatch.setattr("super_agent.ai.provider.RETRY_DELAYS", (0.0, 0.0, 0.0))

    class MidStreamFailClient:
        def __init__(self) -> None:
            self.calls = 0

        @property
        def messages(self):
            outer = self

            class _Messages:
                def stream(self, **kwargs):
                    outer.calls += 1
                    return _MidFailStream()

            return _Messages()

    class _MidFailStream:
        def __init__(self) -> None:
            self._sent = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return None

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self._sent:
                self._sent = True
                return SimpleNamespace(type="message_start", message=SimpleNamespace(
                    model="m", usage=SimpleNamespace(
                        input_tokens=1, output_tokens=0, cache_read_input_tokens=0, cache_creation_input_tokens=0,
                    ),
                ))
            raise _status_error(anthropic.RateLimitError, 429)

    client = MidStreamFailClient()
    with pytest.raises(ProviderRateLimitError):
        await _collect(AnthropicProvider(client=client))
    assert client.calls == 1


async def test_4xx_never_retries(monkeypatch) -> None:
    monkeypatch.setattr("super_agent.ai.provider.RETRY_DELAYS", (0.0, 0.0, 0.0))
    client = FlakySdkClient(99, lambda: _status_error(anthropic.APIStatusError, 400))
    with pytest.raises(ProviderError, match="400"):
        await _collect(AnthropicProvider(client=client))
    assert client.calls == 1
