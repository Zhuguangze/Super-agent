"""Seam B4：WS prompt/abort 命令——ChatService 事件流经 WS 到前端的通路。"""

import threading

from fastapi.testclient import TestClient

from super_agent.ai.provider import ProviderAuthError
from super_agent.ai.types import Message, MessageEnd, MessageStart, TextBlock, TextDelta, Usage
from super_agent.app.chat import ChatService
from super_agent.server.main import create_app
from test_chat import ScriptedProvider


def _client_with_chat(provider, tmp_path) -> tuple[TestClient, str]:
    app = create_app(
        db_path=tmp_path / "test.db",
        chat=ChatService(provider),  # 无 store：不产生 session_info 帧
        sessions_dir=tmp_path / "sessions",
    )
    client = TestClient(app)
    client.post("/api/auth/register", json={"username": "u1", "password": "pw123456"})
    login = client.post("/api/auth/login", json={"username": "u1", "password": "pw123456"})
    return client, login.json()["token"]


def test_prompt_streams_events_to_frontend(tmp_path) -> None:
    provider = ScriptedProvider(
        [
            MessageStart(model="claude-opus-5", usage=Usage(input_tokens=3)),
            TextDelta("你"),
            TextDelta("好"),
            MessageEnd(
                stop_reason="end_turn",
                usage=Usage(input_tokens=3, output_tokens=2),
                message=Message(role="assistant", content=[TextBlock("你好")]),
            ),
        ]
    )
    client, token = _client_with_chat(provider, tmp_path)

    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.send_json({"cmd": "prompt", "req_id": "1", "text": "打招呼"})
        assert ws.receive_json() == {"type": "response", "req_id": "1", "ok": True}
        assert ws.receive_json()["type"] == "agent_start"
        assert ws.receive_json()["type"] == "turn_start"
        assert ws.receive_json()["type"] == "message_start"
        first = ws.receive_json()
        second = ws.receive_json()
        assert [e["delta"]["text"] for e in (first, second)] == ["你", "好"]
        end = ws.receive_json()
        assert end["type"] == "message_end"
        assert end["stop_reason"] == "end_turn"

    # provider 收到了 system 与用户消息
    assert provider.seen_system is not None
    assert provider.seen_messages[-1].text() == "打招呼"


def test_prompt_command_carries_skill(tmp_path) -> None:
    """手动选技能（Claude Code 式）：WS prompt 命令的 skill 字段透传到 ChatService。"""
    from pathlib import Path as _Path

    skills_dir = tmp_path / "skills"
    (skills_dir / "deploy").mkdir(parents=True)
    (skills_dir / "deploy" / "SKILL.md").write_text(
        "---\nname: deploy\ndescription: 部署清单\n---\n1. 跑测试", encoding="utf-8"
    )
    provider = ScriptedProvider(
        [
            MessageStart(model="claude-opus-5", usage=Usage(input_tokens=3)),
            MessageEnd(
                stop_reason="end_turn",
                usage=Usage(input_tokens=3, output_tokens=2),
                message=Message(role="assistant", content=[TextBlock("好")]),
            ),
        ]
    )
    app = create_app(
        db_path=tmp_path / "test.db",
        chat=ChatService(provider, skills_dir=skills_dir),
        sessions_dir=tmp_path / "sessions",
    )
    client = TestClient(app)
    client.post("/api/auth/register", json={"username": "u1", "password": "pw123456"})
    token = client.post("/api/auth/login", json={"username": "u1", "password": "pw123456"}).json()["token"]

    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.send_json({"cmd": "prompt", "req_id": "1", "text": "开始部署", "skill": "deploy"})
        assert ws.receive_json() == {"type": "response", "req_id": "1", "ok": True}
        # 等流结束（收到 message_end 即可断言）
        while True:
            frame = ws.receive_json()
            if frame.get("type") == "message_end":
                break

    first_text = provider.seen_messages[0].content[0].text
    assert '<skill name="deploy">' in first_text
    assert "1. 跑测试" in first_text


def test_prompt_queued_while_agent_busy(tmp_path) -> None:
    gate = threading.Event()
    provider = ScriptedProvider([], gate=gate)
    client, token = _client_with_chat(provider, tmp_path)

    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.send_json({"cmd": "prompt", "req_id": "1", "text": "第一个"})
        assert ws.receive_json() == {"type": "response", "req_id": "1", "ok": True}
        assert ws.receive_json()["type"] == "agent_start"
        assert ws.receive_json()["type"] == "turn_start"
        ws.send_json({"cmd": "prompt", "req_id": "2", "text": "第二个"})
        # 运行中到达 → 进 steer 队列，回执 queued + 队列事件
        assert ws.receive_json() == {
            "type": "response", "req_id": "2", "ok": True, "queued": True,
        }
        assert ws.receive_json() == {"type": "queue_update", "pending": 1}
        gate.set()


def test_abort_mid_stream(tmp_path) -> None:
    gate = threading.Event()  # 第一道闸：确认 agent_busy 断言窗口（任务已启动）
    mid_gate = threading.Event()  # 第二道闸：流停在最后一个事件之前
    provider = ScriptedProvider(
        [
            MessageStart(model="claude-opus-5", usage=Usage()),
            TextDelta("部分输出"),
            MessageEnd(stop_reason="end_turn", usage=Usage(), message=Message(role="assistant", content=[])),
        ],
        gate=gate,
        mid_gate=mid_gate,
    )
    client, token = _client_with_chat(provider, tmp_path)

    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.send_json({"cmd": "prompt", "req_id": "1", "text": "长任务"})
        assert ws.receive_json()["ok"] is True
        assert ws.receive_json()["type"] == "agent_start"
        assert ws.receive_json()["type"] == "turn_start"
        ws.send_json({"cmd": "prompt", "req_id": "2", "text": "排队等当前任务"})
        queued = ws.receive_json()
        assert queued["ok"] is True and queued.get("queued") is True
        assert ws.receive_json()["type"] == "queue_update"
        gate.set()
        assert ws.receive_json()["type"] == "message_start"
        ws.receive_json()  # message_update "部分输出"
        ws.send_json({"cmd": "abort", "req_id": "3"})
        # abort 的 response 与补发的 message_end 来自两个 task，顺序不保证——按内容各取
        frames = [ws.receive_json(), ws.receive_json()]
        responses = [f for f in frames if f["type"] == "response"]
        ends = [f for f in frames if f["type"] == "message_end"]
        assert len(responses) == 1 and responses[0]["aborted"] is True
        assert len(ends) == 1 and ends[0]["stop_reason"] == "aborted"
        mid_gate.set()


def test_provider_auth_error_is_visible_to_user(tmp_path) -> None:
    provider = ScriptedProvider(error=ProviderAuthError("Anthropic API key 缺失或无效"))
    client, token = _client_with_chat(provider, tmp_path)

    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.send_json({"cmd": "prompt", "req_id": "1", "text": "hi"})
        assert ws.receive_json() == {"type": "response", "req_id": "1", "ok": True}
        assert ws.receive_json()["type"] == "agent_start"
        assert ws.receive_json()["type"] == "turn_start"
        error = ws.receive_json()
        assert error["type"] == "error"
        assert error["code"] == "provider_auth"
        assert "API key" in error["message"]


def test_empty_prompt_and_unknown_cmd(tmp_path) -> None:
    client, token = _client_with_chat(ScriptedProvider([]), tmp_path)

    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.send_json({"cmd": "prompt", "req_id": "1", "text": "   "})
        assert ws.receive_json()["error"] == "empty_prompt"
        ws.send_json({"cmd": "nope", "req_id": "2"})
        assert ws.receive_json()["error"] == "unknown_cmd"
