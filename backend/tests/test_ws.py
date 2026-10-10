"""Seam B3：WS 通道——token 握手与 echo 命令。"""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from super_agent.app.chat import ChatService
from super_agent.server.main import create_app
from test_chat import ScriptedProvider


def _client_with_user(tmp_path) -> tuple[TestClient, str]:
    app = create_app(
        db_path=tmp_path / "test.db",
        chat=ChatService(ScriptedProvider()),  # 无 store：不产生 session_info 帧
        sessions_dir=tmp_path / "sessions",
    )
    client = TestClient(app)
    client.post("/api/auth/register", json={"username": "u1", "password": "pw123456"})
    login = client.post("/api/auth/login", json={"username": "u1", "password": "pw123456"})
    return client, login.json()["token"]


def test_ws_echo_with_valid_token(tmp_path) -> None:
    client, token = _client_with_user(tmp_path)
    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.send_json({"cmd": "echo", "req_id": "1", "text": "hello"})
        assert ws.receive_json() == {"type": "response", "req_id": "1", "text": "hello"}


def test_ws_rejects_invalid_token(tmp_path) -> None:
    client, _ = _client_with_user(tmp_path)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws?token=not-a-real-token") as ws:
            ws.receive_json()
    assert exc_info.value.code == 4401
