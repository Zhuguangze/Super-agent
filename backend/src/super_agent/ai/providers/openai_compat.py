"""OpenAI 兼容 Provider——chat/completions SSE 流式（自定义模型接入点，spec §4.9 原型决策）。

覆盖 OpenAI Chat Completions 兼容端点（vLLM/Ollama/DeepSeek/OneAPI 等）：
- system 并入 messages；toolResult 拆成逐条 tool 消息（tool_call_id 对齐）
- 工具定义转 function 格式；流式 tool_calls 增量按 index 拼装
- reasoning_content 映射 ThinkingDelta（无签名——会话重放会跳过，不影响继续对话）
- effort 在该协议下无对应概念，忽略（docstring 注明）
- 错误映射与 AnthropicProvider 一致；v1 不做重试（协议实现优先，需要时补 RETRY_DELAYS 逻辑）
"""

import json
from collections.abc import AsyncIterator

import httpx

from super_agent.ai.provider import (
    DEFAULT_EFFORT,
    Effort,
    Provider,
    ProviderAuthError,
    ProviderConnectionError,
    ProviderError,
    ProviderRateLimitError,
    ProviderServerError,
)
from super_agent.ai.types import (
    ContentBlock,
    Message,
    MessageEnd,
    MessageStart,
    StreamEvent,
    TextBlock,
    TextDelta,
    ThinkingDelta,
    ToolDef,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
)

DEFAULT_TIMEOUT = 300.0


class OpenAICompatProvider(Provider):
    id = "openai-compat"

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._client = client or httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, transport=transport)

    def configured(self, model_id: str) -> bool:
        # base_url 创建时必填——本地端点（Ollama 等）可以无 key
        return bool(self._base_url)

    async def stream(
        self,
        messages: list[Message],
        model_id: str,
        *,
        system: str | None = None,
        effort: Effort = DEFAULT_EFFORT,
        tools: list[ToolDef] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        payload: dict = {
            "model": model_id,
            "messages": _to_api_messages(messages, system),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        yield MessageStart(model=model_id, usage=Usage())
        text_parts: list[str] = []
        blocks: list[ContentBlock] = []
        tool_acc: dict[int, dict] = {}
        usage = Usage()
        finish_reason = "stop"
        try:
            async with self._client.stream(
                "POST", f"{self._base_url}/chat/completions", json=payload, headers=headers
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", errors="replace")[:300]
                    raise _map_http_error(response.status_code, body)
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if not data or data == "[DONE]":
                        if data == "[DONE]":
                            break
                        continue
                    chunk = json.loads(data)
                    if chunk.get("usage"):
                        usage = Usage(
                            input_tokens=int(chunk["usage"].get("prompt_tokens") or 0),
                            output_tokens=int(chunk["usage"].get("completion_tokens") or 0),
                        )
                    for choice in chunk.get("choices", []):
                        delta = choice.get("delta") or {}
                        if delta.get("reasoning_content"):
                            yield ThinkingDelta(delta["reasoning_content"])
                        if delta.get("content"):
                            yield TextDelta(delta["content"])
                            text_parts.append(delta["content"])
                        for tc in delta.get("tool_calls") or []:
                            _accumulate_tool_call(tool_acc, tc)
                        if choice.get("finish_reason"):
                            finish_reason = choice["finish_reason"]
        except httpx.HTTPError as exc:
            raise ProviderConnectionError(f"无法连接模型服务（{self._base_url}）：{exc}") from exc

        for acc in tool_acc.values():
            try:
                arguments = json.loads(acc.get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}
            blocks.append(ToolUseBlock(id=acc.get("id") or f"call_{len(blocks)}", name=acc.get("name") or "", input=arguments))
        blocks.append(TextBlock("".join(text_parts)))
        yield MessageEnd(
            stop_reason=_map_finish_reason(finish_reason, len(tool_acc)),
            usage=usage,
            message=Message(role="assistant", content=blocks),
        )


def _accumulate_tool_call(tool_acc: dict[int, dict], tc: dict) -> None:
    index = int(tc.get("index") or 0)
    acc = tool_acc.setdefault(index, {"arguments": ""})
    if tc.get("id"):
        acc["id"] = tc["id"]
    function = tc.get("function") or {}
    if function.get("name"):
        acc["name"] = acc.get("name", "") + function["name"]
    if function.get("arguments"):
        acc["arguments"] += function["arguments"]


def _map_finish_reason(finish: str, tool_count: int) -> str:
    if tool_count or finish == "tool_calls":
        return "tool_use"
    if finish == "length":
        return "max_tokens"
    return "end_turn"


def _to_api_messages(messages: list[Message], system: str | None) -> list[dict]:
    api_messages: list[dict] = []
    if system:
        api_messages.append({"role": "system", "content": system})
    for m in messages:
        if m.role == "toolResult":
            for block in m.content:
                if isinstance(block, ToolResultBlock):
                    api_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": block.tool_use_id,
                            "content": block.content,
                        }
                    )
            continue
        text = m.text()
        if m.role == "assistant":
            tool_calls = [
                {
                    "id": b.id,
                    "type": "function",
                    "function": {"name": b.name, "arguments": json.dumps(b.input, ensure_ascii=False)},
                }
                for b in m.content
                if isinstance(b, ToolUseBlock)
            ]
            entry: dict = {"role": "assistant", "content": text or None}
            if tool_calls:
                entry["tool_calls"] = tool_calls
            api_messages.append(entry)
        else:
            api_messages.append({"role": "user", "content": text})
    return api_messages


def _map_http_error(status_code: int, body: str) -> ProviderError:
    if status_code in (401, 403):
        return ProviderAuthError("模型服务认证失败：API key 无效或缺失")
    if status_code == 429:
        return ProviderRateLimitError("模型服务速率限制，请稍后重试")
    if status_code >= 500:
        return ProviderServerError(f"模型服务暂不可用（HTTP {status_code}）：{body}")
    return ProviderError(f"模型服务错误（HTTP {status_code}）：{body}")
