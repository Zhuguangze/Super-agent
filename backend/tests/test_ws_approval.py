"""Seam B7：WS 审批通路——approval_request 下发、approve 命令、幂等裁决（ADR-0001）。

注：TestClient 每次连接是独立 portal/事件循环，断线会终止整个 loop 的 run task，
无法端到端模拟"断线不杀 run + 重连重发"（uvicorn 单 loop 生产形态无此限制）。
重连重发语义由 test_approval.py 的 payload 同构单测覆盖。
"""

from fastapi.testclient import TestClient

from super_agent.ai.types import (
    Message,
    MessageEnd,
    MessageStart,
    TextBlock,
    ToolUseBlock,
    Usage,
)
from super_agent.app.approval import ApprovalService
from super_agent.app.chat import ChatService
from super_agent.app.session_store import SessionStore
from super_agent.app.tools import DEFAULT_ALLOWLIST
from super_agent.server.main import create_app
from test_chat import MultiTurnProvider


def _bash_script() -> list:
    return [
        [
            MessageStart(model="m", usage=Usage(input_tokens=5)),
            MessageEnd(
                stop_reason="tool_use",
                usage=Usage(input_tokens=5, output_tokens=4),
                message=Message(
                    role="assistant",
                    content=[ToolUseBlock(id="t1", name="bash", input={"command": "echo hi"})],
                ),
            ),
        ],
        [
            MessageStart(model="m", usage=Usage(input_tokens=9)),
            MessageEnd(
                stop_reason="end_turn",
                usage=Usage(input_tokens=9, output_tokens=6),
                message=Message(role="assistant", content=[TextBlock("done")]),
            ),
        ],
    ]


def _client(tmp_path, allowlist: set[str] | None = None):
    approval = ApprovalService(allowlist=DEFAULT_ALLOWLIST if allowlist is None else allowlist)
    store = SessionStore(tmp_path / "sessions")
    app = create_app(
        db_path=tmp_path / "test.db",
        chat=ChatService(
            MultiTurnProvider(_bash_script()),
            store,
            workspace_root=tmp_path / "ws",
            approval=approval,
        ),
        sessions_dir=tmp_path / "sessions",
    )
    client = TestClient(app)
    client.post("/api/auth/register", json={"username": "u1", "password": "pw123456"})
    login = client.post("/api/auth/login", json={"username": "u1", "password": "pw123456"})
    return client, login.json()["token"], approval


def test_approve_allow_then_bash_runs(tmp_path) -> None:
    client, token, approval = _client(tmp_path, allowlist=set())

    with client.websocket_connect(f"/ws?token={token}") as ws:
        session_id = ws.receive_json()["session_id"]
        ws.send_json({"cmd": "prompt", "req_id": "1", "text": "跑个命令"})
        assert ws.receive_json()["ok"] is True
        # 相位帧后到达审批请求
        request = None
        for _ in range(10):
            frame = ws.receive_json()
            if frame.get("type") == "approval_request":
                request = frame
                break
        assert request is not None and request["tool"] == "bash"

        ws.send_json({"cmd": "approve", "req_id": "2", "approval_id": request["approval_id"], "allow": True})
        resolved = None
        for _ in range(10):
            frame = ws.receive_json()
            if frame.get("type") == "response" and frame.get("req_id") == "2":
                assert frame["resolved"] is True
            if frame.get("type") == "approval_resolved":
                resolved = frame
                break
        assert resolved == {"type": "approval_resolved", "approval_id": request["approval_id"], "allow": True}

        # 放行后 bash 真执行，loop 走到 agent_end
        end = None
        for _ in range(20):
            frame = ws.receive_json()
            if frame.get("type") == "agent_end":
                end = frame
                break
        assert end is not None

    # 会话历史里留下真实执行结果（bash echo 的输出）
    entries = client.app.state.sessions.read_entries(1, session_id)
    tool_results = [e for e in entries if e.get("message", {}).get("role") == "toolResult"]
    assert any("hi" in str(e) for e in tool_results)


def test_approve_reject_backfills_error(tmp_path) -> None:
    client, token, _ = _client(tmp_path, allowlist=set())

    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.receive_json()  # session_info
        ws.send_json({"cmd": "prompt", "req_id": "1", "text": "跑个命令"})
        assert ws.receive_json()["ok"] is True
        request = None
        for _ in range(10):
            frame = ws.receive_json()
            if frame.get("type") == "approval_request":
                request = frame
                break
        ws.send_json({"cmd": "approve", "req_id": "2", "approval_id": request["approval_id"], "allow": False})
        # 拒绝 → is_error 结果回填，loop 继续
        while True:
            frame = ws.receive_json()
            if frame.get("type") == "tool_execution_end":
                assert frame["is_error"] is True
                assert "用户拒绝" in frame["content"]
                break


def test_approve_is_idempotent_per_approval_id(tmp_path) -> None:
    """同一 approval_id 二次裁决无效（resolved=False）——重发场景下不产生重复状态迁移。"""
    client, token, approval = _client(tmp_path, allowlist=set())

    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.receive_json()  # session_info
        ws.send_json({"cmd": "prompt", "req_id": "1", "text": "跑个命令"})
        assert ws.receive_json()["ok"] is True
        request = None
        for _ in range(10):
            frame = ws.receive_json()
            if frame.get("type") == "approval_request":
                request = frame
                break
        approval_id = request["approval_id"]
        ws.send_json({"cmd": "approve", "req_id": "2", "approval_id": approval_id, "allow": True})
        while True:
            frame = ws.receive_json()
            if frame.get("type") == "approval_resolved":
                break
        # 二次裁决同一 id：不再有挂起请求可命中
        ws.send_json({"cmd": "approve", "req_id": "3", "approval_id": approval_id, "allow": False})
        while True:
            frame = ws.receive_json()
            if frame.get("type") == "response" and frame.get("req_id") == "3":
                break
        assert frame["resolved"] is False
