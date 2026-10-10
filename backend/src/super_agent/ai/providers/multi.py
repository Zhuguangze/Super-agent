"""MultiProvider——按 model_id 路由到已注册 Provider（自定义模型接入点）。

内置模型（MODELS）默认路由到 AnthropicProvider；settings.json 的
custom_models（OpenAI 兼容 / 自定义 base_url 的 Anthropic）经
rebind_custom_models 动态注册进路由表与 MODELS——GET /api/models 与
set_model 随之可见可用，Settings 热更无需重启。
"""

import logging
from collections.abc import AsyncIterator

import anthropic

from super_agent.ai.provider import (
    MODELS,
    Effort,
    Model,
    Provider,
    ProviderError,
)
from super_agent.ai.providers.anthropic import AnthropicProvider
from super_agent.ai.providers.openai_compat import OpenAICompatProvider
from super_agent.ai.types import Message, StreamEvent, ToolDef

DEFAULT_CUSTOM_CONTEXT = 128_000
DEFAULT_CUSTOM_MAX_OUTPUT = 32_000


def _int_field(spec: dict, key: str, fallback: int, low: int, high: int) -> int:
    try:
        value = int(spec.get(key) or fallback)
    except (TypeError, ValueError):
        value = fallback
    return max(low, min(high, value))


class MultiProvider(Provider):
    id = "multi"

    def __init__(self) -> None:
        self._by_model: dict[str, Provider] = {}
        self._custom_ids: set[str] = set()

    # ---- 注册 ----

    def register_for_models(self, model_ids: list[str], provider: Provider) -> None:
        for model_id in model_ids:
            self._by_model[model_id] = provider

    def rebind_custom_models(self, specs: list[dict]) -> list[str]:
        """按 Settings 的 custom_models 全量重建路由与 MODELS 注册，返回错误列表。"""
        errors: list[str] = []
        for model_id in list(self._custom_ids):
            self._by_model.pop(model_id, None)
            MODELS.pop(model_id, None)
        self._custom_ids.clear()

        for spec in specs:
            model_id = str(spec.get("id") or "").strip()
            name = str(spec.get("name") or model_id)
            kind = str(spec.get("provider") or "openai-compat")
            base_url = str(spec.get("base_url") or "").strip()
            api_key = str(spec.get("api_key") or "").strip() or None
            if not model_id or not base_url:
                errors.append(f"自定义模型缺少 id 或 base_url: {spec!r}")
                continue
            if model_id in MODELS:
                errors.append(f"模型 ID 与已有模型冲突: {model_id}")
                continue
            try:
                if kind == "anthropic":
                    provider: Provider = AnthropicProvider(
                        client=anthropic.AsyncAnthropic(base_url=base_url, api_key=api_key or None)
                    )
                else:
                    provider = OpenAICompatProvider(base_url, api_key)
            except Exception as exc:
                errors.append(f"模型 {model_id} 的 Provider 构建失败: {exc}")
                continue
            MODELS[model_id] = Model(
                id=model_id,
                name=name,
                provider="custom",
                context_window=_int_field(spec, "context_window", DEFAULT_CUSTOM_CONTEXT, 1_000, 10_000_000),
                max_output_tokens=_int_field(spec, "max_output_tokens", DEFAULT_CUSTOM_MAX_OUTPUT, 256, 200_000),
            )
            self._by_model[model_id] = provider
            self._custom_ids.add(model_id)
        if errors:
            logging.warning("自定义模型注册问题: %s", errors)
        return errors

    @property
    def custom_model_ids(self) -> set[str]:
        return set(self._custom_ids)

    # ---- Provider 协议 ----

    def configured(self, model_id: str) -> bool:
        """路由到子 Provider 查询；未注册的模型视为未配置。"""
        provider = self._by_model.get(model_id)
        if provider is None:
            return False
        return provider.configured(model_id)

    async def stream(
        self,
        messages: list[Message],
        model_id: str,
        *,
        system: str | None = None,
        effort: Effort,
        tools: list[ToolDef] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        provider = self._by_model.get(model_id)
        if provider is None:
            raise ProviderError(f"未知模型: {model_id}（可用: {', '.join(sorted(MODELS))}）")
        async for event in provider.stream(messages, model_id, system=system, effort=effort, tools=tools):
            yield event
