"""会话持久化——JSONL append-only 树（ADR-0002，照搬 pi 的会话格式）。

文件：``<root>/<user_id>/<timestamp>_<session_id>.jsonl``
首行 header：``{"type":"session","version":1,"id","timestamp","user_id"}``
其后每行一个条目：``{"type","id","parentId","timestamp", ...}`` 构成树；
活跃分支即根到叶路径，重放活跃分支即重建上下文。
用户隔离由目录边界天然保证：一切读写都以 user_id 为第一级路径。
"""

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from super_agent.ai.types import (
    ContentBlock,
    ImageBlock,
    Message,
    ThinkingBlock,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
)

HEADER_TYPE = "session"
FORMAT_VERSION = 1
SUMMARY_LEAD = "（此前对话的摘要，供续接上下文）\n"


def _walk_active_branch(entries: list[dict], start_id: str | None = None) -> list[dict]:
    """从指定条目（缺省最后一条）沿 parentId 回溯活跃分支（新→旧）。

    返回链上的 message / compaction 条目。压缩语义（ticket 10）：
    - collect 模式：正常收集；遇 compaction → 收下摘要条目并进入 bounded
    - bounded 模式：只收 first_kept_entry_id 起的保留段；走到边界或遇到
      更早的 compaction（其上内容已被最近摘要覆盖）即停止
    """
    if not entries:
        return []
    by_id = {e["id"]: e for e in entries}
    picked: list[dict] = []
    mode = "collect"
    boundary: str | None = None
    cur: dict | None = by_id.get(start_id) if start_id else entries[-1]
    while cur is not None:
        etype = cur["type"]
        if mode == "collect":
            if etype in ("message", "compaction"):
                picked.append(cur)
                if etype == "compaction":
                    boundary = cur.get("first_kept_entry_id")
                    mode = "bounded" if boundary else "stop"
        elif mode == "bounded":
            if etype == "message":
                picked.append(cur)
            if cur["id"] == boundary or etype == "compaction":
                mode = "stop"
        parent_id = cur.get("parentId")
        cur = by_id.get(parent_id) if parent_id else None
    return picked


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_entry_id() -> str:
    return f"e_{uuid.uuid4().hex[:12]}"


@dataclass
class SessionMeta:
    id: str
    user_id: int
    updated_at: float  # 文件 mtime（unix 秒）
    title: str = ""  # 显式重命名优先，否则首条 user 消息截断
    msg_count: int = 0
    total_tokens: int = 0  # usage 条目 input+output 累计
    pinned: bool = False  # 置顶（header 元数据）


class SessionStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    # ---- 写 ----

    def create_session(self, user_id: int) -> str:
        session_id = uuid.uuid4().hex
        user_dir = self._user_dir(user_id)
        user_dir.mkdir(parents=True, exist_ok=True)
        header = {
            "type": HEADER_TYPE,
            "version": FORMAT_VERSION,
            "id": session_id,
            "timestamp": _now_iso(),
            "user_id": user_id,
        }
        path = self._session_path(user_id, session_id)
        path.write_text(json.dumps(header, ensure_ascii=False) + "\n", encoding="utf-8")
        return session_id

    def append_message(
        self,
        user_id: int,
        session_id: str,
        message: Message,
        *,
        usage: Usage | None = None,
        stop_reason: str | None = None,
        parent_entry_id: str | None = None,
    ) -> str | None:
        """追加一条 message 条目；usage 非空时再挂一条 usage 子条目。

        parent_entry_id 指定时从该条目分叉（Branch，ADR-0002 树结构）；
        缺省挂当前叶（文件最后一个条目）。append-only：新分支的消息总在文件尾，
        因此"沿最后一条 message 回溯"重放出的即是活跃分支。
        返回 message 条目 id（无该会话时返回 None——调用方保证会话存在）。
        """
        path = self._find_path(user_id, session_id)
        if path is None:
            return None
        parent_id = parent_entry_id if parent_entry_id is not None else self._leaf_entry_id(path)
        entry: dict = {
            "type": "message",
            "id": _new_entry_id(),
            "parentId": parent_id,
            "timestamp": _now_iso(),
            "message": _message_to_dict(message),
        }
        if stop_reason is not None:
            entry["stop_reason"] = stop_reason
        lines = [json.dumps(entry, ensure_ascii=False)]
        if usage is not None:
            usage_entry = {
                "type": "usage",
                "id": _new_entry_id(),
                "parentId": entry["id"],
                "timestamp": _now_iso(),
                "usage": {
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "cache_read_tokens": usage.cache_read_tokens,
                    "cache_write_tokens": usage.cache_write_tokens,
                },
            }
            lines.append(json.dumps(usage_entry, ensure_ascii=False))
        with path.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return str(entry["id"])

    def delete_session(self, user_id: int, session_id: str) -> bool:
        """删除会话文件；不存在返回 False。"""
        path = self._find_path(user_id, session_id)
        if path is None:
            return False
        path.unlink()
        return True

    def update_session_header(
        self,
        user_id: int,
        session_id: str,
        *,
        title: str | None = None,
        pinned: bool | None = None,
    ) -> bool:
        """更新会话 header 元数据（重命名/置顶）。树条目不动，仅重写首行。

        title 传空串表示清除显式标题（回退到首条消息自动标题）。
        """
        path = self._find_path(user_id, session_id)
        if path is None:
            return False
        try:
            stat = path.stat()
            lines = path.read_text(encoding="utf-8").splitlines()
            if not lines:
                return False
            header = json.loads(lines[0])
            if header.get("type") != HEADER_TYPE:
                return False
            if title is not None:
                header["title"] = title.strip()
            if pinned is not None:
                header["pinned"] = bool(pinned)
            lines[0] = json.dumps(header, ensure_ascii=False)
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            # 元数据更新不算"会话有新消息"——恢复 mtime，避免污染最近更新排序
            os.utime(path, (stat.st_atime, stat.st_mtime))
        except (OSError, json.JSONDecodeError):
            return False
        return True

    def append_model_change(self, user_id: int, session_id: str, model: str) -> str | None:
        """记录模型切换（model_change 条目，spec §4.5 v1 条目类型）。"""
        path = self._find_path(user_id, session_id)
        if path is None:
            return None
        entry = {
            "type": "model_change",
            "id": _new_entry_id(),
            "parentId": self._leaf_entry_id(path),
            "timestamp": _now_iso(),
            "model": model,
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry["id"]

    def total_usage(self, user_id: int) -> dict:
        """该用户全部会话的 token 用量累计（usage 条目求和，ticket 14）。"""
        totals = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}
        user_dir = self._user_dir(user_id)
        if not user_dir.is_dir():
            return totals
        for path in user_dir.glob("*.jsonl"):
            for line in path.read_text(encoding="utf-8").splitlines()[1:]:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") != "usage" or not isinstance(entry.get("usage"), dict):
                    continue
                for key in totals:
                    totals[key] += int(entry["usage"].get(key) or 0)
        return totals

    def append_compaction(
        self,
        user_id: int,
        session_id: str,
        *,
        summary: str,
        first_kept_entry_id: str | None,
    ) -> str | None:
        """追加 compaction 条目（ticket 10，spec §4.5）。

        挂当前叶；first_kept_entry_id 之后的条目保留原文，之前的以 summary 覆盖。
        返回条目 id。
        """
        path = self._find_path(user_id, session_id)
        if path is None:
            return None
        entry = {
            "type": "compaction",
            "id": _new_entry_id(),
            "parentId": self._leaf_entry_id(path),
            "timestamp": _now_iso(),
            "summary": summary,
            "first_kept_entry_id": first_kept_entry_id,
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry["id"]

    # ---- 读 ----

    def get_session(self, user_id: int, session_id: str) -> SessionMeta | None:
        path = self._find_path(user_id, session_id)
        if path is None:
            return None
        stat = path.stat()
        return SessionMeta(id=session_id, user_id=user_id, updated_at=stat.st_mtime)

    def list_sessions(self, user_id: int) -> list[SessionMeta]:
        """该用户的全部会话；置顶在前，其余最近更新在前。附标题/消息数/token 累计。"""
        user_dir = self._user_dir(user_id)
        if not user_dir.is_dir():
            return []
        metas: list[SessionMeta] = []
        for path in user_dir.glob("*.jsonl"):
            session_id = _sid_from_filename(path.name)
            if session_id is None:
                continue
            summary = _summarize_file(path)
            metas.append(
                SessionMeta(
                    session_id, user_id, path.stat().st_mtime,
                    title=summary["title"],
                    msg_count=summary["msg_count"],
                    total_tokens=summary["total_tokens"],
                    pinned=summary["pinned"],
                )
            )
        return sorted(metas, key=lambda m: (not m.pinned, -m.updated_at))

    def latest_session(self, user_id: int) -> str | None:
        sessions = self.list_sessions(user_id)
        return sessions[0].id if sessions else None

    def read_entries(self, user_id: int, session_id: str) -> list[dict]:
        """全量原始条目（REST 回放用），header 不含在内。"""
        path = self._find_path(user_id, session_id)
        if path is None:
            return []
        entries: list[dict] = []
        for line in path.read_text(encoding="utf-8").splitlines()[1:]:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
        return entries

    def replay_messages(self, user_id: int, session_id: str) -> list[Message]:
        """活跃分支（根到叶）重建上下文（ticket 10 压缩语义）。

        遇 compaction 条目：注入一条摘要合成消息（置于最前），其后收集
        first_kept_entry_id 起的保留段原文；更早的内容以摘要覆盖
        （再次压缩时旧 compaction 直接停止）。无 signature 的 thinking 块
        （如 abort 截断的部分输出）无法回放给 API，跳过。
        """
        entries = self.read_entries(user_id, session_id)
        picked = _walk_active_branch(entries)

        # walk 输出为新→旧：摘要条目在最前，保留段随后（新→旧）
        chain: list[Message] = []
        for entry in picked:
            if entry["type"] == "compaction":
                chain.append(
                    Message(
                        role="user",
                        content=[TextBlock(SUMMARY_LEAD + entry.get("summary", ""))],
                    )
                )
        for entry in reversed(picked):
            if entry["type"] == "message":
                message = _message_from_dict(entry["message"])
                if message is not None:
                    chain.append(message)
        return chain

    def read_active_branch(self, user_id: int, session_id: str) -> list[dict]:
        """活跃分支条目（前端回放用），保持文件顺序——read_branch(None) 的特例。"""
        return self.read_branch(user_id, session_id)

    def read_branch(self, user_id: int, session_id: str, entry_id: str | None = None) -> list[dict]:
        """任意分支的活跃路径（票 17）：entry_id 指定时回放根到该条目；
        None 即全局活跃分支（文件最后条目的祖先链）。

        分叉后非链上条目不返回——线性展示不串台；链上 message 的 usage
        子条目随消息一起返回；compaction 条目随链返回（前端据以提示）。
        """
        entries = self.read_entries(user_id, session_id)
        if not entries:
            return []
        # 起点：None 即全局活跃分支（文件最后条目）；entry_id 指定时沿最后子代
        # 降到其子树叶（票 17）——切换分支视图看到该分支的最新延续
        if entry_id is None:
            start_id = entries[-1]["id"]
        else:
            children: dict[str | None, list[str]] = {}
            for e in entries:
                children.setdefault(e.get("parentId"), []).append(e["id"])
            start_id = entry_id
            while children.get(start_id):
                start_id = children[start_id][-1]
        picked = _walk_active_branch(entries, start_id=start_id)
        on_branch = {e["id"] for e in picked}
        # 分支注记（票 17）：链上条目记录其最近 conversation 祖先下的兄弟——
        # usage/model_change 是元数据中间节点，不计入分支也不隔断兄弟关系
        by_id = {e["id"]: e for e in entries}

        def _conv_parent(entry: dict) -> str | None:
            pid = entry.get("parentId")
            while pid is not None and by_id[pid]["type"] not in ("message", "compaction"):
                pid = by_id[pid].get("parentId")
            return pid

        conv_children: dict[str | None, list[str]] = {}
        for e in entries:
            if e["type"] in ("message", "compaction"):
                conv_children.setdefault(_conv_parent(e), []).append(e["id"])
        out: list[dict] = []
        for e in entries:
            if e["id"] in on_branch:
                if e["type"] in ("message", "compaction"):
                    siblings = conv_children.get(_conv_parent(e), [])
                    e = {
                        **e,
                        "branch_index": siblings.index(e["id"]),
                        "branch_total": len(siblings),
                        "siblings": siblings,
                    }
                out.append(e)
            elif e.get("parentId") in on_branch and e["type"] == "usage":
                out.append(e)
        return out

    # ---- 路径 ----

    def _user_dir(self, user_id: int) -> Path:
        return self._root / str(user_id)

    def _session_path(self, user_id: int, session_id: str) -> Path:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        return self._user_dir(user_id) / f"{ts}_{session_id}.jsonl"

    def _find_path(self, user_id: int, session_id: str) -> Path | None:
        user_dir = self._user_dir(user_id)
        if not user_dir.is_dir():
            return None
        for path in user_dir.glob(f"*_{session_id}.jsonl"):
            return path
        return None

    def _leaf_entry_id(self, path: Path) -> str | None:
        """最后一个条目的 id（append-only，即当前叶）。"""
        leaf_id: str | None = None
        for line in path.read_text(encoding="utf-8").splitlines()[1:]:
            line = line.strip()
            if line:
                leaf_id = str(json.loads(line)["id"])
        return leaf_id


def _sid_from_filename(name: str) -> str | None:
    stem = name.removesuffix(".jsonl")
    if "_" not in stem:
        return None
    return stem.split("_", 1)[1]


def _summarize_file(path: Path) -> dict:
    """单会话文件摘要：显式标题（header，重命名）优先于首条 user 消息截断、
    message 条数、usage 累计、置顶标志。"""
    explicit_title = ""
    pinned = False
    auto_title = ""
    msg_count = 0
    total_tokens = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {"title": "", "pinned": False, "msg_count": 0, "total_tokens": 0}
    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if i == 0:  # header 行：会话元数据（title/pinned 由重命名/置顶写入）
            explicit_title = str(entry.get("title") or "")
            pinned = bool(entry.get("pinned"))
            continue
        if entry.get("type") == "message" and isinstance(entry.get("message"), dict):
            msg_count += 1
            if not auto_title and entry["message"].get("role") == "user":
                for block in entry["message"].get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                        auto_title = str(block["text"])[:60]
                        break
        elif entry.get("type") == "usage" and isinstance(entry.get("usage"), dict):
            total_tokens += int(entry["usage"].get("input_tokens") or 0)
            total_tokens += int(entry["usage"].get("output_tokens") or 0)
    return {
        "title": explicit_title or auto_title,
        "pinned": pinned,
        "msg_count": msg_count,
        "total_tokens": total_tokens,
    }


def _message_to_dict(message: Message) -> dict:
    blocks: list[dict] = []
    for block in message.content:
        if isinstance(block, TextBlock):
            blocks.append({"type": "text", "text": block.text})
        elif isinstance(block, ThinkingBlock):
            entry = {"type": "thinking", "thinking": block.text}
            if block.signature:
                entry["signature"] = block.signature
            blocks.append(entry)
        elif isinstance(block, ToolUseBlock):
            blocks.append(
                {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
            )
        elif isinstance(block, ToolResultBlock):
            entry = {
                "type": "tool_result",
                "tool_use_id": block.tool_use_id,
                "content": block.content,
            }
            if block.is_error:
                entry["is_error"] = True
            if block.blocks:  # 票 23：富块（text/image）；存量条目无此键
                entry["blocks"] = block.blocks
            blocks.append(entry)
        elif isinstance(block, ImageBlock):
            blocks.append(
                {"type": "image", "media_type": block.media_type, "data": block.data}
            )
    return {"role": message.role, "content": blocks}


def _message_from_dict(data: dict) -> Message | None:
    blocks: list[ContentBlock] = []
    for block in data.get("content", []):
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            blocks.append(TextBlock(block["text"]))
        elif block.get("type") == "thinking" and block.get("signature"):
            blocks.append(ThinkingBlock(text=block.get("thinking", ""), signature=block["signature"]))
        elif block.get("type") == "tool_use":
            blocks.append(
                ToolUseBlock(
                    id=block.get("id", ""), name=block.get("name", ""),
                    input=block.get("input") or {},
                )
            )
        elif block.get("type") == "tool_result":
            blocks.append(
                ToolResultBlock(
                    tool_use_id=block.get("tool_use_id", ""),
                    content=block.get("content", ""),
                    is_error=bool(block.get("is_error", False)),
                    blocks=block.get("blocks"),
                )
            )
        elif block.get("type") == "image":
            blocks.append(
                ImageBlock(
                    media_type=str(block.get("media_type", "")),
                    data=str(block.get("data", "")),
                )
            )
    return Message(role=data.get("role", "user"), content=blocks)
