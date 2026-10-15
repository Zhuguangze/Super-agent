"""Seam B4：WS prompt/abort 命令——ChatService 事件流经 WS 到前端的通路。"""

import asyncio
import threading

from fastapi.testclient import TestClient

from super_agent.ai.provider import Provider, ProviderAuthError
from super_agent.ai.types import Message, MessageEnd, MessageStart, TextBlock, TextDelta, Usage
from super_agent.app.chat import ChatService
from super_agent.app.session_store import SessionStore
from super_agent.server.main import create_app
from test_chat import ScriptedProvider
from test_file_tools import _PNG_1PX


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
        assert ws.receive_json() == {"type": "queue_update", "pending": 1, "session_id": None}
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


def test_prompt_carries_images_to_provider(tmp_path) -> None:
    """票 01：带图 prompt——images 扁平字典 → 用户消息携带 ImageBlock；纯图空文本合法。"""
    import base64

    from super_agent.ai.types import ImageBlock

    png_b64 = base64.b64encode(_PNG_1PX).decode()
    provider = ScriptedProvider(
        [
            MessageStart(model="claude-opus-5", usage=Usage(input_tokens=3)),
            MessageEnd(
                stop_reason="end_turn",
                usage=Usage(input_tokens=3, output_tokens=2),
                message=Message(role="assistant", content=[TextBlock("看到图了")]),
            ),
        ]
    )
    client, token = _client_with_chat(provider, tmp_path)

    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.send_json(
            {
                "cmd": "prompt",
                "req_id": "1",
                "text": "",
                "images": [{"type": "image", "media_type": "image/png", "data": png_b64}],
            }
        )
        assert ws.receive_json() == {"type": "response", "req_id": "1", "ok": True}
        while True:
            frame = ws.receive_json()
            if frame.get("type") == "message_end":
                break

    last = provider.seen_messages[-1]
    assert last.role == "user"
    images = [b for b in last.content if isinstance(b, ImageBlock)]
    assert len(images) == 1
    assert images[0].media_type == "image/png"
    assert base64.b64decode(images[0].data) == _PNG_1PX


def test_prompt_rejects_invalid_images(tmp_path) -> None:
    """票 01：images 校验——数量/大小/类型/总量超限 → ok:False 且不发起 run。"""
    import base64 as _b64

    provider = ScriptedProvider([])
    client, token = _client_with_chat(provider, tmp_path)
    png_b64 = _b64.b64encode(_PNG_1PX).decode()
    big_b64 = _b64.b64encode(b"\0" * (5 * 1024 * 1024 + 1)).decode()  # 解码后 >5MB

    def _send(ws, req_id: str, images: list) -> dict:
        ws.send_json({"cmd": "prompt", "req_id": req_id, "text": "看图", "images": images})
        return ws.receive_json()

    with client.websocket_connect(f"/ws?token={token}") as ws:
        # 超 4 张
        resp = _send(ws, "1", [{"type": "image", "media_type": "image/png", "data": png_b64}] * 5)
        assert resp["ok"] is False and resp["error"] == "invalid_images"
        # 单张超 5MB
        resp = _send(ws, "2", [{"type": "image", "media_type": "image/png", "data": big_b64}])
        assert resp["ok"] is False and resp["error"] == "invalid_images"
        # 非白名单类型
        resp = _send(ws, "3", [{"type": "image", "media_type": "image/bmp", "data": png_b64}])
        assert resp["ok"] is False and resp["error"] == "invalid_images"
        # 结构非法（非 dict 项）
        resp = _send(ws, "4", ["not-a-dict"])
        assert resp["ok"] is False and resp["error"] == "invalid_images"

    assert getattr(provider, "seen_messages", []) == []  # 全部被拒,未发起 run


class _PairGatedProvider(Provider):
    """两次 stream 调用分别由 gate_a/gate_b 控制——并发会话测试用;记录每次收到的消息。"""

    id = "pair-gated"

    def __init__(self, gate_a: threading.Event, gate_b: threading.Event) -> None:
        self.gate_a = gate_a
        self.gate_b = gate_b
        self.calls: list[list[Message]] = []

    async def stream(self, messages, model_id, *, system=None, effort="high", tools=None):
        self.calls.append(list(messages))
        gate = self.gate_a if len(self.calls) == 1 else self.gate_b
        while not gate.is_set():
            await asyncio.sleep(0.01)
        yield MessageStart(model="m", usage=Usage(input_tokens=1))
        yield MessageEnd(
            stop_reason="end_turn",
            usage=Usage(input_tokens=1, output_tokens=1),
            message=Message(role="assistant", content=[TextBlock("done")]),
        )


def _client_with_sessions(provider, tmp_path) -> tuple[TestClient, str]:
    app = create_app(
        db_path=tmp_path / "test.db",
        chat=ChatService(provider, store=SessionStore(tmp_path / "sessions")),
        sessions_dir=tmp_path / "sessions",
    )
    client = TestClient(app)
    client.post("/api/auth/register", json={"username": "u1", "password": "pw123456"})
    login = client.post("/api/auth/login", json={"username": "u1", "password": "pw123456"})
    return client, login.json()["token"]


def test_two_sessions_run_concurrently(tmp_path) -> None:
    """并发会话：A 挂住 → 切 B 不再 agent_busy → 双 run 事件各带 session_id → 定点 abort 只杀 A。"""
    gate_a, gate_b = threading.Event(), threading.Event()
    provider = _PairGatedProvider(gate_a, gate_b)
    client, token = _client_with_sessions(provider, tmp_path)

    with client.websocket_connect(f"/ws?token={token}") as ws:
        info = ws.receive_json()
        assert info["type"] == "session_info"
        sid_a = info["session_id"]

        # A 发起 run(挂住)
        ws.send_json({"cmd": "prompt", "req_id": "1", "text": "A 任务"})
        assert ws.receive_json()["ok"] is True
        start_a = ws.receive_json()
        assert start_a == {"type": "agent_start", "session_id": sid_a}
        assert ws.receive_json() == {"type": "turn_start", "turn": 1, "session_id": sid_a}

        # 新建 B 并切换——A 运行中不再被拒
        ws.send_json({"cmd": "new_session", "req_id": "2"})
        resp_new = ws.receive_json()
        assert resp_new == {
            "type": "response",
            "req_id": "2",
            "ok": True,
            "session_id": resp_new.get("session_id"),
        }, resp_new
        info_b = ws.receive_json()
        assert info_b["type"] == "session_info"
        sid_b = info_b["session_id"]
        ws.send_json({"cmd": "switch_session", "req_id": "3", "session_id": sid_b})
        assert ws.receive_json()["ok"] is True
        info_after = ws.receive_json()  # switch 的 session_info 帧
        assert info_after["type"] == "session_info" and info_after["session_id"] == sid_b

        # B 发起 run——与 A 并存
        ws.send_json({"cmd": "prompt", "req_id": "4", "text": "B 任务"})
        assert ws.receive_json()["ok"] is True
        start_b = ws.receive_json()
        assert start_b == {"type": "agent_start", "session_id": sid_b}

        # REST:两个会话同时 running
        sessions = client.get(
            "/api/sessions", headers={"Authorization": f"Bearer {token}"}
        ).json()["sessions"]
        running = {s["id"]: s["running"] for s in sessions}
        assert running[sid_a] is True and running[sid_b] is True

        # 放行 A:收帧到 A 的 agent_end(其间 B 挂着不产帧)
        gate_a.set()
        while True:
            f = ws.receive_json()
            if f.get("type") == "agent_end" and f.get("session_id") == sid_a:
                assert f.get("stop_reason") == "end_turn"
                break

        # 定点 abort B:B 未产出内容 → 收尾帧为 agent_end(aborted),A 不受影响
        ws.send_json({"cmd": "abort", "req_id": "5", "session_id": sid_b})
        frames = [ws.receive_json(), ws.receive_json()]
        responses = [f for f in frames if f["type"] == "response"]
        ends = [f for f in frames if f["type"] == "agent_end"]
        assert len(responses) == 1 and responses[0]["aborted"] is True
        assert len(ends) == 1 and ends[0]["session_id"] == sid_b
        assert ends[0]["stop_reason"] == "aborted"

        # REST:A 完成(false)、B 已中止(false)
        sessions = client.get(
            "/api/sessions", headers={"Authorization": f"Bearer {token}"}
        ).json()["sessions"]
        running = {s["id"]: s["running"] for s in sessions}
        assert running[sid_a] is False and running[sid_b] is False


def test_delete_running_session_aborts_its_run(tmp_path) -> None:
    """删除运行中的会话 → 其 run 被定点中止(aborted 收尾),不向已删 JSONL 继续写。"""
    gate_a = threading.Event()
    provider = _PairGatedProvider(gate_a, threading.Event())
    client, token = _client_with_sessions(provider, tmp_path)

    with client.websocket_connect(f"/ws?token={token}") as ws:
        info = ws.receive_json()
        sid_a = info["session_id"]
        ws.send_json({"cmd": "prompt", "req_id": "1", "text": "长任务"})
        assert ws.receive_json()["ok"] is True
        start = ws.receive_json()
        assert start["session_id"] == sid_a
        turn = ws.receive_json()
        assert turn["type"] == "turn_start" and turn["session_id"] == sid_a

        resp = client.delete(f"/api/sessions/{sid_a}", headers={"Authorization": f"Bearer {token}"})
        assert resp.json()["ok"] is True

        # 取消发生在模型产出之前 → 收尾帧为 agent_end(aborted)（ADR-0005 修复）
        f = ws.receive_json()
        assert f["type"] == "agent_end" and f["session_id"] == sid_a
        assert f["stop_reason"] == "aborted"
        gate_a.set()  # 兜底:任务已被 cancel,不依赖门
