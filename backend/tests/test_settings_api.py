"""Settings API——GET/PUT /api/settings 校验、落盘与热生效（白名单/并发/默认模型/自定义模型）。"""

from pathlib import Path

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from super_agent.ai.provider import MODELS, Model
from super_agent.ai.types import Message, MessageEnd, MessageStart, TextBlock, ToolUseBlock, Usage
from super_agent.app.approval import ApprovalService
from super_agent.app.chat import ChatService
from super_agent.app.tools import DEFAULT_ALLOWLIST
from super_agent.app.session_store import SessionStore
from super_agent.server.main import create_app
from test_chat import MultiTurnProvider

_TEST_MODELS = ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"]


@pytest.fixture()
def test_models():
    for mid in _TEST_MODELS:
        MODELS[mid] = Model(id=mid, name=mid, provider="test", context_window=100_000, max_output_tokens=32_000)
    yield
    for mid in _TEST_MODELS:
        MODELS.pop(mid, None)


def _bash_then_reply() -> list:
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
            MessageStart(model="m", usage=Usage()),
            MessageEnd(
                stop_reason="end_turn",
                usage=Usage(input_tokens=9, output_tokens=2),
                message=Message(role="assistant", content=[TextBlock("done")]),
            ),
        ],
    ]


def _client(tmp_path) -> tuple[TestClient, str, ChatService, SessionStore]:
    store = SessionStore(tmp_path / "sessions")
    provider = MultiTurnProvider(_bash_then_reply() * 3)
    approval = ApprovalService(allowlist=DEFAULT_ALLOWLIST)
    chat = ChatService(provider, store, workspace_root=tmp_path / "ws", approval=approval, default_model="claude-opus-5")
    app = create_app(
        db_path=tmp_path / "t.db",
        chat=chat,
        sessions_dir=tmp_path / "sessions",
        workspace_root=tmp_path / "ws",
        settings_path=tmp_path / "settings.json",
    )
    client = TestClient(app)
    client.post("/api/auth/register", json={"username": "u1", "password": "pw123456"})
    token = client.post("/api/auth/login", json={"username": "u1", "password": "pw123456"}).json()["token"]
    return client, token, chat, store


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.usefixtures("test_models")
def test_get_settings_returns_current_state(tmp_path) -> None:
    client, token, chat, _ = _client(tmp_path)
    resp = client.get("/api/settings", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["default_model"] == "claude-opus-5"
    assert body["max_concurrency"] == 4
    assert "bash" not in body["approval_allowlist"]  # 默认 bash 需审批
    assert body["custom_models"] == []


@pytest.mark.usefixtures("test_models")
def test_put_validates_and_rejects(tmp_path) -> None:
    client, token, _, _ = _client(tmp_path)
    bad_model = client.put(
        "/api/settings", headers=_auth(token), json={"default_model": "gpt-99"}
    )
    assert bad_model.status_code == 422
    bad_concurrency = client.put(
        "/api/settings", headers=_auth(token), json={"max_concurrency": 999}
    )
    assert bad_concurrency.status_code == 422
    bad_allowlist = client.put(
        "/api/settings", headers=_auth(token), json={"approval_allowlist": "read"}
    )
    assert bad_allowlist.status_code == 422
    bad_custom = client.put(
        "/api/settings", headers=_auth(token), json={"custom_models": ["x"]}
    )
    assert bad_custom.status_code == 422


@pytest.mark.usefixtures("test_models")
def test_put_hot_updates_allowlist_and_concurrency(tmp_path) -> None:
    client, token, chat, _ = _client(tmp_path)
    # bash 加入白名单 + 并发上限 2
    resp = client.put(
        "/api/settings",
        headers=_auth(token),
        json={"approval_allowlist": ["read", "bash"], "max_concurrency": 2},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert chat.approval.is_allowed("bash") is True  # 热生效
    assert chat.max_concurrency == 2
    # 落盘（settings.json 写入注入路径）
    import json

    saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert saved["approval_allowlist"] == ["bash", "read"]
    assert saved["max_concurrency"] == 2
    assert client.get("/api/settings", headers=_auth(token)).json()["max_concurrency"] == 2


@pytest.mark.usefixtures("test_models")
def test_put_default_model_applies_to_new_runs(tmp_path) -> None:
    client, token, chat, store = _client(tmp_path)
    resp = client.put(
        "/api/settings", headers=_auth(token), json={"default_model": "claude-haiku-4-5"}
    )
    assert resp.status_code == 200
    sid = store.create_session(user_id=1)
    assert chat.session_model(1, sid) == "claude-haiku-4-5"  # 未显式切换的会话跟默认


@pytest.mark.usefixtures("test_models")
def test_put_custom_models_registers_and_persists(tmp_path) -> None:
    client, token, _, _ = _client(tmp_path)
    resp = client.put(
        "/api/settings",
        headers=_auth(token),
        json={
            "custom_models": [
                {"id": "local-qwen", "name": "本地千问", "provider": "openai-compat", "base_url": "http://127.0.0.1:9999/v1", "api_key": "x"}
            ]
        },
    )
    assert resp.status_code == 200
    assert resp.json()["custom_model_errors"] == []
    # 模型列表出现自定义模型
    models = client.get("/api/models", headers=_auth(token)).json()["models"]
    assert any(m["id"] == "local-qwen" for m in models)
    # 读回设置包含它
    assert client.get("/api/settings", headers=_auth(token)).json()["custom_models"][0]["id"] == "local-qwen"
    # 清空后消失
    client.put("/api/settings", headers=_auth(token), json={"custom_models": []})
    models = client.get("/api/models", headers=_auth(token)).json()["models"]
    assert not any(m["id"] == "local-qwen" for m in models)
