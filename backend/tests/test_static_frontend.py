"""ticket 15 补充：前端构建产物的 SPA 托管——assets、fallback 与穿越防护。"""

from pathlib import Path

from fastapi.testclient import TestClient

from super_agent.server.main import create_app
from test_chat import ScriptedProvider


def _client_with_dist(tmp_path: Path) -> TestClient:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html><body>Super Agent SPA</body></html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log('app')", encoding="utf-8")
    (dist / "favicon.ico").write_bytes(b"\x00\x00\x01\x00")
    app = create_app(
        db_path=tmp_path / "t.db",
        chat=ScriptedProvider(),
        sessions_dir=tmp_path / "sessions",
        frontend_dist=dist,
    )
    return TestClient(app)


def test_root_serves_index_html(tmp_path: Path) -> None:
    client = _client_with_dist(tmp_path)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Super Agent SPA" in resp.text


def test_assets_served_from_static_mount(tmp_path: Path) -> None:
    client = _client_with_dist(tmp_path)
    resp = client.get("/assets/app.js")
    assert resp.status_code == 200
    assert "console.log" in resp.text


def test_spa_fallback_for_client_routes(tmp_path: Path) -> None:
    """前端路由（如 /login）刷新 → 回落 index.html。"""
    client = _client_with_dist(tmp_path)
    resp = client.get("/login")
    assert resp.status_code == 200
    assert "Super Agent SPA" in resp.text


def test_missing_file_falls_back_to_index(tmp_path: Path) -> None:
    client = _client_with_dist(tmp_path)
    resp = client.get("/no-such-file.js")
    assert resp.status_code == 200
    assert "Super Agent SPA" in resp.text


def test_path_traversal_blocked(tmp_path: Path, tmp_path_factory) -> None:
    """../ 穿越：解析后在 dist 外 → 回落 index.html，绝不泄露外部文件。"""
    secret = tmp_path / "secret.txt"
    secret.write_text("机密", encoding="utf-8")
    client = _client_with_dist(tmp_path)
    resp = client.get("/..%2Fsecret.txt")
    assert resp.status_code == 200
    assert "机密" not in resp.text


def test_api_and_ws_take_precedence(tmp_path: Path) -> None:
    client = _client_with_dist(tmp_path)
    assert client.get("/api/health").json() == {"status": "ok"}
    # /api/xxx 未匹配到具体路由时也不会被 SPA 吞掉（404 而非 index.html）
    resp = client.get("/api/unknown")
    assert resp.status_code == 404


def test_no_dist_means_no_fallback(tmp_path: Path) -> None:
    app = create_app(
        db_path=tmp_path / "t.db",
        chat=ScriptedProvider(),
        sessions_dir=tmp_path / "sessions",
        frontend_dist=tmp_path / "nonexistent",
    )
    client = TestClient(app)
    resp = client.get("/whatever")
    assert resp.status_code == 404
