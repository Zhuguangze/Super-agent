"""ticket 14：模型选择与 usage 统计——set_model/set_thinking_level/REST/聚合。"""

import json
import sys
from pathlib import Path

import pytest

from fastapi.testclient import TestClient

from super_agent.ai.provider import MODELS, Model
from super_agent.ai.types import Message, MessageEnd, MessageStart, TextBlock, Usage
from super_agent.app.chat import ChatService
from super_agent.app.session_store import SessionStore
from super_agent.server.main import create_app
from test_chat import MultiTurnProvider, _collector

# 模型注册表是动态的（零内置）——本文件涉及具体模型 id 的用例先注册测试模型
_TEST_MODELS = ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"]


@pytest.fixture()
def test_models():
    from super_agent.ai.provider import MODELS as M

    for mid in _TEST_MODELS:
        M[mid] = Model(id=mid, name=mid, provider="test", context_window=100_000, max_output_tokens=32_000)
    yield
    for mid in _TEST_MODELS:
        M.pop(mid, None)


def _reply_script(text: str = "回") -> list:
    return [
        [
            MessageStart(model="m", usage=Usage(input_tokens=3)),
            MessageEnd(
                stop_reason="end_turn",
                usage=Usage(input_tokens=3, output_tokens=2),
                message=Message(role="assistant", content=[TextBlock(text)]),
            ),
        ]
    ]


def _ws_client(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    provider = MultiTurnProvider(_reply_script() * 4)
    app = create_app(
        db_path=tmp_path / "t.db",
        chat=ChatService(provider, store, default_model="claude-opus-5"),
        sessions_dir=tmp_path / "sessions",
    )
    client = TestClient(app)
    client.post("/api/auth/register", json={"username": "u1", "password": "pw123456"})
    token = client.post("/api/auth/login", json={"username": "u1", "password": "pw123456"}).json()["token"]
    return client, token, store, provider


# ---- REST：模型列表与用量 ----


@pytest.mark.usefixtures("test_models")
def test_rest_models(tmp_path) -> None:
    client, token, _, _ = _ws_client(tmp_path)
    resp = client.get("/api/models", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    ids = [m["id"] for m in resp.json()["models"]]
    assert "claude-opus-5" in ids and "claude-haiku-4-5" in ids
    assert all("name" in m for m in resp.json()["models"])


def test_rest_usage_aggregates_all_sessions(tmp_path) -> None:
    client, token, store, _ = _ws_client(tmp_path)
    # 两个会话各留 usage
    s1 = store.create_session(user_id=1)
    s2 = store.create_session(user_id=1)
    store.append_message(
        1, s1, Message(role="assistant", content=[TextBlock("a")]),
        usage=Usage(input_tokens=100, output_tokens=50),
    )
    store.append_message(
        1, s2, Message(role="assistant", content=[TextBlock("b")]),
        usage=Usage(input_tokens=30, output_tokens=20),
    )
    resp = client.get("/api/usage", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    usage = resp.json()["usage"]
    assert usage["input_tokens"] == 130 and usage["output_tokens"] == 70
    # 他人会话不计入
    other = store.create_session(user_id=2)
    store.append_message(
        2, other, Message(role="assistant", content=[TextBlock("c")]),
        usage=Usage(input_tokens=999, output_tokens=999),
    )
    usage2 = client.get("/api/usage", headers={"Authorization": f"Bearer {token}"}).json()["usage"]
    assert usage2["input_tokens"] == 130


# ---- WS：set_model / set_thinking_level ----


@pytest.mark.usefixtures("test_models")
def test_set_model_takes_effect_next_run(tmp_path) -> None:
    client, token, store, provider = _ws_client(tmp_path)

    with client.websocket_connect(f"/ws?token={token}") as ws:
        sid = ws.receive_json()["session_id"]
        # 切到 haiku
        ws.send_json({"cmd": "set_model", "req_id": "1", "model": "claude-haiku-4-5"})
        assert ws.receive_json() == {
            "type": "response", "req_id": "1", "ok": True, "model": "claude-haiku-4-5",
        }
        # model_change 条目落盘
        changes = [e for e in store.read_entries(1, sid) if e["type"] == "model_change"]
        assert changes and changes[-1]["model"] == "claude-haiku-4-5"

        # 下一 run 用新模型
        ws.send_json({"cmd": "prompt", "req_id": "2", "text": "hi"})
        assert ws.receive_json()["ok"] is True
        while ws.receive_json().get("type") != "agent_end":
            pass
    assert provider.seen_model == "claude-haiku-4-5"


@pytest.mark.usefixtures("test_models")
def test_set_model_rejects_unknown(tmp_path) -> None:
    client, token, _, _ = _ws_client(tmp_path)
    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.receive_json()  # session_info
        ws.send_json({"cmd": "set_model", "req_id": "1", "model": "gpt-99"})
        assert ws.receive_json() == {"type": "response", "req_id": "1", "ok": False, "error": "unknown_model"}


@pytest.mark.usefixtures("test_models")
def test_set_thinking_level_maps_to_effort(tmp_path) -> None:
    client, token, _, provider = _ws_client(tmp_path)
    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.receive_json()  # session_info
        ws.send_json({"cmd": "set_thinking_level", "req_id": "1", "level": "max"})
        assert ws.receive_json() == {"type": "response", "req_id": "1", "ok": True, "level": "max"}
        ws.send_json({"cmd": "set_thinking_level", "req_id": "2", "level": "bogus"})
        assert ws.receive_json() == {"type": "response", "req_id": "2", "ok": False, "error": "unknown_level"}

        ws.send_json({"cmd": "prompt", "req_id": "3", "text": "hi"})
        assert ws.receive_json()["ok"] is True
        while ws.receive_json().get("type") != "agent_end":
            pass
    assert provider.seen_effort == "max"


@pytest.mark.usefixtures("test_models")
def test_model_switch_isolated_per_session(tmp_path) -> None:
    client, token, store, provider = _ws_client(tmp_path)
    with client.websocket_connect(f"/ws?token={token}") as ws:
        sid_a = ws.receive_json()["session_id"]
        sid_b = store.create_session(user_id=1)
        # 在 A 会话切到 sonnet
        ws.send_json({"cmd": "set_model", "req_id": "1", "model": "claude-sonnet-5"})
        assert ws.receive_json()["ok"] is True
        # 切到 B（从未设置过模型）→ 回默认
        ws.send_json({"cmd": "switch_session", "req_id": "2", "session_id": sid_b})
        while ws.receive_json().get("type") != "session_info":
            pass
        ws.send_json({"cmd": "prompt", "req_id": "3", "text": "hi"})
        assert ws.receive_json()["ok"] is True
        while ws.receive_json().get("type") != "agent_end":
            pass
    assert provider.seen_model == "claude-opus-5"


# ---- 未配置模型报错 + 新会话继承（Settings 视图需求） ----


class UnconfiguredProvider:
    """configured=False 的替身：模拟没配 API key。"""

    id = "unconfigured"

    def configured(self, model_id: str) -> bool:
        return False

    async def stream(self, messages, model_id, *, system=None, effort="high", tools=None):
        yield MessageStart(model=model_id, usage=Usage())
        yield MessageEnd(
            stop_reason="end_turn",
            usage=Usage(),
            message=Message(role="assistant", content=[TextBlock("不应到达")]),
        )


@pytest.mark.usefixtures("test_models")
async def test_unconfigured_model_errors_before_llm_call(tmp_path) -> None:
    """未配置凭证的模型：发送即报 model_not_configured，不发起 LLM 调用。"""
    from test_chat import _collector

    provider = UnconfiguredProvider()
    chat = ChatService(provider, SessionStore(tmp_path / "s"), default_model="claude-opus-5")
    sent: list[dict] = []
    await chat.run_prompt("hi", user_id=1, on_event=_collector(sent))

    assert sent == [
        {
            "type": "error",
            "code": "model_not_configured",
            "message": "API 配置错误：模型 claude-opus-5 未配置 API key——请在设置页添加凭证或切换到已配置的模型",
        }
    ]
    assert provider.stream  # 替身存在但从未被调用（无 message_start 事件在 sent 里）


@pytest.mark.usefixtures("test_models")
async def test_new_session_inherits_last_used_model(tmp_path) -> None:
    """新会话继承该用户上次使用的模型（而非固定默认）。"""
    from super_agent.ai.provider import Provider

    class RecordingProvider(Provider):
        id = "recording"
        seen: list = []

        def configured(self, model_id: str) -> bool:
            return True

        async def stream(self, messages, model_id, *, system=None, effort="high", tools=None):
            self.seen.append(model_id)
            yield MessageStart(model=model_id, usage=Usage())
            yield MessageEnd(
                stop_reason="end_turn",
                usage=Usage(input_tokens=1, output_tokens=1),
                message=Message(role="assistant", content=[TextBlock("ok")]),
            )

    store = SessionStore(tmp_path / "s")
    provider = RecordingProvider()
    chat = ChatService(provider, store, default_model="claude-opus-5")

    # 会话 A 切到 haiku 并对话一次
    sid_a = store.create_session(user_id=1)
    await chat.run_prompt("a", user_id=1, session_id=sid_a, on_event=_collector([]), model="claude-haiku-4-5")
    assert provider.seen == ["claude-haiku-4-5"]

    # 全新会话 B：不显式传 model → 继承上次（haiku）
    sid_b = store.create_session(user_id=1)
    await chat.run_prompt("b", user_id=1, session_id=sid_b, on_event=_collector([]))
    assert provider.seen == ["claude-haiku-4-5", "claude-haiku-4-5"]
    # 新会话 B 的模型视图也是 haiku
    assert chat.session_model(1, sid_b) == "claude-haiku-4-5"
