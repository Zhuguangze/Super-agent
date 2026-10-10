"""REST 端点（spec §4.3）——会话列表、活跃分支回放、会话删除、模型/设置与用量。

认证走 Authorization Bearer token。GET /api/sessions/{id} 返回**活跃分支**
（沿最后一条 message 的 parentId 链），分叉后旧分支不会串进前端线性展示。
PUT /api/settings 落 settings.json 并热生效（白名单/并发上限/默认模型/自定义模型）。
"""

from fastapi import APIRouter, HTTPException, Request, status

from super_agent.ai.provider import MODELS
from super_agent.server.auth import AuthService, user_from_request

MAX_CONCURRENCY_BOUNDS = (1, 32)


def rest_router(service: AuthService, sessions, chat=None, settings_store=None, multi=None) -> APIRouter:
    router = APIRouter()

    def _require_user(request: Request):
        user = user_from_request(request, service)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="missing or invalid token"
            )
        return user

    @router.get("/api/sessions")
    def list_sessions(request: Request) -> dict:
        user = _require_user(request)
        running_sid = None
        if chat is not None:
            handle = getattr(request.app.state, "runs", {}).get(user.id)
            if handle is not None and not handle.task.done():
                running_sid = getattr(handle, "session_id", None)
        return {
            "sessions": [
                {
                    "id": meta.id,
                    "updated_at": meta.updated_at,
                    "title": meta.title,
                    "msg_count": meta.msg_count,
                    "total_tokens": meta.total_tokens,
                    "pinned": meta.pinned,
                    "running": meta.id == running_sid,
                }
                for meta in sessions.list_sessions(user.id)
            ]
        }

    @router.patch("/api/sessions/{session_id}")
    async def patch_session(session_id: str, request: Request) -> dict:
        """重命名 / 置顶（header 元数据，热生效）。"""
        user = _require_user(request)
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="body must be an object")
        title = body.get("title")
        pinned = body.get("pinned")
        if title is not None and not isinstance(title, str):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="title 必须是字符串")
        if pinned is not None and not isinstance(pinned, bool):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="pinned 必须是布尔")
        if title is None and pinned is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="title 与 pinned 至少给一个")
        ok = sessions.update_session_header(user.id, session_id, title=title, pinned=pinned)
        if not ok:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")
        return {"ok": True, "title": title, "pinned": pinned}

    @router.get("/api/sessions/{session_id}")
    def get_session(session_id: str, request: Request) -> dict:
        user = _require_user(request)
        if sessions.get_session(user.id, session_id) is None:
            # 他人会话与本用户的未知会话同样返回 404，不泄露存在性
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")
        return {"id": session_id, "entries": sessions.read_active_branch(user.id, session_id)}

    @router.get("/api/sessions/{session_id}/branch/{entry_id}")
    def get_session_branch(session_id: str, entry_id: str, request: Request) -> dict:
        """任意分支视图（票 17）：根到 entry_id 子树叶的路径，条目带分支注记。"""
        user = _require_user(request)
        if sessions.get_session(user.id, session_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")
        if not any(e["id"] == entry_id for e in sessions.read_entries(user.id, session_id)):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="entry not found")
        return {"id": session_id, "entries": sessions.read_branch(user.id, session_id, entry_id)}

    @router.delete("/api/sessions/{session_id}")
    def delete_session(session_id: str, request: Request) -> dict:
        user = _require_user(request)
        deleted = sessions.delete_session(user.id, session_id)
        if not deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")
        if chat is not None:
            chat.delete_session(user.id, session_id)  # 清分叉指针等残留状态
        return {"ok": True}

    @router.get("/api/models")
    def list_models(request: Request) -> dict:
        _require_user(request)
        return {
            "models": [
                {
                    "id": model.id,
                    "name": model.name,
                    "context_window": model.context_window,
                    "max_output_tokens": model.max_output_tokens,
                    "configured": multi.configured(model.id) if multi is not None else True,
                }
                for model in MODELS.values()
            ]
        }

    @router.get("/api/mcp/tools")
    async def list_mcp_tools(request: Request) -> dict:
        """已发现的 MCP 工具（票 19）：白名单勾选数据源；未配置 MCP 时为空列表。"""
        _require_user(request)
        if chat is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="chat unavailable")
        return {"tools": await chat.mcp_tools()}

    @router.get("/api/skills")
    def list_skills(request: Request) -> dict:
        """可用技能清单（Composer 手动选择菜单，票 18 后续）。"""
        user = _require_user(request)
        if chat is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="chat unavailable")
        return {"skills": chat.skills_list(user.id)}

    @router.get("/api/mcp")
    async def get_mcp(request: Request) -> dict:
        """MCP 面板状态（票 20）：mcp.json 现内容 + 每 server 运行态 + 已发现工具。"""
        _require_user(request)
        if chat is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="chat unavailable")
        return await chat.mcp_status()

    @router.put("/api/mcp")
    async def put_mcp(request: Request) -> dict:
        """更新 MCP 配置（票 20）：校验 → 写回 mcp.json → 重连热生效；坏配置 422 不落盘。"""
        _require_user(request)
        if chat is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="chat unavailable")
        body = await request.json()
        if not isinstance(body, dict) or not isinstance(body.get("servers"), dict):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="servers 必须是对象"
            )
        servers = body["servers"]
        for name, spec in servers.items():
            if not isinstance(name, str) or not name.strip():
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="server 名必须是非空字符串"
                )
            if (
                not isinstance(spec, dict)
                or not isinstance(spec.get("command"), str)
                or not spec["command"].strip()
            ):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"server {name}: command 必须是非空字符串",
                )
            args = spec.get("args", [])
            if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"server {name}: args 必须是字符串列表",
                )
            env = spec.get("env", {})
            if not isinstance(env, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in env.items()
            ):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"server {name}: env 必须是字符串字典",
                )
        chat.mcp_save_config(servers)
        errors = await chat.mcp_restart()
        state = await chat.mcp_status()
        return {
            "ok": True,
            "errors": errors,
            "servers": state["servers"],
            "tools": state["tools"],
        }

    @router.get("/api/usage")
    def get_usage(request: Request) -> dict:
        user = _require_user(request)
        totals = sessions.total_usage(user.id)
        return {"usage": totals}

    # ---- Settings（spec §4.3；热生效，票：Settings 视图） ----

    @router.get("/api/settings")
    def get_settings(request: Request) -> dict:
        _require_user(request)
        if settings_store is None or chat is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="settings unavailable")
        return {
            "approval_allowlist": sorted(chat.approval.allowlist) if chat.approval else [],
            "approval_enabled": chat.approval.enabled if chat.approval else True,
            "default_model": chat.default_model,
            "max_concurrency": chat.max_concurrency,
            "bash_timeout": chat.bash_timeout,
            "custom_models": settings_store.load_custom_models(),
        }

    @router.put("/api/settings")
    async def put_settings(request: Request) -> dict:
        _require_user(request)
        if settings_store is None or chat is None or multi is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="settings unavailable")
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="settings must be an object")

        # 校验（default_model 可为 None——尚未添加任何模型时允许保存）
        default_model = body.get("default_model") or chat.default_model
        if default_model is not None and default_model not in MODELS:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"未知模型: {default_model}")
        try:
            max_concurrency = int(body.get("max_concurrency") or chat.max_concurrency)
        except (TypeError, ValueError):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="max_concurrency 必须是整数")
        if not MAX_CONCURRENCY_BOUNDS[0] <= max_concurrency <= MAX_CONCURRENCY_BOUNDS[1]:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"max_concurrency 需在 {MAX_CONCURRENCY_BOUNDS[0]}–{MAX_CONCURRENCY_BOUNDS[1]} 之间",
            )
        allowlist_raw = body.get("approval_allowlist", [])
        if not isinstance(allowlist_raw, list) or not all(isinstance(n, str) for n in allowlist_raw):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="approval_allowlist 必须是字符串列表")
        approval_enabled = body.get("approval_enabled", True)
        if not isinstance(approval_enabled, bool):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="approval_enabled 必须是布尔")
        bash_timeout = body.get("bash_timeout", chat.bash_timeout)
        try:
            bash_timeout = float(bash_timeout)
        except (TypeError, ValueError):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="bash_timeout 必须是数字")
        if not 1 <= bash_timeout <= 300:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="bash_timeout 需在 1–300 秒之间")
        custom_models = body.get("custom_models", [])
        if not isinstance(custom_models, list) or not all(isinstance(m, dict) for m in custom_models):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="custom_models 必须是对象列表")

        # 落盘 + 热生效
        settings_store.save(
            {
                "approval_allowlist": sorted({str(n) for n in allowlist_raw}),
                "approval_enabled": approval_enabled,
                "default_model": default_model,
                "max_concurrency": max_concurrency,
                "bash_timeout": bash_timeout,
                "custom_models": custom_models,
            }
        )
        if chat.approval is not None:
            chat.approval.set_allowlist({str(n) for n in allowlist_raw})
            chat.approval.enabled = approval_enabled
        await chat.set_max_concurrency(max_concurrency)
        chat.set_default_model(default_model)
        chat.set_bash_timeout(bash_timeout)
        multi_errors = multi.rebind_custom_models(custom_models)

        return {
            "ok": True,
            "custom_model_errors": multi_errors,
            "default_model": chat.default_model,
            "max_concurrency": chat.max_concurrency,
            "approval_allowlist": sorted(chat.approval.allowlist) if chat.approval else [],
            "approval_enabled": chat.approval.enabled if chat.approval else True,
            "bash_timeout": chat.bash_timeout,
        }

    return router
