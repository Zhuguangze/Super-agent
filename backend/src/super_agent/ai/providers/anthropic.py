"""Anthropic Provider——v1 唯一实现：官方 SDK，流式 + adaptive thinking。"""

import asyncio
import inspect
import os
import random
from collections.abc import AsyncIterator

import anthropic

from super_agent.ai.provider import (
    DEFAULT_EFFORT,
    DEFAULT_MAX_TOKENS,
    RETRY_DELAYS,
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
    ImageBlock,
    Message,
    MessageEnd,
    MessageStart,
    StreamEvent,
    TextBlock,
    TextDelta,
    ThinkingBlock,
    ThinkingDelta,
    ToolDef,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
)


class AnthropicProvider(Provider):
    id = "anthropic"

    def __init__(self, client: anthropic.AsyncAnthropic | None = None) -> None:
        # 凭证由 SDK 从环境解析（ANTHROPIC_API_KEY 等）；无效时在请求期映射为 ProviderAuthError
        self._client = client if client is not None else anthropic.AsyncAnthropic()

    def configured(self, model_id: str) -> bool:
        """有显式 key 或环境变量非空才算已配置（Settings 的"未配置即报错"）。"""
        return bool(self._client.api_key or os.environ.get("ANTHROPIC_API_KEY"))

    async def stream(
        self,
        messages: list[Message],
        model_id: str,
        *,
        system: str | None = None,
        effort: Effort = DEFAULT_EFFORT,
        tools: list[ToolDef] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        request_messages = [
            {"role": "user" if m.role == "toolResult" else m.role,
             "content": [_block_to_api(b) for b in m.content]}
            for m in messages
        ]
        tool_defs = (
            [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in tools
            ]
            if tools
            else None
        )
        # 稳定前缀打缓存断点（票 16）：system 与最后一个工具跨轮不变，断点使其进入
        # prompt cache——多轮会话第二轮起按 1/10 计价读回（Anthropic 上限 4 断点，此处占 1–2 个）
        system_param = (
            [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
            if system
            else None
        )
        if tool_defs:
            tool_defs[-1] = {**tool_defs[-1], "cache_control": {"type": "ephemeral"}}
        # 历史增量断点（票 16，第三个断点）：打在最后一条消息的末块——本轮输入即刻
        # 写入缓存前缀，下一轮整段命中；loop 中间态与重放下末条恒为 user/toolResult，
        # 不会落在 thinking 块上
        if request_messages:
            request_messages[-1]["content"][-1]["cache_control"] = {"type": "ephemeral"}
        # 429 / 5xx 指数退避重试（ticket 15）：仅当尚未产出任何流事件时——
        # 流中途失败重试会导致重复输出，只能向上抛错由调用方收尾
        attempt = 0
        while True:
            yielded_any = False
            try:
                async with self._client.messages.stream(
                    model=model_id,
                    max_tokens=DEFAULT_MAX_TOKENS,
                    # display="summarized"：默认 "omitted" 时思考块为空文本，前端在思考期只能干等
                    thinking={"type": "adaptive", "display": "summarized"},
                    output_config={"effort": effort},
                    system=system_param,
                    tools=tool_defs,
                    messages=request_messages,
                ) as stream:
                    async for event in stream:
                        for mapped in _map_event(event):
                            yielded_any = True
                            yield mapped
                    final = stream.get_final_message()
                    if inspect.isawaitable(final):
                        # AsyncMessageStream 的 get_final_message 是 async（SDK 1.12）
                        final = await final
                    yield MessageEnd(
                        stop_reason=final.stop_reason,
                        usage=_usage_from(final.usage),
                        message=_message_from_api(final),
                    )
                    return
            except anthropic.AuthenticationError as exc:
                raise ProviderAuthError(
                    "Anthropic API key 缺失或无效——请检查服务端的 ANTHROPIC_API_KEY"
                ) from exc
            except anthropic.RateLimitError as exc:
                retryable = ProviderRateLimitError("触发 Anthropic 速率限制，正在自动重试")
            except anthropic.APIConnectionError as exc:
                raise ProviderConnectionError("无法连接 Anthropic API，请检查网络") from exc
            except anthropic.APIStatusError as exc:
                if exc.status_code < 500:
                    raise ProviderError(f"Anthropic API 错误（{exc.status_code}）：{exc.message}") from exc
                retryable = ProviderServerError(f"Anthropic 服务暂不可用（{exc.status_code}），正在自动重试")
            if yielded_any or attempt >= len(RETRY_DELAYS):
                raise retryable
            await asyncio.sleep(RETRY_DELAYS[attempt] + random.uniform(0, 0.25))
            attempt += 1


def _result_block_to_api(block: dict) -> dict:
    """存储形态的富块 → Anthropic API 块（image 需嵌套 source 形态）。"""
    if block.get("type") == "image":
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": block.get("media_type", ""), "data": block.get("data", "")},
        }
    return {"type": "text", "text": str(block.get("text") or "")}


def _block_to_api(block: ContentBlock) -> dict:
    if isinstance(block, ThinkingBlock):
        out: dict = {"type": "thinking", "thinking": block.text}
        if block.signature:
            out["signature"] = block.signature
        return out
    if isinstance(block, ToolUseBlock):
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    if isinstance(block, ToolResultBlock):
        # 票 23：有富块 → API content 列表（模型能看图）；无 → 字符串（存量不变）
        content: str | list[dict] = (
            [_result_block_to_api(b) for b in block.blocks] if block.blocks else block.content
        )
        out = {"type": "tool_result", "tool_use_id": block.tool_use_id, "content": content}
        if block.is_error:
            out["is_error"] = True
        return out
    if isinstance(block, ImageBlock):
        # 票 01：用户消息多模态图片块 → API image source
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": block.media_type, "data": block.data},
        }
    return {"type": "text", "text": block.text}


def _map_event(event) -> list[StreamEvent]:
    """SDK 流事件 → StreamEvent（未识别的事件丢弃，保持前向兼容）。"""
    if event.type == "message_start":
        return [
            MessageStart(model=event.message.model, usage=_usage_from(event.message.usage)),
        ]
    if event.type == "content_block_delta":
        if event.delta.type == "text_delta":
            return [TextDelta(event.delta.text)]
        if event.delta.type == "thinking_delta":
            return [ThinkingDelta(event.delta.thinking)]
    return []


def _usage_from(usage) -> Usage:
    return Usage(
        input_tokens=int(usage.input_tokens or 0),
        output_tokens=int(usage.output_tokens or 0),
        cache_read_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
        cache_write_tokens=int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
    )


def _message_from_api(message) -> Message:
    blocks: list[ContentBlock] = []
    for block in message.content:
        if block.type == "text":
            blocks.append(TextBlock(block.text))
        elif block.type == "thinking":
            blocks.append(ThinkingBlock(text=block.thinking, signature=block.signature or ""))
        elif block.type == "tool_use":
            blocks.append(ToolUseBlock(id=block.id, name=block.name, input=dict(block.input)))
    return Message(role="assistant", content=blocks)
