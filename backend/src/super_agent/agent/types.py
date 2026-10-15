"""agent 层类型——AgentTool / AgentToolResult / AgentEvent（spec §4.4，事件对齐 §4.1）。

AgentMessage 复用 ai.types.Message（roles 含 toolResult）；agent 层不 import 任何上层。
provider 的流事件（MessageStart / TextDelta / ThinkingDelta / MessageEnd）经 on_event 原样透传，
与下面的 AgentEvent 构成统一事件流（pi 的核心设计）。
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import ClassVar, Literal, Protocol, runtime_checkable

from super_agent.ai.types import (  # noqa: F401 —— 流事件随 on_event 透传
    Message,
    MessageEnd,
    MessageStart,
    TextDelta,
    ThinkingDelta,
    Usage,
)

ExecutionMode = Literal["parallel", "sequential"]

ToolUpdateFn = Callable[[str], Awaitable[None]]

AgentEventEmitter = Callable[[object], Awaitable[None]]


@dataclass
class AgentToolResult:
    """工具结果：content 为文本形态（回退/摘要），blocks 携带富内容（票 23）。

    blocks 元素约定：``{"type": "text", "text": ...}`` 或
    ``{"type": "image", "media_type": "image/png", "data": "<base64>"}``。
    """

    content: str
    is_error: bool = False
    details: dict | None = None
    blocks: list[dict] | None = None
    terminate: bool = False


@runtime_checkable
class AgentTool(Protocol):
    """工具协议（spec §4.4）：schema 声明参数，异步执行，默认并行。"""

    name: str
    description: str
    parameters: dict  # JSON Schema
    execution_mode: ExecutionMode

    async def execute(
        self,
        tool_call_id: str,
        params: dict,
        signal: asyncio.Event,
        on_update: ToolUpdateFn | None,
    ) -> AgentToolResult: ...


@dataclass
class ToolCall:
    """一次待执行的调用（loop 从 assistant 消息提取）。"""

    id: str
    name: str
    params: dict
    tool: AgentTool | None


# ---- AgentEvent：loop → 上层（app）的事件 ----


@dataclass
class AgentStart:
    type: ClassVar[str] = "agent_start"


@dataclass
class TurnStart:
    type: ClassVar[str] = "turn_start"
    turn: int


@dataclass
class TurnEnd:
    type: ClassVar[str] = "turn_end"
    turn: int
    stop_reason: str | None


@dataclass
class ToolExecutionStart:
    type: ClassVar[str] = "tool_execution_start"
    tool_call_id: str
    name: str
    params: dict


@dataclass
class ToolExecutionUpdate:
    type: ClassVar[str] = "tool_execution_update"
    tool_call_id: str
    delta: str


@dataclass
class ToolExecutionEnd:
    type: ClassVar[str] = "tool_execution_end"
    tool_call_id: str
    result: AgentToolResult


@dataclass
class MessagesAppended:
    """工具结果（及 steer）回填进上下文后触发——app 层据此落盘。"""

    type: ClassVar[str] = "messages_appended"
    messages: list[Message]


@dataclass
class AgentEnd:
    type: ClassVar[str] = "agent_end"
    stop_reason: str  # end_turn / max_turns / aborted
    usage: Usage
    message: Message | None  # 最后一条 assistant 消息


AgentEvent = (
    AgentStart
    | TurnStart
    | TurnEnd
    | ToolExecutionStart
    | ToolExecutionUpdate
    | ToolExecutionEnd
    | MessagesAppended
    | AgentEnd
)
