"""Seam C2：REST 会话端点——列表与全量条目，Bearer 认证与用户隔离。"""

from fastapi.testclient import TestClient

from super_agent.ai.types import Message, TextBlock
from super_agent.app.chat import ChatService
from super_agent.app.session_store import SessionStore
from super_agent.server.main import create_app
from test_chat import ScriptedProvider


def _client_as(tmp_path, username: str) -> tuple[TestClient, str]:
    store = SessionStore(tmp_path / "sessions")
    app = create_app(
        db_path=tmp_path / "test.db",
        chat=ChatService(ScriptedProvider(), store),
        sessions_dir=tmp_path / "sessions",
    )
    client = TestClient(app)
    client.post("/api/auth/register", json={"username": username, "password": "pw123456"})
    login = client.post("/api/auth/login", json={"username": username, "password": "pw123456"})
    return client, login.json()["token"]


def test_requires_token(tmp_path) -> None:
    client, _ = _client_as(tmp_path, "u1")
    assert client.get("/api/sessions").status_code == 401


def test_list_returns_only_own_sessions(tmp_path) -> None:
    client1, token1 = _client_as(tmp_path, "u1")
    client2, _ = _client_as(tmp_path, "u2")

    store = SessionStore(tmp_path / "sessions")
    mine_a = store.create_session(user_id=1)
    mine_b = store.create_session(user_id=1)
    store.create_session(user_id=2)  # 他人会话

    resp = client1.get("/api/sessions", headers={"Authorization": f"Bearer {token1}"})
    assert resp.status_code == 200
    ids = [s["id"] for s in resp.json()["sessions"]]
    assert set(ids) == {mine_a, mine_b}


def test_get_returns_full_entries(tmp_path) -> None:
    client, token = _client_as(tmp_path, "u1")
    store = SessionStore(tmp_path / "sessions")
    sid = store.create_session(user_id=1)
    store.append_message(1, sid, Message(role="user", content=[TextBlock("历史消息")]))

    resp = client.get(f"/api/sessions/{sid}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == sid
    assert [e["type"] for e in body["entries"]] == ["message"]
    assert body["entries"][0]["message"]["content"][0]["text"] == "历史消息"


def test_get_other_users_session_is_404(tmp_path) -> None:
    client1, token1 = _client_as(tmp_path, "u1")
    _, _ = _client_as(tmp_path, "u2")
    store = SessionStore(tmp_path / "sessions")
    other_sid = store.create_session(user_id=2)

    resp = client1.get(f"/api/sessions/{other_sid}", headers={"Authorization": f"Bearer {token1}"})
    assert resp.status_code == 404
    # 不存在的会话同样 404，不泄露存在性
    resp = client1.get("/api/sessions/nonexistent", headers={"Authorization": f"Bearer {token1}"})
    assert resp.status_code == 404


# ---- ticket 09：删除与活跃分支回放 ----


def test_delete_own_session(tmp_path) -> None:
    client, token = _client_as(tmp_path, "u1")
    store = SessionStore(tmp_path / "sessions")
    sid = store.create_session(user_id=1)

    resp = client.delete(f"/api/sessions/{sid}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200 and resp.json()["ok"] is True
    assert client.get(f"/api/sessions/{sid}", headers={"Authorization": f"Bearer {token}"}).status_code == 404
    ids = [s["id"] for s in client.get("/api/sessions", headers={"Authorization": f"Bearer {token}"}).json()["sessions"]]
    assert sid not in ids
    # 再删一次 → 404
    assert client.delete(f"/api/sessions/{sid}", headers={"Authorization": f"Bearer {token}"}).status_code == 404


def test_delete_other_users_session_is_404(tmp_path) -> None:
    client1, token1 = _client_as(tmp_path, "u1")
    _, _ = _client_as(tmp_path, "u2")
    store = SessionStore(tmp_path / "sessions")
    other_sid = store.create_session(user_id=2)

    resp = client1.delete(f"/api/sessions/{other_sid}", headers={"Authorization": f"Bearer {token1}"})
    assert resp.status_code == 404
    assert store.get_session(2, other_sid) is not None  # 未误删


# ---- 重命名 / 置顶（PATCH） ----


def test_patch_renames_and_lists_pinned_first(tmp_path) -> None:
    client, token = _client_as(tmp_path, "u1")
    store = SessionStore(tmp_path / "sessions")
    sid = store.create_session(user_id=1)

    resp = client.patch(
        f"/api/sessions/{sid}",
        headers={"Authorization": f"Bearer {token}"},
        json={"title": "研究项目", "pinned": True},
    )
    assert resp.status_code == 200 and resp.json()["ok"] is True

    listed = client.get("/api/sessions", headers={"Authorization": f"Bearer {token}"}).json()["sessions"]
    assert listed[0]["id"] == sid and listed[0]["pinned"] is True
    assert listed[0]["title"] == "研究项目"


def test_patch_validates_body(tmp_path) -> None:
    client, token = _client_as(tmp_path, "u1")
    headers = {"Authorization": f"Bearer {token}"}
    empty = client.patch("/api/sessions/s1", headers=headers, json={})
    assert empty.status_code == 422
    bad_type = client.patch("/api/sessions/s1", headers=headers, json={"pinned": "yes"})
    assert bad_type.status_code == 422
    missing = client.patch("/api/sessions/ghost", headers=headers, json={"title": "x"})
    assert missing.status_code == 404


def test_get_returns_active_branch_after_fork(tmp_path) -> None:
    """分叉后 GET 只回活跃分支——从"第一问"分叉，旧分支尾（第一答）不出现。"""
    client, token = _client_as(tmp_path, "u1")
    store = SessionStore(tmp_path / "sessions")
    sid = store.create_session(user_id=1)
    e1 = store.append_message(1, sid, Message(role="user", content=[TextBlock("第一问")]))
    store.append_message(1, sid, Message(role="assistant", content=[TextBlock("第一答")]))
    store.append_message(
        1, sid, Message(role="user", content=[TextBlock("换个问法")]), parent_entry_id=e1
    )

    resp = client.get(f"/api/sessions/{sid}", headers={"Authorization": f"Bearer {token}"})
    texts = [
        e["message"]["content"][0]["text"]
        for e in resp.json()["entries"]
        if e["type"] == "message"
    ]
    assert texts == ["第一问", "换个问法"]


# ---- ticket 17：分支注记与任意分支视图 ----


def test_get_entries_carry_branch_annotations(tmp_path) -> None:
    client, token = _client_as(tmp_path, "u1")
    store = SessionStore(tmp_path / "sessions")
    sid = store.create_session(user_id=1)
    e1 = store.append_message(1, sid, Message(role="user", content=[TextBlock("第一问")]))
    e3 = store.append_message(1, sid, Message(role="user", content=[TextBlock("第二问")]))
    e4 = store.append_message(
        1, sid, Message(role="user", content=[TextBlock("换个问法")]), parent_entry_id=e1
    )

    resp = client.get(f"/api/sessions/{sid}", headers={"Authorization": f"Bearer {token}"})
    entries = resp.json()["entries"]
    by_text = {e["message"]["content"][0]["text"]: e for e in entries if e["type"] == "message"}
    assert by_text["换个问法"]["branch_index"] == 1
    assert by_text["换个问法"]["branch_total"] == 2
    assert by_text["换个问法"]["siblings"] == [e3, e4]


def test_get_branch_view_returns_subtree_path(tmp_path) -> None:
    client, token = _client_as(tmp_path, "u1")
    store = SessionStore(tmp_path / "sessions")
    sid = store.create_session(user_id=1)
    e1 = store.append_message(1, sid, Message(role="user", content=[TextBlock("第一问")]))
    store.append_message(1, sid, Message(role="assistant", content=[TextBlock("第一答")]))
    store.append_message(
        1, sid, Message(role="user", content=[TextBlock("换个问法")]), parent_entry_id=e1
    )
    headers = {"Authorization": f"Bearer {token}"}

    # 任意分支视图：从"第一问"的子树看，活跃叶是最后子代"换个问法"
    resp = client.get(f"/api/sessions/{sid}/branch/{e1}", headers=headers)
    assert resp.status_code == 200
    texts = [
        e["message"]["content"][0]["text"]
        for e in resp.json()["entries"]
        if e["type"] == "message"
    ]
    assert texts == ["第一问", "换个问法"]

    # 未知条目 404（不泄露会话内容）
    assert client.get(f"/api/sessions/{sid}/branch/ghost", headers=headers).status_code == 404

    # 他人会话同样 404
    store.create_session(user_id=2)
    resp = client.get("/api/sessions/nonexistent/branch/ghost", headers=headers)
    assert resp.status_code == 404


# ---- ticket 19：MCP 动态工具白名单（发现端点） ----


def test_get_mcp_tools_lists_discovered(tmp_path) -> None:
    import json
    import sys
    from pathlib import Path as _Path

    from test_mcp_client import FIXTURE_SERVER

    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps({"servers": {"fake": {"command": sys.executable, "args": [str(FIXTURE_SERVER)]}}}),
        encoding="utf-8",
    )
    app = create_app(
        db_path=tmp_path / "test.db",
        chat=ChatService(ScriptedProvider(), mcp_config_path=config),
        sessions_dir=tmp_path / "sessions",
    )
    client = TestClient(app)
    client.post("/api/auth/register", json={"username": "u1", "password": "pw123456"})
    token = client.post("/api/auth/login", json={"username": "u1", "password": "pw123456"}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    resp = client.get("/api/mcp/tools", headers=headers)
    assert resp.status_code == 200
    tools = resp.json()["tools"]
    assert "mcp__fake__echo" in {t["name"] for t in tools}
    assert all(t["server"] == "fake" for t in tools)

    # 未认证 401
    assert client.get("/api/mcp/tools").status_code == 401


# ---- 手动选择技能（票：Claude Code 式会话框选技能） ----


def test_get_skills_lists_available(tmp_path) -> None:
    """GET /api/skills：可用技能清单（Composer 菜单数据源）。"""
    from pathlib import Path as _Path

    skills_dir = tmp_path / "skills"
    (skills_dir / "deploy").mkdir(parents=True)
    (skills_dir / "deploy" / "SKILL.md").write_text(
        "---\nname: deploy\ndescription: 部署清单\n---\n步骤", encoding="utf-8"
    )
    app = create_app(
        db_path=tmp_path / "test.db",
        chat=ChatService(ScriptedProvider(), skills_dir=skills_dir),
        sessions_dir=tmp_path / "sessions",
    )
    client = TestClient(app)
    client.post("/api/auth/register", json={"username": "u1", "password": "pw123456"})
    token = client.post("/api/auth/login", json={"username": "u1", "password": "pw123456"}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    resp = client.get("/api/skills", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["skills"] == [{"name": "deploy", "description": "部署清单"}]
    assert client.get("/api/skills").status_code == 401


# ---- ticket 22（已移除）：分支树面板曾用 GET /tree——2026-10-10 按用户决策删除 ----


def test_get_and_put_mcp_config(tmp_path) -> None:
    """票 20：GET 返回状态与 mcp.json 现内容；PUT 校验后写回并重连。"""
    import json
    import sys

    from test_mcp_client import FIXTURE_SERVER

    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps({"servers": {"fake": {"command": sys.executable, "args": [str(FIXTURE_SERVER)]}}}),
        encoding="utf-8",
    )
    app = create_app(
        db_path=tmp_path / "test.db",
        chat=ChatService(ScriptedProvider(), mcp_config_path=config),
        sessions_dir=tmp_path / "sessions",
    )
    client = TestClient(app)
    client.post("/api/auth/register", json={"username": "u1", "password": "pw123456"})
    token = client.post("/api/auth/login", json={"username": "u1", "password": "pw123456"}).json()["token"]
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    got = client.get("/api/mcp", headers=headers)
    assert got.status_code == 200
    assert got.json()["configured"] is True
    assert set(got.json()["config"]["servers"]) == {"fake"}

    # PUT：加入 fake2 → 写回文件 + 重连生效
    new_servers = {
        "fake": {"command": sys.executable, "args": [str(FIXTURE_SERVER)]},
        "fake2": {"command": sys.executable, "args": [str(FIXTURE_SERVER)]},
    }
    put = client.put("/api/mcp", headers=headers, json={"servers": new_servers})
    assert put.status_code == 200 and put.json()["ok"] is True
    assert json.loads(config.read_text(encoding="utf-8"))["servers"].keys() == {"fake", "fake2"}
    assert {t["server"] for t in put.json()["tools"]} == {"fake", "fake2"}


def test_put_mcp_validates_before_writing(tmp_path) -> None:
    """坏配置 → 422 且不落盘。"""
    import json

    config = tmp_path / "mcp.json"
    original = json.dumps({"servers": {"fake": {"command": "python", "args": ["x"]}}})
    config.write_text(original, encoding="utf-8")
    app = create_app(
        db_path=tmp_path / "test.db",
        chat=ChatService(ScriptedProvider(), mcp_config_path=config),
        sessions_dir=tmp_path / "sessions",
    )
    client = TestClient(app)
    client.post("/api/auth/register", json={"username": "u1", "password": "pw123456"})
    token = client.post("/api/auth/login", json={"username": "u1", "password": "pw123456"}).json()["token"]
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    bad_bodies = [
        {"servers": "not-a-dict"},
        {"servers": {"x": {"args": []}}},  # 缺 command
        {"servers": {"x": {"command": ""}}},  # 空 command
        {"servers": {"x": {"command": "py", "args": ["a", 1]}}},  # args 非全字符串
        {"servers": {"x": {"command": "py", "env": {"A": 1}}}},  # env 值非字符串
    ]
    for body in bad_bodies:
        resp = client.put("/api/mcp", headers=headers, json=body)
        assert resp.status_code == 422, f"应拒绝: {body}"
    assert config.read_text(encoding="utf-8") == original  # 未落盘

    # 未认证 401
    assert client.get("/api/mcp").status_code == 401
