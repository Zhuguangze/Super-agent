"""ticket 10：Compaction——压缩条目、摘要续接重放、手动与自动触发。"""

from super_agent.ai.provider import MODELS, Model, Provider
from super_agent.ai.types import (
    Message,
    MessageEnd,
    MessageStart,
    TextBlock,
    TextDelta,
    Usage,
)
from super_agent.app.chat import ChatService
from super_agent.app.compaction import auto_compact_threshold, transcribe
from super_agent.app.session_store import SUMMARY_LEAD, SessionStore
from test_chat import _collector, MultiTurnProvider
from test_extensions import _write_extension


def _store(tmp_path) -> SessionStore:
    return SessionStore(tmp_path / "sessions")


def _seed(store: SessionStore, sid: str, texts: list[str]) -> list[str]:
    """写入 user/assistant 交替消息，返回 message 条目 ids。"""
    ids = []
    for i, text in enumerate(texts):
        role = "user" if i % 2 == 0 else "assistant"
        ids.append(store.append_message(1, sid, Message(role=role, content=[TextBlock(text)])))
    return ids


# ---- store 层：摘要续接重放 ----


def test_replay_after_compaction_starts_from_summary(tmp_path) -> None:
    """压缩后重放 = 摘要合成消息 + 保留段原文（摘要处续接）。"""
    store = _store(tmp_path)
    sid = store.create_session(1)
    ids = _seed(store, sid, [f"消息{i}" for i in range(10)])

    store.append_compaction(1, sid, summary="早期对话要点", first_kept_entry_id=ids[-2])

    replayed = store.replay_messages(1, sid)
    assert replayed[0].role == "user"
    assert replayed[0].text().startswith(SUMMARY_LEAD)
    assert "早期对话要点" in replayed[0].text()
    # 保留段 = 最后 2 条 message 原文
    assert [m.text() for m in replayed[1:]] == ["消息8", "消息9"]


def test_replay_after_compaction_without_kept_segment(tmp_path) -> None:
    store = _store(tmp_path)
    sid = store.create_session(1)
    _seed(store, sid, ["一问", "一答"])
    store.append_compaction(1, sid, summary="全部内容摘要", first_kept_entry_id=None)

    replayed = store.replay_messages(1, sid)
    assert [m.text() for m in replayed] == [SUMMARY_LEAD + "全部内容摘要"]


def test_double_compaction_uses_latest_summary_only(tmp_path) -> None:
    """二次压缩：最近摘要生效，更早的 compaction 不再展开。"""
    store = _store(tmp_path)
    sid = store.create_session(1)
    ids = _seed(store, sid, [f"消息{i}" for i in range(10)])

    store.append_compaction(1, sid, summary="第一次摘要", first_kept_entry_id=ids[-2])
    # 压缩后再来几条消息，再次压缩（保留段更小）
    late = store.append_message(1, sid, Message(role="user", content=[TextBlock("新问")]))
    store.append_compaction(1, sid, summary="第二次摘要", first_kept_entry_id=late)

    replayed = store.replay_messages(1, sid)
    texts = [m.text() for m in replayed]
    # 链：第二次摘要 → 保留段（"新问"）；第一次摘要已被覆盖不重复出现
    assert texts == [SUMMARY_LEAD + "第二次摘要", "新问"]
    assert not any("第一次摘要" in t for t in texts)


def test_active_branch_includes_compaction_marker(tmp_path) -> None:
    """前端回放链含 compaction 条目（据以提示），usage 仍附着。"""
    store = _store(tmp_path)
    sid = store.create_session(1)
    ids = _seed(store, sid, ["一问", "一答"])
    store.append_compaction(1, sid, summary="摘要", first_kept_entry_id=ids[0])
    store.append_message(1, sid, Message(role="user", content=[TextBlock("续问")]))

    branch = store.read_active_branch(1, sid)
    assert [e["type"] for e in branch] == ["message", "message", "compaction", "message"]


def test_transcribe_covers_roles() -> None:
    transcript = transcribe(
        [
            Message(role="user", content=[TextBlock("你好")]),
            Message(role="assistant", content=[TextBlock("回答")]),
            Message(role="toolResult", content=[TextBlock("工具输出")]),
            Message(role="assistant", content=[]),
        ]
    )
    assert "[用户] 你好" in transcript
    assert "[助手] 回答" in transcript
    assert "[工具结果] 工具输出" in transcript


def test_transcribe_mentions_images_in_tool_results() -> None:
    """票 23：富块结果的转录标注图片存在的事实——摘要不丢图。"""
    from super_agent.ai.types import ToolResultBlock

    transcript = transcribe(
        [
            Message(
                role="toolResult",
                content=[
                    ToolResultBlock(
                        tool_use_id="t1",
                        content="[图片: chart.png]",
                        blocks=[
                            {"type": "image", "media_type": "image/png", "data": "aGk="},
                            {"type": "image", "media_type": "image/jpeg", "data": "aGk="},
                        ],
                    ),
                    TextBlock("工具输出文字"),
                ],
            )
        ]
    )
    assert "含 2 张图片" in transcript


# ---- app 层：手动压缩 ----


async def test_manual_compact_session(tmp_path) -> None:
    """手动 compact：LLM 生成摘要 → compaction 条目落盘 → 事件可见 → 下一 run 从摘要续接。"""
    store = _store(tmp_path)
    sid = store.create_session(1)
    texts = [f"问答{i}" for i in range(10)]
    _seed(store, sid, texts)

    summary_provider = MultiTurnProvider(
        [
            [
                MessageStart(model="m", usage=Usage(input_tokens=10)),
                TextDelta("早期是 5 轮问答"),
                MessageEnd(
                    stop_reason="end_turn",
                    usage=Usage(input_tokens=10, output_tokens=4),
                    message=Message(role="assistant", content=[TextBlock("早期是 5 轮问答")]),
                ),
            ],
            [
                MessageStart(model="m", usage=Usage(input_tokens=20)),
                MessageEnd(
                    stop_reason="end_turn",
                    usage=Usage(input_tokens=20, output_tokens=2),
                    message=Message(role="assistant", content=[TextBlock("好的")]),
                ),
            ],
        ]
    )
    chat = ChatService(summary_provider, store)
    sent: list[dict] = []
    compacted = await chat.compact_session(user_id=1, session_id=sid, on_event=_collector(sent))

    assert compacted is True
    assert [e["type"] for e in sent] == ["compaction_start", "compaction_end"]
    assert sent[-1]["compacted"] is True

    entry = next(e for e in store.read_entries(1, sid) if e["type"] == "compaction")
    assert entry["summary"] == "早期是 5 轮问答"

    # 下一 run 的上下文 = 摘要 + 保留段（最近 8 条原文）
    await chat.run_prompt("新问题", user_id=1, session_id=sid, on_event=_collector([]))
    ctx = summary_provider.contexts[1]
    assert ctx[0].text().startswith(SUMMARY_LEAD)
    assert "早期是 5 轮问答" in ctx[0].text()
    assert [m.text() for m in ctx[1:-1]] == [f"问答{i}" for i in range(2, 10)]
    assert ctx[-1].text() == "新问题"


async def test_manual_compact_skips_when_too_few_messages(tmp_path) -> None:
    store = _store(tmp_path)
    sid = store.create_session(1)
    _seed(store, sid, ["一问", "一答"])

    chat = ChatService(MultiTurnProvider([]), store)
    sent: list[dict] = []
    compacted = await chat.compact_session(user_id=1, session_id=sid, on_event=_collector(sent))

    assert compacted is False
    assert sent[-1]["type"] == "compaction_end"
    assert sent[-1]["compacted"] is False
    assert sent[-1]["reason"] == "消息不足"  # 失败必须带原因（Q7）
    assert all(e["type"] != "compaction" for e in store.read_entries(1, sid))


async def test_compact_failure_does_not_break_session(tmp_path) -> None:
    """摘要 LLM 调用失败 → compacted False，会话照常可用。"""

    class BoomProvider(Provider):
        id = "boom"

        async def stream(self, messages, model_id, *, system=None, effort="high", tools=None):
            raise RuntimeError("摘要服务不可用")
            yield  # pragma: no cover

    store = _store(tmp_path)
    sid = store.create_session(1)
    _seed(store, sid, [f"问答{i}" for i in range(10)])

    chat = ChatService(BoomProvider(), store)
    sent: list[dict] = []
    assert await chat.compact_session(user_id=1, session_id=sid, on_event=_collector(sent)) is False
    assert sent[-1]["compacted"] is False
    # 会话照常对话
    fallback = MultiTurnProvider(
        [
            [
                MessageStart(model="m", usage=Usage()),
                MessageEnd(
                    stop_reason="end_turn", usage=Usage(),
                    message=Message(role="assistant", content=[TextBlock("在")]),
                ),
            ]
        ]
    )
    chat2 = ChatService(fallback, store)
    await chat2.run_prompt("还在吗", user_id=1, session_id=sid, on_event=_collector([]))
    assert [m.text() for m in fallback.contexts[0]] == [f"问答{i}" for i in range(10)] + ["还在吗"]


# ---- app 层：自动触发 ----


async def test_auto_compact_on_input_tokens_threshold(tmp_path) -> None:
    """某轮真实 input_tokens 超阈值 → run 结束后自动压缩。"""
    store = _store(tmp_path)
    sid = store.create_session(1)
    _seed(store, sid, [f"问答{i}" for i in range(10)])

    # 阈值按模型上下文窗口推导，故须注册一个已知窗口的模型
    mid = "test-compact-model"
    MODELS[mid] = Model(
        id=mid, name=mid, provider="test", context_window=200_000, max_output_tokens=8_000
    )
    huge = auto_compact_threshold(200_000) + 1000
    provider = MultiTurnProvider(
        [
            [
                MessageStart(model="m", usage=Usage(input_tokens=huge)),
                MessageEnd(
                    stop_reason="end_turn",
                    usage=Usage(input_tokens=huge, output_tokens=2),
                    message=Message(role="assistant", content=[TextBlock("长会话回复")]),
                ),
            ],
            [
                MessageStart(model="m", usage=Usage(input_tokens=30)),
                TextDelta("压缩完成后的答复"),
                MessageEnd(
                    stop_reason="end_turn",
                    usage=Usage(input_tokens=30, output_tokens=3),
                    message=Message(role="assistant", content=[TextBlock("压缩完成后的答复")]),
                ),
            ],
        ]
    )
    sent: list[dict] = []
    try:
        await ChatService(provider, store).run_prompt(
            "继续聊", user_id=1, session_id=sid, on_event=_collector(sent), model=mid
        )
    finally:
        MODELS.pop(mid, None)

    # run 内自动压缩：事件 + compaction 条目
    types = [e["type"] for e in sent]
    assert "compaction_start" in types and "compaction_end" in types
    compactions = [e for e in store.read_entries(1, sid) if e["type"] == "compaction"]
    assert len(compactions) == 1


# ---- WS：compact 命令 ----


def _ws_reply_script() -> list:
    return [
        MessageStart(model="m", usage=Usage(input_tokens=3)),
        TextDelta("回"),
        MessageEnd(
            stop_reason="end_turn",
            usage=Usage(input_tokens=3, output_tokens=1),
            message=Message(role="assistant", content=[TextBlock("回")]),
        ),
    ]


def test_ws_compact_command(tmp_path) -> None:
    from fastapi.testclient import TestClient

    from super_agent.server.main import create_app

    store = SessionStore(tmp_path / "sessions")
    app = create_app(
        db_path=tmp_path / "t.db",
        chat=ChatService(MultiTurnProvider([_ws_reply_script(), _ws_reply_script()]), store),
        sessions_dir=tmp_path / "sessions",
    )
    client = TestClient(app)
    client.post("/api/auth/register", json={"username": "u1", "password": "pw123456"})
    token = client.post("/api/auth/login", json={"username": "u1", "password": "pw123456"}).json()["token"]

    with client.websocket_connect(f"/ws?token={token}") as ws:
        sid = ws.receive_json()["session_id"]
        # 先凑足消息（10 条 message 才有压缩意义）
        store_replies = MultiTurnProvider([_ws_reply_script()] * 12)
        # 直接经 store 造历史更省事：删掉上面 provider 依赖，改为 seed
        # （此处 provider 脚本未用尽不影响断言）
        ids = []
        for i in range(10):
            role = "user" if i % 2 == 0 else "assistant"
            ids.append(store.append_message(1, sid, Message(role=role, content=[TextBlock(f"问答{i}")])))
        ws.send_json({"cmd": "compact", "req_id": "1"})
        # compaction_start / compaction_end 事件先到，随后是 response
        events = []
        for _ in range(3):
            frame = ws.receive_json()
            if frame.get("type") in ("compaction_start", "compaction_end"):
                events.append(frame)
            elif frame.get("type") == "response":
                assert frame["compacted"] is True
                break
        assert [e["type"] for e in events] == ["compaction_start", "compaction_end"]
        assert any(e["type"] == "compaction" for e in store.read_entries(1, sid))


def test_ws_compact_uses_session_model_not_default(tmp_path) -> None:
    """手动压缩的摘要须由会话当前模型生成，而不是落到默认模型（与自动压缩路径一致）。"""
    from fastapi.testclient import TestClient

    from super_agent.server.main import create_app

    session_mid, default_mid = "test-session-model", "test-default-model"
    for mid in (session_mid, default_mid):
        MODELS[mid] = Model(
            id=mid, name=mid, provider="test", context_window=200_000, max_output_tokens=8_000
        )
    try:
        store = SessionStore(tmp_path / "sessions")
        provider = MultiTurnProvider([[TextDelta("摘要")]] * 4)
        chat = ChatService(provider, store, default_model=default_mid)
        app = create_app(
            db_path=tmp_path / "t.db", chat=chat, sessions_dir=tmp_path / "sessions"
        )
        client = TestClient(app)
        client.post("/api/auth/register", json={"username": "u1", "password": "pw123456"})
        token = client.post(
            "/api/auth/login", json={"username": "u1", "password": "pw123456"}
        ).json()["token"]

        with client.websocket_connect(f"/ws?token={token}") as ws:
            sid = ws.receive_json()["session_id"]
            for i in range(10):
                role = "user" if i % 2 == 0 else "assistant"
                store.append_message(
                    1, sid, Message(role=role, content=[TextBlock(f"问答{i}")])
                )
            assert chat.set_session_model(1, sid, session_mid) is True
            ws.send_json({"cmd": "compact", "req_id": "1"})
            for _ in range(3):
                frame = ws.receive_json()
                if frame.get("type") == "response":
                    assert frame["compacted"] is True
                    break

        # 关键断言：摘要器用的是会话模型，不是 default_model
        assert provider.seen_model == session_mid
    finally:
        for mid in (session_mid, default_mid):
            MODELS.pop(mid, None)


# ---- 阈值按窗口推导 + 压缩干预点 ----


class _BoomProvider(Provider):
    """摘要调用必炸——验证压缩失败的可见性。"""

    id = "boom"

    async def stream(self, messages, model_id, *, system=None, effort="high", tools=None):
        raise RuntimeError("摘要器炸了")
        yield TextDelta("unreachable")  # 使其成为异步生成器


def test_auto_compact_threshold_scales_with_context_window() -> None:
    """阈值随窗口缩放——写死 160K 时 128K 窗口的模型永远够不着，自动压缩是死代码。"""
    assert auto_compact_threshold(128_000) == 102_400
    assert auto_compact_threshold(1_000_000) == 800_000
    # 关键回归：阈值必须低于窗口本身，否则自动压缩永不触发
    assert auto_compact_threshold(128_000) < 128_000


async def test_manual_compact_passes_instructions_to_summarizer(tmp_path) -> None:
    """手动 compact 的 instructions 并入摘要系统提示（Claude Code 式 /compact focus on …）。"""
    store = _store(tmp_path)
    sid = store.create_session(1)
    _seed(store, sid, [f"问答{i}" for i in range(10)])

    provider = MultiTurnProvider([[TextDelta("摘要正文")]])
    ok = await ChatService(provider, store).compact_session(
        user_id=1, session_id=sid, on_event=_collector([]), instructions="聚焦认证重构"
    )

    assert ok is True
    assert provider.last_system is not None
    assert "聚焦认证重构" in provider.last_system


async def test_before_compaction_hook_can_block(tmp_path) -> None:
    """before_compaction 返回 {"block": True} → 本次压缩被阻止，摘要器根本不该被调用。"""
    store = _store(tmp_path)
    sid = store.create_session(1)
    _seed(store, sid, [f"问答{i}" for i in range(10)])

    ext_dir = tmp_path / "extensions"
    _write_extension(
        ext_dir,
        "blocker.py",
        "def register(api):\n"
        "    async def block(payload):\n"
        "        return {'block': True}\n"
        "    api.on('before_compaction', block)\n",
    )

    provider = MultiTurnProvider([[TextDelta("不该被调用")]])
    chat = ChatService(provider, store, ext_global_dir=ext_dir)
    sent: list[dict] = []
    ok = await chat.compact_session(user_id=1, session_id=sid, on_event=_collector(sent))

    assert ok is False
    assert provider.last_system is None  # 摘要器未被调用
    assert sent[-1]["reason"] == "被扩展阻止"
    assert not [e for e in store.read_entries(1, sid) if e["type"] == "compaction"]


async def test_before_compaction_hook_injects_instructions(tmp_path) -> None:
    """before_compaction 返回 instructions → 并入摘要系统提示。"""
    store = _store(tmp_path)
    sid = store.create_session(1)
    _seed(store, sid, [f"问答{i}" for i in range(10)])

    ext_dir = tmp_path / "extensions"
    _write_extension(
        ext_dir,
        "focus.py",
        "def register(api):\n"
        "    async def focus(payload):\n"
        "        return {'instructions': '保留所有文件路径'}\n"
        "    api.on('before_compaction', focus)\n",
    )

    provider = MultiTurnProvider([[TextDelta("摘要正文")]])
    chat = ChatService(provider, store, ext_global_dir=ext_dir)
    ok = await chat.compact_session(user_id=1, session_id=sid, on_event=_collector([]))

    assert ok is True
    assert provider.last_system is not None
    assert "保留所有文件路径" in provider.last_system


async def test_compaction_failure_is_visible(tmp_path) -> None:
    """摘要器抛异常 → compaction_end 带原因 + 发 compaction_failed 扩展事件。"""
    store = _store(tmp_path)
    sid = store.create_session(1)
    _seed(store, sid, [f"问答{i}" for i in range(10)])

    log_file = tmp_path / "failed.log"
    ext_dir = tmp_path / "extensions"
    _write_extension(
        ext_dir,
        "watcher.py",
        "from pathlib import Path\n"
        f"LOG = Path({str(log_file)!r})\n"
        "def register(api):\n"
        "    def note(payload):\n"
        "        LOG.write_text(str(payload.get('reason')), encoding='utf-8')\n"
        "    api.on('compaction_failed', note)\n",
    )

    chat = ChatService(_BoomProvider(), store, ext_global_dir=ext_dir)
    sent: list[dict] = []
    ok = await chat.compact_session(user_id=1, session_id=sid, on_event=_collector(sent))

    assert ok is False
    assert sent[-1]["compacted"] is False
    assert "摘要器炸了" in sent[-1]["reason"]
    assert "摘要器炸了" in log_file.read_text(encoding="utf-8")
