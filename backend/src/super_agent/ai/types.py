"""ai 层类型——Message / ContentBlock / ToolDef / Usage / StreamEvent（spec §2/§4.1）。"""

from dataclasses import dataclass, field
from typing import ClassVar, Literal

Role = Literal["system", "user", "assistant", "toolResult"]


@dataclass
class TextBlock:
    text: str


@dataclass
class ThinkingBlock:
    """思考块：signature 是回放给 API 的凭据，落盘时必须原样保留（ticket 04）。"""

    text: str
    signature: str = ""


@dataclass
class ToolUseBlock:
    """assistant 发起的工具调用（API 的 tool_use 块）。"""

    id: str
    name: str
    input: dict = field(default_factory=dict)


@dataclass
class ToolResultBlock:
    """工具执行结果（API 的 tool_result 块，随 user 角色消息回传）。

    blocks（票 23）：富内容块（text/image），缺省 None = 纯字符串形态（存量兼容）。
    """

    tool_use_id: str
    content: str
    is_error: bool = False
    blocks: list[dict] | None = None


@dataclass
class ImageBlock:
    """用户消息中的图片块（多模态输入）；与工具结果富块同构的扁平字典形状（票 24 约定）。

    data 为 base64 字符串，media_type 限 png/jpeg/webp/gif。
    """

    media_type: str
    data: str


ContentBlock = TextBlock | ThinkingBlock | ToolUseBlock | ToolResultBlock | ImageBlock


@dataclass
class ToolDef:
    """工具定义（API 侧形态：name / description / JSON Schema parameters）。"""

    name: str
    description: str
    parameters: dict


@dataclass
class Message:
    role: Role
    content: list[ContentBlock]

    def text(self) -> str:
        """拼接全部 text block（thinking / 工具块不算）。"""
        return "".join(b.text for b in self.content if isinstance(b, TextBlock))


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


# ---- StreamEvent：Provider → 上层的单向事件流（pi stream 事件的对应物） ----


@dataclass
class MessageStart:
    type: ClassVar[str] = "message_start"
    model: str
    usage: Usage


@dataclass
class TextDelta:
    type: ClassVar[str] = "text_delta"
    delta: str


@dataclass
class ThinkingDelta:
    type: ClassVar[str] = "thinking_delta"
    delta: str


@dataclass
class MessageEnd:
    type: ClassVar[str] = "message_end"
    stop_reason: str | None
    usage: Usage
    message: Message  # 完整 assistant 消息（含 thinking 签名），供持久化


StreamEvent = MessageStart | TextDelta | ThinkingDelta | MessageEnd
