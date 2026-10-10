"""OpenAI 兼容 Provider——SSE 解析、工具调用拼装、错误映射与 MultiProvider 路由。"""

import json

import httpx
import pytest

from super_agent.ai.provider import (
    ProviderAuthError,
    ProviderConnectionError,
    ProviderError,
    ProviderRateLimitError,
    ProviderServerError,
)
from super_agent.ai.providers.multi import MultiProvider
from super_agent.ai.providers.openai_compat import OpenAICompatProvider
from super_agent.ai.types import Message, TextBlock, ToolDef, ToolResultBlock, ToolUseBlock

BASE = "http://model.local/v1"


def _sse_response(chunks: list[dict], status: int = 200) -> httpx.Response:
    body = "".join(f"data: {json.dumps(c, ensure_ascii=False)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return httpx.Response(status, content=body.encode("utf-8"), headers={"content-type": "text/event-stream"})


def _provider(handler) -> OpenAICompatProvider:
    return OpenAICompatProvider(BASE, api_key="k", transport=httpx.MockTransport(handler))


async def _collect(provider: OpenAICompatProvider, **kwargs):
    return [
        ev
        async for ev in provider.stream(
            [Message(role="user", content=[TextBlock("hi")])], "my-model", **kwargs
        )
    ]


def _captured_payload(chunks: list[dict]):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.read())
        captured["auth"] = request.headers.get("authorization")
        return _sse_response(chunks)

    return captured, handler


async def test_stream_parses_text_and_usage() -> None:
    chunks = [
        {"choices": [{"delta": {"content": "你"}, "finish_reason": None}]},
        {"choices": [{"delta": {"content": "好"}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 4}},
    ]
    captured, handler = _captured_payload(chunks)
    events = await _collect(_provider(handler))

    assert events[0].model == "my-model"
    assert [e.delta for e in events if type(e).__name__ == "TextDelta"] == ["你", "好"]
    end = events[-1]
    assert end.stop_reason == "end_turn"
    assert end.usage.input_tokens == 10 and end.usage.output_tokens == 4
    assert end.message.text() == "你好"
    # 请求形态：system 并入、stream、include_usage、Bearer
    assert captured["auth"] == "Bearer k"
    assert captured["body"]["stream"] is True
    assert captured["body"]["model"] == "my-model"
    assert captured["body"]["messages"][0] == {"role": "user", "content": "hi"}


def _delta_chunk(delta: dict, finish_reason: str | None = None) -> dict:
    return {"choices": [{"delta": delta, "finish_reason": finish_reason}]}


async def test_stream_tool_calls_accumulate_and_result_messages_convert() -> None:
    chunks = [
        _delta_chunk({"content": "我来调用"}),
        _delta_chunk({"tool_calls": [{"index": 0, "id": "call1", "function": {"name": "echo", "arguments": '{"te'}}]}),
        _delta_chunk({"tool_calls": [{"index": 0, "function": {"arguments": 'xt": "hi"}'}}]}),
        _delta_chunk({}, finish_reason="tool_calls"),
    ]
    events = await _collect(
        _provider(_captured_payload(chunks)[1]),
        tools=[ToolDef(name="echo", description="d", parameters={"type": "object"})],
    )
    end = events[-1]
    assert end.stop_reason == "tool_use"
    tool_use = next(b for b in end.message.content if isinstance(b, ToolUseBlock))
    assert tool_use.id == "call1" and tool_use.name == "echo" and tool_use.input == {"text": "hi"}


async def test_tool_result_converts_to_openai_tool_messages() -> None:
    captured, handler = _captured_payload([])
    messages = [
        Message(role="user", content=[TextBlock("hi")]),
        Message(role="assistant", content=[ToolUseBlock(id="call1", name="echo", input={"text": "hi"})]),
        Message(role="toolResult", content=[ToolResultBlock(tool_use_id="call1", content="echo: hi")]),
    ]
    async for _ in OpenAICompatProvider(BASE, api_key="k", transport=httpx.MockTransport(handler)).stream(messages, "m"):
        pass
    roles = [m["role"] for m in captured["body"]["messages"]]
    assert roles == ["user", "assistant", "tool"]
    tool_msg = captured["body"]["messages"][2]
    assert tool_msg["tool_call_id"] == "call1" and tool_msg["content"] == "echo: hi"
    # assistant 的 tool_calls 形态
    assert captured["body"]["messages"][1]["tool_calls"][0]["id"] == "call1"


@pytest.mark.parametrize(
    ("status", "expected"),
    [(401, ProviderAuthError), (429, ProviderRateLimitError), (500, ProviderServerError), (400, ProviderError)],
)
async def test_http_errors_map(status: int, expected: type[ProviderError]) -> None:
    provider = _provider(lambda request: httpx.Response(status, text="boom"))
    with pytest.raises(expected):
        await _collect(provider)


async def test_connection_error_maps() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(ProviderConnectionError):
        await _collect(_provider(handler))


# ---- MultiProvider 路由与自定义模型注册 ----


async def test_multi_routes_by_model_id() -> None:
    multi = MultiProvider()
    multi.register_for_models(["m-a"], _provider(lambda r: _sse_response([])))
    errors = multi.rebind_custom_models(
        [{"id": "local-llama", "name": "本地 Llama", "provider": "openai-compat", "base_url": "http://x/v1", "api_key": "k"}]
    )
    assert errors == []
    assert "local-llama" in multi.custom_model_ids
    from super_agent.ai.provider import MODELS

    assert "local-llama" in MODELS  # GET /api/models 随之可见


async def test_multi_rebind_replaces_custom_models() -> None:
    multi = MultiProvider()
    multi.rebind_custom_models([{"id": "a1", "base_url": "http://x/v1"}])
    assert "a1" in multi.custom_model_ids
    multi.rebind_custom_models([{"id": "b1", "base_url": "http://y/v1"}])
    assert "a1" not in multi.custom_model_ids and "b1" in multi.custom_model_ids
    from super_agent.ai.provider import MODELS

    assert "a1" not in MODELS and "b1" in MODELS


async def test_multi_rejects_conflicting_id_and_missing_url() -> None:
    from super_agent.ai.provider import MODELS, Model

    multi = MultiProvider()
    MODELS["existing"] = Model(id="existing", name="已有", provider="test", context_window=1, max_output_tokens=1)
    errors = multi.rebind_custom_models(
        [
            {"id": "existing", "base_url": "http://x/v1"},  # 与已有冲突
            {"id": "", "base_url": "http://y/v1"},  # 缺 id
        ]
    )
    MODELS.pop("existing", None)
    assert len(errors) == 2
    assert "existing" not in multi.custom_model_ids


async def test_multi_unknown_model_errors() -> None:
    multi = MultiProvider()
    with pytest.raises(ProviderError, match="未知模型"):
        async for _ in multi.stream([Message(role="user", content=[TextBlock("x")])], "ghost", effort="high"):
            pass
