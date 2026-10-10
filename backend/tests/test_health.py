"""Seam B1：HTTP API——GET /api/health 返回 200 与状态 JSON。

这是前端将来消费的同一接口，是最真实的公共边界。
"""

from httpx import ASGITransport, AsyncClient


async def test_health_returns_ok() -> None:
    from super_agent.server.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/api/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
