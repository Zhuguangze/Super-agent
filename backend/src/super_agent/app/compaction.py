"""Compaction——长会话接近上下文上限时把早期内容换成摘要（ticket 10，spec §4.5）。

流程：活跃分支上早于保留段的消息转录给 LLM 生成摘要 → 写 compaction 条目
（summary + first_kept_entry_id）→ 重放从摘要处续接（见 session_store）。
压缩本身是一次静默的 LLM 调用（流事件不透传），前后发 compaction_start/end。
"""

from super_agent.ai.provider import Effort, Provider
from super_agent.ai.types import Message, TextBlock, TextDelta
from super_agent.app.session_store import SUMMARY_LEAD

# 保留段：最近 N 条 message 原文不动，更早的进摘要
COMPACT_KEEP_LAST = 8
# 自动触发阈值占上下文窗口的比例：压缩本身要消耗 token 生成摘要，故留出余量
AUTO_COMPACT_RATIO = 0.8


def auto_compact_threshold(context_window: int) -> int:
    """按模型上下文窗口推导自动压缩阈值。

    阈值不能写死：写死 160K 时，128K 窗口的模型输入永远到不了 160K，
    自动压缩成了死代码；1M 窗口的模型又会在 160K 就压缩，浪费 84% 的窗口。
    """
    return int(context_window * AUTO_COMPACT_RATIO)

SUMMARY_SYSTEM = (
    "你是对话摘要助手。把给出的对话浓缩成要点摘要，供后续对话续接上下文使用。"
    "必须保留：用户的意图与约束、已做出的关键决定、工具执行的结论、未完成事项。"
    "用对话的语言书写，直接输出摘要正文。"
)


def transcribe(messages: list[Message]) -> str:
    """对话转录：每行一条 `[role] 内容`，工具结果标注来源与图片数（票 23）。"""
    lines: list[str] = []
    for message in messages:
        text = message.text().strip()
        if not text:
            blocks_desc = "、".join(block.type for block in message.content) or "空"
            text = f"（{blocks_desc} 内容）"
        if message.role == "toolResult":
            images = 0
            for block in message.content:
                rich = getattr(block, "blocks", None) or []
                images += sum(1 for b in rich if isinstance(b, dict) and b.get("type") == "image")
            if images:
                text += f"（含 {images} 张图片）"
        prefix = {"user": "用户", "assistant": "助手", "toolResult": "工具结果"}.get(
            message.role, message.role
        )
        lines.append(f"[{prefix}] {text}")
    return "\n".join(lines)


async def summarize(
    provider: Provider,
    model_id: str,
    effort: Effort,
    transcript: str,
    *,
    instructions: str | None = None,
) -> str:
    """静默生成摘要（流事件不透传给前端）。

    instructions：调用方的附加要求（扩展的 before_compaction 钩子或手动
    compact 命令传入），并入系统提示，用于引导摘要聚焦——摘要质量差是
    压缩最实际的痛点。
    """
    parts: list[str] = []
    system = SUMMARY_SYSTEM
    if instructions and instructions.strip():
        system = f"{SUMMARY_SYSTEM}\n\n附加要求：{instructions.strip()}"
    messages = [Message(role="user", content=[TextBlock(transcript)])]
    async for event in provider.stream(messages, model_id, system=system, effort=effort):
        if isinstance(event, TextDelta):
            parts.append(event.delta)
    return "".join(parts).strip()


def is_summary_message(message: Message) -> bool:
    first = message.content[0] if message.content else None
    return isinstance(first, TextBlock) and first.text.startswith(SUMMARY_LEAD)
