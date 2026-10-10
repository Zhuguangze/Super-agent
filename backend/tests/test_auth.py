"""Seam B2：认证——注册、登录、token 校验。全部走 HTTP API 公共接口，不摸库。"""

from httpx import ASGITransport, AsyncClient

from super_agent.server.main import create_app


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


async def test_register_then_login_returns_token_and_username(tmp_path) -> None:
    app = create_app(db_path=tmp_path / "test.db")
    async with _client(app) as client:
        reg = await client.post(
            "/api/auth/register",
            json={"username": "zhuguangze", "password": "secret123"},
        )
        assert reg.status_code == 201

        login = await client.post(
            "/api/auth/login",
            json={"username": "zhuguangze", "password": "secret123"},
        )
        assert login.status_code == 200

        body = login.json()
        assert body["username"] == "zhuguangze"
        assert isinstance(body["token"], str) and len(body["token"]) > 20


async def test_wrong_password_rejected_401(tmp_path) -> None:
    app = create_app(db_path=tmp_path / "test.db")
    async with _client(app) as client:
        await client.post(
            "/api/auth/register",
            json={"username": "zhuguangze", "password": "secret123"},
        )
        login = await client.post(
            "/api/auth/login",
            json={"username": "zhuguangze", "password": "wrong-password"},
        )
        assert login.status_code == 401


async def test_duplicate_username_rejected_409(tmp_path) -> None:
    app = create_app(db_path=tmp_path / "test.db")
    async with _client(app) as client:
        await client.post(
            "/api/auth/register",
            json={"username": "zhuguangze", "password": "secret123"},
        )
        again = await client.post(
            "/api/auth/register",
            json={"username": "zhuguangze", "password": "another-pass"},
        )
        assert again.status_code == 409


async def test_me_requires_valid_token(tmp_path) -> None:
    app = create_app(db_path=tmp_path / "test.db")
    async with _client(app) as client:
        # 无 token → 401
        no_token = await client.get("/api/me")
        assert no_token.status_code == 401

        # 无效 token → 401
        bad = await client.get(
            "/api/me", headers={"Authorization": "Bearer not-a-real-token"}
        )
        assert bad.status_code == 401

        # 注册 + 登录拿到的有效 token → 200 + username
        await client.post(
            "/api/auth/register",
            json={"username": "zhuguangze", "password": "secret123"},
        )
        login = await client.post(
            "/api/auth/login",
            json={"username": "zhuguangze", "password": "secret123"},
        )
        me = await client.get(
            "/api/me", headers={"Authorization": f"Bearer {login.json()['token']}"}
        )
        assert me.status_code == 200
        assert me.json() == {"username": "zhuguangze"}
