"""Seam B5：SessionStore——JSONL 树格式、追加、重放与用户隔离（ADR-0002）。"""

import json

from super_agent.ai.types import Message, TextBlock, ThinkingBlock, ToolResultBlock, Usage
from super_agent.app.session_store import SessionStore


def _store(tmp_path) -> SessionStore:
    return SessionStore(tmp_path / "sessions")


def test_create_writes_header_with_version(tmp_path) -> None:
    store = _store(tmp_path)
    sid = store.create_session(user_id=1)

    path = next((tmp_path / "sessions" / "1").glob("*.jsonl"))
    assert path.name.endswith(f"_{sid}.jsonl")
    header = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert header["type"] == "session"
    assert header["version"] == 1
    assert header["id"] == sid
    assert header["user_id"] == 1


def test_append_builds_parent_chain_and_usage_entry(tmp_path) -> None:
    store = _store(tmp_path)
    sid = store.create_session(1)
    store.append_message(1, sid, Message(role="user", content=[TextBlock("hi")]))
    store.append_message(
        1,
        sid,
        Message(
            role="assistant",
            content=[ThinkingBlock(text="想", signature="sig-1"), TextBlock("你好")],
        ),
        usage=Usage(input_tokens=3, output_tokens=2),
    )

    entries = store.read_entries(1, sid)
    assert [e["type"] for e in entries] == ["message", "message", "usage"]
    assert entries[0]["parentId"] is None
    assert entries[1]["parentId"] == entries[0]["id"]
    assert entries[2]["parentId"] == entries[1]["id"]
    # thinking 签名原样落盘（重放凭据）
    assert entries[1]["message"]["content"] == [
        {"type": "thinking", "thinking": "想", "signature": "sig-1"},
        {"type": "text", "text": "你好"},
    ]
    assert entries[2]["usage"] == {
        "input_tokens": 3, "output_tokens": 2, "cache_read_tokens": 0, "cache_write_tokens": 0,
    }


def test_replay_rebuilds_context_from_active_branch(tmp_path) -> None:
    store = _store(tmp_path)
    sid = store.create_session(1)
    store.append_message(1, sid, Message(role="user", content=[TextBlock("第一问")]))
    store.append_message(1, sid, Message(role="assistant", content=[TextBlock("第一答")]))
    store.append_message(1, sid, Message(role="user", content=[TextBlock("第二问")]))

    replayed = store.replay_messages(1, sid)
    assert replayed == [
        Message(role="user", content=[TextBlock("第一问")]),
        Message(role="assistant", content=[TextBlock("第一答")]),
        Message(role="user", content=[TextBlock("第二问")]),
    ]


def test_replay_skips_usage_entries_and_unsigned_thinking(tmp_path) -> None:
    store = _store(tmp_path)
    sid = store.create_session(1)
    store.append_message(1, sid, Message(role="user", content=[TextBlock("q")]))
    # abort 半截消息：thinking 无签名 + stop_reason 标记
    store.append_message(
        1,
        sid,
        Message(
            role="assistant",
            content=[ThinkingBlock(text="截断的思考"), TextBlock("部分回答")],
        ),
        stop_reason="aborted",
    )

    entries = store.read_entries(1, sid)
    assert entries[-1].get("stop_reason") == "aborted"
    replayed = store.replay_messages(1, sid)
    # 无签名 thinking 块不能回放给 API——被跳过，正文保留
    assert replayed[-1].content == [TextBlock("部分回答")]


def test_sessions_are_isolated_per_user(tmp_path) -> None:
    store = _store(tmp_path)
    sid = store.create_session(user_id=1)
    store.append_message(1, sid, Message(role="user", content=[TextBlock("user1 的秘密")]))

    # user 2 看不到 user 1 的会话
    assert store.get_session(2, sid) is None
    assert store.read_entries(2, sid) == []
    assert store.replay_messages(2, sid) == []
    assert [m.id for m in store.list_sessions(2)] == []
    # user 1 自己可见
    assert store.get_session(1, sid) is not None
    assert len(store.read_entries(1, sid)) == 1


def test_list_sessions_orders_by_recency(tmp_path) -> None:
    import time

    store = _store(tmp_path)
    first = store.create_session(1)
    time.sleep(0.02)  # mtime 分辨率
    second = store.create_session(1)

    assert [m.id for m in store.list_sessions(1)] == [second, first]
    assert store.latest_session(1) == second


# ---- ticket 09：删除、分叉挂载与活跃分支 ----


def test_delete_session_removes_file(tmp_path) -> None:
    store = _store(tmp_path)
    sid = store.create_session(1)
    store.append_message(1, sid, Message(role="user", content=[TextBlock("x")]))

    assert store.delete_session(1, sid) is True
    assert store.get_session(1, sid) is None
    assert store.read_entries(1, sid) == []
    assert store.delete_session(1, sid) is False  # 幂等安全


def test_branch_mounts_new_message_under_given_parent(tmp_path) -> None:
    """分叉：parent_entry_id 指定后新消息挂该条目下（树形，旧分支保留）。"""
    store = _store(tmp_path)
    sid = store.create_session(1)
    e1 = store.append_message(1, sid, Message(role="user", content=[TextBlock("第一问")]))
    store.append_message(1, sid, Message(role="assistant", content=[TextBlock("第一答")]))
    # 从第一问重新分叉
    branch = store.append_message(
        1, sid, Message(role="user", content=[TextBlock("换个问法")]), parent_entry_id=e1
    )

    entries = store.read_entries(1, sid)
    assert [e["message"]["content"][0]["text"] for e in entries if e["type"] == "message"] == [
        "第一问", "第一答", "换个问法",
    ]
    assert entries[-1]["parentId"] == e1  # 挂在第一问下，而非旧分支尾


def test_read_active_branch_returns_only_current_path(tmp_path) -> None:
    """分叉后活跃分支 = 最后一条 message 的祖先链；旧分支与 usage 随消息各归其位。"""
    store = _store(tmp_path)
    sid = store.create_session(1)
    e1 = store.append_message(1, sid, Message(role="user", content=[TextBlock("第一问")]))
    e2 = store.append_message(
        1, sid, Message(role="assistant", content=[TextBlock("第一答")]),
        usage=Usage(input_tokens=3, output_tokens=2),
    )
    store.append_message(1, sid, Message(role="user", content=[TextBlock("第二问")]))
    # 从第一答分叉再对话
    store.append_message(
        1, sid, Message(role="user", content=[TextBlock("换个问法")]), parent_entry_id=e2
    )

    branch = store.read_active_branch(1, sid)
    texts = [
        e["message"]["content"][0]["text"]
        for e in branch
        if e["type"] == "message"
    ]
    # 走新分支：第一问 → 第一答 → 换个问法；旧分支尾（第二问）不出现
    assert texts == ["第一问", "第一答", "换个问法"]
    # usage 子条目随其消息返回
    assert [e["type"] for e in branch] == ["message", "message", "usage", "message"]
    assert branch[2]["usage"]["output_tokens"] == 2
    # 旧分支数据仍在全量条目里（树形历史不丢）
    assert len(store.read_entries(1, sid)) == 5


def test_read_active_branch_linear_session_unchanged(tmp_path) -> None:
    store = _store(tmp_path)
    sid = store.create_session(1)
    store.append_message(1, sid, Message(role="user", content=[TextBlock("q")]))
    store.append_message(1, sid, Message(role="assistant", content=[TextBlock("a")]))
    # 注记字段（票 17）是增量行为——线性会话下断言条目序列不变
    assert [e["id"] for e in store.read_active_branch(1, sid)] == [
        e["id"] for e in store.read_entries(1, sid)
    ]


def test_read_branch_annotates_fork_points(tmp_path) -> None:
    """票 17：链上 message/compaction 条目带分支注记——父的 conversation 子代
    （usage/model_change 不计入）中自己的位置与兄弟 id 列表。"""
    store = _store(tmp_path)
    sid = store.create_session(1)
    store.append_message(1, sid, Message(role="user", content=[TextBlock("第一问")]))
    e2 = store.append_message(
        1,
        sid,
        Message(role="assistant", content=[TextBlock("第一答")]),
        usage=Usage(input_tokens=3, output_tokens=2),
    )
    e3 = store.append_message(1, sid, Message(role="user", content=[TextBlock("第二问")]))
    e4 = store.append_message(
        1, sid, Message(role="user", content=[TextBlock("换个问法")]), parent_entry_id=e2
    )

    branch = store.read_branch(1, sid)
    msgs = {e["message"]["content"][0]["text"]: e for e in branch if e["type"] == "message"}
    assert msgs["第一问"]["branch_total"] == 1  # 根，无分叉
    assert msgs["第一答"]["branch_total"] == 1  # usage 子条目不算分支
    assert msgs["换个问法"]["branch_index"] == 1
    assert msgs["换个问法"]["branch_total"] == 2
    assert msgs["换个问法"]["siblings"] == [e3, e4]
    # 旧分支（第二问）不在活跃链上——其注记从它自己的分支视图读取
    old = store.read_branch(1, sid, e3)
    old_msgs = {e["message"]["content"][0]["text"]: e for e in old if e["type"] == "message"}
    assert old_msgs["第二问"]["branch_index"] == 0
    assert old_msgs["第二问"]["branch_total"] == 2
    assert old_msgs["第二问"]["siblings"] == [e3, e4]
    # usage 子条目不携带注记
    usage_entries = [e for e in branch if e["type"] == "usage"]
    assert usage_entries and all("branch_total" not in e for e in usage_entries)


# ---- ticket 17：任意分支回放与分支注记 ----


def test_read_branch_default_matches_active_branch(tmp_path) -> None:
    """read_branch(None) ≡ read_active_branch：同一套行走逻辑（重构安全网）。"""
    store = _store(tmp_path)
    # 线性树
    sid = store.create_session(1)
    store.append_message(1, sid, Message(role="user", content=[TextBlock("q")]))
    store.append_message(1, sid, Message(role="assistant", content=[TextBlock("a")]))
    assert store.read_branch(1, sid) == store.read_active_branch(1, sid)
    # 分叉树
    sid2 = store.create_session(1)
    e1 = store.append_message(1, sid2, Message(role="user", content=[TextBlock("第一问")]))
    store.append_message(1, sid2, Message(role="assistant", content=[TextBlock("第一答")]))
    store.append_message(1, sid2, Message(role="user", content=[TextBlock("第二问")]))
    store.append_message(
        1, sid2, Message(role="user", content=[TextBlock("换个问法")]), parent_entry_id=e1
    )
    assert store.read_branch(1, sid2) == store.read_active_branch(1, sid2)


def test_read_branch_descends_to_subtree_leaf(tmp_path) -> None:
    """票 17：entry_id 指向树腰节点时，沿最后子代降到子树叶再回溯——
    切换分支视图看到的是该分支的最新延续，而非分叉瞬间的截面。"""
    store = _store(tmp_path)
    sid = store.create_session(1)
    e1 = store.append_message(1, sid, Message(role="user", content=[TextBlock("问1")]))
    store.append_message(1, sid, Message(role="assistant", content=[TextBlock("答A")]))
    store.append_message(1, sid, Message(role="user", content=[TextBlock("问2")]))
    store.append_message(
        1, sid, Message(role="assistant", content=[TextBlock("答B")]), parent_entry_id=e1
    )

    def texts(entries: list[dict]) -> list[str]:
        return [e["message"]["content"][0]["text"] for e in entries if e["type"] == "message"]

    # 活跃分支（None）不受影响：走文件最后条目（答B）
    assert texts(store.read_branch(1, sid)) == ["问1", "答B"]
    # 从根看：子树叶 = 答B（e1 的最后子代）
    assert texts(store.read_branch(1, sid, e1)) == ["问1", "答B"]
    # 叶自身：就是它
    e4 = store.read_entries(1, sid)[-1]["id"]
    assert texts(store.read_branch(1, sid, e4)) == ["问1", "答B"]
    # 旧分支腰节点 e2（答A）的子树：降到最后子代问2
    e2 = next(e["id"] for e in store.read_entries(1, sid) if e["message"]["content"][0]["text"] == "答A")
    assert texts(store.read_branch(1, sid, e2)) == ["问1", "答A", "问2"]


# ---- ticket 23：tool_result 富块（text/image）----


def test_tool_result_blocks_round_trip(tmp_path) -> None:
    """blocks 落盘与回读；存量无 blocks 键的旧条目回读 None（向后兼容）。"""
    store = _store(tmp_path)
    sid = store.create_session(1)
    store.append_message(
        1,
        sid,
        Message(
            role="toolResult",
            content=[
                ToolResultBlock(
                    tool_use_id="t1",
                    content="[图片: chart.png]",
                    blocks=[{"type": "image", "media_type": "image/png", "data": "aGk="}],
                )
            ],
        ),
    )

    entries = store.read_entries(1, sid)
    assert entries[0]["message"]["content"][0]["blocks"] == [
        {"type": "image", "media_type": "image/png", "data": "aGk="}
    ]
    replayed = store.replay_messages(1, sid)
    assert replayed[0].content[0].blocks == [
        {"type": "image", "media_type": "image/png", "data": "aGk="}
    ]

    # 存量兼容：手写旧格式（无 blocks 键）
    raw = {
        "type": "message", "id": "e_old", "parentId": None, "timestamp": "t",
        "message": {"role": "toolResult", "content": [{"type": "tool_result", "tool_use_id": "t0", "content": "纯文本"}]},
    }
    path = next((tmp_path / "sessions" / "1").glob("*.jsonl"))
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(raw, ensure_ascii=False) + "\n")
    old = store.replay_messages(1, sid)[-1]
    assert old.content[0].content == "纯文本"
    assert old.content[0].blocks is None


# ---- 重命名 / 置顶（header 元数据） ----


def test_rename_overrides_auto_title(tmp_path) -> None:
    store = _store(tmp_path)
    sid = store.create_session(1)
    store.append_message(1, sid, Message(role="user", content=[TextBlock("自动标题来源")]))
    assert store.list_sessions(1)[0].title == "自动标题来源"  # 自动标题

    assert store.update_session_header(1, sid, title="我的研究项目") is True
    assert store.list_sessions(1)[0].title == "我的研究项目"  # 显式重命名覆盖
    # 树条目不受影响
    assert len(store.read_entries(1, sid)) == 1


def test_clear_title_falls_back_to_auto(tmp_path) -> None:
    store = _store(tmp_path)
    sid = store.create_session(1)
    store.append_message(1, sid, Message(role="user", content=[TextBlock("自动标题")]))
    store.update_session_header(1, sid, title="改名")
    store.update_session_header(1, sid, title="")
    assert store.list_sessions(1)[0].title == "自动标题"


def test_pinned_sessions_sort_first(tmp_path) -> None:
    import time

    store = _store(tmp_path)
    a = store.create_session(1)
    time.sleep(0.02)
    b = store.create_session(1)
    time.sleep(0.02)
    c = store.create_session(1)
    # 默认按最近更新：c, b, a；置顶 a 后：a, c, b
    assert store.update_session_header(1, a, pinned=True) is True
    assert [m.id for m in store.list_sessions(1)] == [a, c, b]
    meta = next(m for m in store.list_sessions(1) if m.id == a)
    assert meta.pinned is True
    # 取消置顶恢复
    store.update_session_header(1, a, pinned=False)
    assert [m.id for m in store.list_sessions(1)] == [c, b, a]


def test_update_header_missing_session(tmp_path) -> None:
    store = _store(tmp_path)
    assert store.update_session_header(1, "ghost", title="x") is False
