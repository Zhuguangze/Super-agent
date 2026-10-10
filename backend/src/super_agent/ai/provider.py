"""Provider 抽象——LLM 供应方统一接口，多 Provider 形态从第一天成立（spec §1）。

ai 层不知道工具执行：Provider 只负责"消息进 → 流式事件出"。
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal

from super_agent.ai.types import Message, StreamEvent, ToolDef

Effort = Literal["low", "medium", "high", "xhigh", "max"]
DEFAULT_EFFORT: Effort = "high"

# 流式请求给足输出空间——大 max_tokens 必须配流式，否则有 HTTP 超时截断风险
DEFAULT_MAX_TOKENS = 64000


@dataclass(frozen=True)
class Model:
    id: str
    name: str  # 界面展示名
    provider: str
    context_window: int
    max_output_tokens: int


# 模型注册表——**全部动态**（Settings 的 custom_models 注册，票：模型配置化）。
# 代码不预置任何模型：零配置 = 零模型，前端引导到设置页添加。
# 测试需要具体模型时直接向本 dict 插入条目（teardown 清理）。
MODELS: dict[str, Model] = {}


class ProviderError(Exception):
    """Provider 层错误；message 面向最终用户可读，code 供前端判别。"""

    code = "provider_error"


class ProviderAuthError(ProviderError):
    code = "provider_auth"


class ProviderRateLimitError(ProviderError):
    code = "provider_rate_limit"


class ProviderConnectionError(ProviderError):
    code = "provider_connection"


class ProviderServerError(ProviderError):
    """供应方 5xx/过载——瞬时故障，重试前先退避（ticket 15）。"""

    code = "provider_error"


# 指数退避（秒）；未产出任何流事件前的可重试错误按此序列重试
RETRY_DELAYS: tuple[float, ...] = (1.0, 2.0, 4.0)


class Provider(ABC):
    """单一抽象：流式补全。新增 Provider = 实现一个 stream()。

    可选能力：覆写 ``configured(model_id) -> bool`` 声明"凭证是否就绪"——
    app 层据此在发送前拦截未配置的模型（Settings："只有已配置的模型才有回答"）。
    未覆写（测试替身等）不参与该校验。
    """

    id: str

    @abstractmethod
    def stream(
        self,
        messages: list[Message],
        model_id: str,
        *,
        system: str | None = None,
        effort: Effort = DEFAULT_EFFORT,
        tools: list[ToolDef] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """流式产出 StreamEvent；失败抛 ProviderError 子类。中断 = 取消消费方 task。"""
