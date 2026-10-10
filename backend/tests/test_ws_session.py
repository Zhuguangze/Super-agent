"""Seam B6：WS 会话通路——session_info 事件、对话落盘、跨 prompt 重放、new_session。"""

from fastapi.testclient import TestClient

from super_agent.ai.types import Message, MessageEnd, MessageStart, TextBlock, TextDelta, Usage
from super_agent.app.chat import ChatService
from super_agent.app.session_store import SessionStore
from super_agent.server.main import create_app
from test_chat import ScriptedProvider


def _client_as(tmp_path, username: str) -> tuple[TestClient, str, SessionStore]:
    store = SessionStore(tmp_path / "sessions")
    app = create_app(
        db_path=tmp_path / "test.db",
        chat=ChatService(ScriptedProvider(_reply_script("回复")), store, default_model="claude-opus-5"),
        sessions_dir=tmp_path / "sessions",
    )
    client = TestClient(app)
    client.post("/api/auth/register", json={"username": username, "password": "pw123456"})
    login = client.post("/api/auth/login", json={"username": username, "password": "pw123456"})
    return client, login.json()["token"], store


def _reply_script(text: str) -> list:
    return [
        MessageStart(model="claude-opus-5", usage=Usage(input_tokens=3)),
        TextDelta(text),
        MessageEnd(
            stop_reason="end_turn",
            usage=Usage(input_tokens=3, output_tokens=2),
            message=Message(role="assistant", content=[TextBlock(text)]),
        ),
    ]


def _drain_to_agent_end(ws) -> None:
    """排空一轮 run 的剩余事件帧（message_end 之后还有 turn_end/agent_end）。"""
    while ws.receive_json().get("type") != "agent_end":
        pass


def test_connection_announces_current_session(tmp_path) -> None:
    client, token, store = _client_as(tmp_path, "u1")
    sid = store.create_session(user_id=1)

    with client.websocket_connect(f"/ws?token={token}") as ws:
        announced = ws.receive_json()
        assert announced["session_id"] == sid and announced["model"] == "claude-opus-5"


def test_prompt_persists_full_exchange(tmp_path) -> None:
    client, token, store = _client_as(tmp_path, "u1")

    with client.websocket_connect(f"/ws?token={token}") as ws:
        session_id = ws.receive_json()["session_id"]
        ws.send_json({"cmd": "prompt", "req_id": "1", "text": "第一问"})
        assert ws.receive_json()["ok"] is True
        while ws.receive_json().get("type") != "message_end":
            pass

    entries = store.read_entries(1, session_id)
    assert [e["type"] for e in entries] == ["message", "message", "usage"]
    assert entries[0]["message"] == {"role": "user", "content": [{"type": "text", "text": "第一问"}]}
    assert entries[1]["message"]["content"] == [{"type": "text", "text": "回复"}]
    assert entries[1]["stop_reason"] == "end_turn"
    assert entries[2]["usage"]["output_tokens"] == 2
    # 树形链接完整
    assert entries[1]["parentId"] == entries[0]["id"]
    assert entries[2]["parentId"] == entries[1]["id"]


def test_second_prompt_replays_history_into_context(tmp_path) -> None:
    client, token, _ = _client_as(tmp_path, "u1")
    provider = client.app.state.chat._provider

    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.receive_json()  # session_info
        ws.send_json({"cmd": "prompt", "req_id": "1", "text": "第一问"})
        assert ws.receive_json()["ok"] is True
        _drain_to_agent_end(ws)
        ws.send_json({"cmd": "prompt", "req_id": "2", "text": "第二问"})
        assert ws.receive_json()["ok"] is True
        _drain_to_agent_end(ws)

    # 第二轮上下文 = 第一轮完整对话 + 新消息（重放重建上下文，ADR-0002）
    assert [m.text() for m in provider.seen_messages] == ["第一问", "回复", "第二问"]
    assert provider.seen_messages[-1].role == "user"


def test_new_session_command_starts_fresh_session(tmp_path) -> None:
    client, token, store = _client_as(tmp_path, "u1")

    with client.websocket_connect(f"/ws?token={token}") as ws:
        first_sid = ws.receive_json()["session_id"]
        ws.send_json({"cmd": "new_session", "req_id": "1"})
        resp = ws.receive_json()
        second_sid = resp["session_id"]
        assert resp["ok"] is True
        assert ws.receive_json()["session_id"] == second_sid
        assert second_sid != first_sid
        # 新会话只有 header，无条目
        assert store.read_entries(1, second_sid) == []


# ---- ticket 09：switch_session 与 branch ----


def test_switch_session_moves_subsequent_prompts(tmp_path) -> None:
    """切换会话后，新 prompt 落到目标会话（实时状态同步，无串台）。"""
    client, token, store = _client_as(tmp_path, "u1")

    with client.websocket_connect(f"/ws?token={token}") as ws:
        first_sid = ws.receive_json()["session_id"]
        second_sid = store.create_session(user_id=1)
        ws.send_json({"cmd": "switch_session", "req_id": "1", "session_id": second_sid})
        resp = ws.receive_json()
        assert resp == {"type": "response", "req_id": "1", "ok": True, "session_id": second_sid}
        assert ws.receive_json()["session_id"] == second_sid

        # 第二会话里对话——消息必须落到第二会话
        ws.send_json({"cmd": "prompt", "req_id": "2", "text": "在新会话里问"})
        assert ws.receive_json()["ok"] is True
        _drain_to_agent_end(ws)

    assert [e["type"] for e in store.read_entries(1, first_sid)] == []
    new_entries = store.read_entries(1, second_sid)
    assert new_entries[0]["message"]["content"][0]["text"] == "在新会话里问"


def test_switch_session_rejects_unknown_and_foreign(tmp_path) -> None:
    client, token, store = _client_as(tmp_path, "u1")

    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.receive_json()  # session_info
        ws.send_json({"cmd": "switch_session", "req_id": "1", "session_id": "nope"})
        assert ws.receive_json() == {
            "type": "response", "req_id": "1", "ok": False, "error": "session_not_found",
        }
        other = store.create_session(user_id=2)  # 他人会话
        ws.send_json({"cmd": "switch_session", "req_id": "2", "session_id": other})
        assert ws.receive_json()["ok"] is False


def test_branch_command_forks_conversation(tmp_path) -> None:
    """branch {entry_id} 后新对话挂该条目下；重放走新分支（ADR-0002）。"""
    client, token, store = _client_as(tmp_path, "u1")

    with client.websocket_connect(f"/ws?token={token}") as ws:
        sid = ws.receive_json()["session_id"]
        ws.send_json({"cmd": "prompt", "req_id": "1", "text": "第一问"})
        assert ws.receive_json()["ok"] is True
        _drain_to_agent_end(ws)

        entries = store.read_entries(1, sid)
        first_user_id = entries[0]["id"]  # "第一问" 条目
        ws.send_json({"cmd": "branch", "req_id": "2", "entry_id": first_user_id})
        assert ws.receive_json() == {"type": "response", "req_id": "2", "ok": True}

        # 分叉后继续对话
        ws.send_json({"cmd": "prompt", "req_id": "3", "text": "换个问法"})
        assert ws.receive_json()["ok"] is True
        _drain_to_agent_end(ws)

    all_entries = store.read_entries(1, sid)
    messages = [e for e in all_entries if e["type"] == "message"]
    # 树形：旧分支（第一问→回复）+ 新分支（换个问法→回复）都落盘
    texts = [e["message"]["content"][0]["text"] for e in messages]
    assert texts == ["第一问", "回复", "换个问法", "回复"]
    # 新分支的 user 消息挂在"第一问"下
    assert messages[2]["parentId"] == first_user_id
    # 活跃分支 = 新路径（最后一条 message 回溯）
    branch_texts = [
        e["message"]["content"][0]["text"]
        for e in store.read_active_branch(1, sid)
        if e["type"] == "message"
    ]
    assert branch_texts == ["第一问", "换个问法", "回复"]


def test_branch_unknown_entry_rejected(tmp_path) -> None:
    client, token, _ = _client_as(tmp_path, "u1")

    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.receive_json()  # session_info
        ws.send_json({"cmd": "branch", "req_id": "1", "entry_id": "e_ghost"})
        assert ws.receive_json() == {
            "type": "response", "req_id": "1", "ok": False, "error": "entry_not_found",
        }
