"""server 层入口——FastAPI 应用装配。

生产启动：``uvicorn super_agent.server.main:app --host 0.0.0.0 --port 8000``。
前端构建产物存在时（frontend/dist，或 SUPER_AGENT_FRONTEND_DIST）由本服务托管，
单进程即可访问完整 UI；API 与 /ws 路由优先，其余 GET 回落 SPA 的 index.html。
"""

import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from super_agent.ai.provider import MODELS
from super_agent.ai.providers.anthropic import AnthropicProvider
from super_agent.ai.providers.multi import MultiProvider
from super_agent.app.approval import ApprovalService
from super_agent.app.chat import ChatService
from super_agent.app.session_store import SessionStore
from super_agent.app.settings import SettingsStore
from super_agent.app.tools import DEFAULT_ALLOWLIST
from super_agent.server.auth import AuthService, auth_router
from super_agent.server.rest import rest_router
from super_agent.server.ws import ws_router

DEFAULT_DATA_DIR = Path(os.environ.get("SUPER_AGENT_DATA", Path.home() / ".super-agent"))
# 仓库布局下 frontend 与 backend 平级；容器/部署用环境变量指向拷贝后的 dist
DEFAULT_FRONTEND_DIST = Path(
    os.environ.get(
        "SUPER_AGENT_FRONTEND_DIST",
        Path(__file__).resolve().parents[3].parent / "frontend" / "dist",
    )
)

# 结构化文本日志（ticket 15）：时间戳/级别/来源统一格式，uvicorn 与应用共享
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


def _load_env_file(path: Path) -> None:
    """数据目录 .env 载入环境（不覆盖已有值）：ANTHROPIC_API_KEY、TAVILY_API_KEY 等。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'").strip('"'))


def create_app(
    db_path: Path | None = None,
    chat: ChatService | None = None,
    sessions_dir: Path | None = None,
    workspace_root: Path | None = None,
    ext_global_dir: Path | None = None,
    mcp_config_path: Path | None = None,
    frontend_dist: Path | None = None,
    settings_path: Path | None = None,
) -> FastAPI:
    app = FastAPI(title="Super Agent")
    _load_env_file(DEFAULT_DATA_DIR / ".env")
    service = AuthService(db_path or DEFAULT_DATA_DIR / "super_agent.db")
    store = SessionStore(sessions_dir or DEFAULT_DATA_DIR / "sessions")
    settings = SettingsStore(settings_path or DEFAULT_DATA_DIR / "settings.json")
    app.state.auth = service
    app.state.sessions = store
    # ADR-0001：白名单从 settings.json 加载，默认文件工具免审批、bash 需审批
    approval = ApprovalService(allowlist=settings.load_allowlist() or set(DEFAULT_ALLOWLIST))
    app.state.approval = approval
    app.state.runs = {}  # user_id → RunHandle（run 生命周期独立于单条 WS 连接）
    # 模型路由：全部来自 settings.json 的 custom_models（Settings 添加，动态注册）——
    # 代码不预置任何模型；Settings 热更经 multi.rebind_custom_models 生效
    multi = MultiProvider()
    multi.rebind_custom_models(settings.load_custom_models())
    app.state.multi_provider = multi
    # API key 从环境读取（SDK 解析 ANTHROPIC_API_KEY）；测试注入 chat 替身
    max_concurrency = settings.load_max_concurrency()
    app.state.chat = chat or ChatService(
        multi,
        store,
        workspace_root=workspace_root or DEFAULT_DATA_DIR / "workspaces",
        approval=approval,
        memory_root=DEFAULT_DATA_DIR / "memory",
        ext_global_dir=ext_global_dir or DEFAULT_DATA_DIR / "extensions",
        mcp_config_path=mcp_config_path or DEFAULT_DATA_DIR / "mcp.json",
        max_concurrency=max_concurrency,
        skills_dir=DEFAULT_DATA_DIR / "skills",  # 票 18 装配修复：全局技能目录
    )
    default_model = settings.load_default_model()
    if default_model and default_model in MODELS and chat is None:
        app.state.chat.set_default_model(default_model)
    app.include_router(auth_router(service))
    app.include_router(rest_router(service, store, chat=app.state.chat, settings_store=settings, multi=multi))
    app.include_router(ws_router())

    @app.get("/api/health")
    async def health() -> dict:
        return {"status": "ok"}

    _mount_frontend(app, frontend_dist if frontend_dist is not None else DEFAULT_FRONTEND_DIST)

    return app


def _mount_frontend(app: FastAPI, dist: Path) -> None:
    """托管前端构建产物（SPA）：/assets 走静态目录，其余 GET 回落 index.html。

    必须在所有 API/WS 路由之后注册（catch-all 只兜剩余路径）。
    dist 不存在（纯后端开发形态）则不挂载。
    """
    if not dist.is_dir():
        return
    dist_resolved = dist.resolve()
    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> FileResponse:
        # API 命名空间不被 SPA 吞掉：未匹配的 /api/* 保持 404
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="not found")
        if full_path:
            candidate = (dist / full_path).resolve()
            # 路径穿越防护：解析后必须仍在 dist 内
            if candidate.is_file() and candidate.is_relative_to(dist_resolved):
                return FileResponse(candidate)
        return FileResponse(dist / "index.html")


app = create_app()
