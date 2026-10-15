"""WS 通道——命令上行、事件下行（spec §4.2）。

握手即认证：token 无效立即以 4401 关闭；有效后进入命令循环。
命令：echo（开发通路）、prompt（对话；同会话运行中到达则入 steer 队列；不同会话
各自独立 run，可并行）、abort（按 session_id 定点中断，缺省当前会话）、
new_session / switch_session（运行中均可用——旧会话的 run 继续存活）、
approve（工具审批裁决，ADR-0001）、branch / compact / set_model / set_thinking_level。

运行注册表（app.state.runs，(user_id, session_id) → RunHandle）让 run 的生命周期
独立于单条 WS 连接：断线不杀 run（事件暂弃，落盘照旧），重连后该用户所有活动
run 统一接管事件出口，并按同一 approval_id 重发挂起审批——前端幂等去重。
所有下行事件携带 session_id（单连接多路复用的路由前提，ADR-0005）。
"""

import asyncio
import base64

from fastapi import APIRouter, Query, WebSocket, WebSocketException
from starlette.websockets import WebSocketDisconnect

from super_agent.app.approval import approval_request_payload
from super_agent.app.chat import ChatService
from super_agent.server.auth import AuthService

WS_CLOSE_UNAUTHORIZED = 4401

# 票 01：随 prompt 上行的图片限制（对齐 read 工具的单张上限）
_IMAGE_MEDIA_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
_MAX_IMAGES = 4
_MAX_IMAGE_BYTES = 5 * 1024 * 1024
_MAX_TOTAL_IMAGE_BYTES = 12 * 1024 * 1024


def _validate_images(images: list) -> str | None:
    """校验 prompt 携带的图片；返回错误消息（None = 合法）。"""
    if len(images) > _MAX_IMAGES:
        return f"图片数量超限：{len(images)} 张（上限 {_MAX_IMAGES} 张）"
    total = 0
    for im in images:
        if not isinstance(im, dict):
            return "图片项格式不正确"
        media_type = str(im.get("media_type", ""))
        data = str(im.get("data", ""))
        if media_type not in _IMAGE_MEDIA_TYPES:
            return f"不支持的图片类型：{media_type or '未知'}"
        try:
            raw = base64.b64decode(data)
        except Exception:
            return "图片数据不是有效的 base64"
        if len(raw) > _MAX_IMAGE_BYTES:
            return "单张图片超过 5MB 上限"
        total += len(raw)
    if total > _MAX_TOTAL_IMAGE_BYTES:
        return "图片总大小超过 12MB 上限"
    return None


class RunHandle:
    """一次 run 的 WS 侧句柄：task、事件出口（可换连接）、steer 队列、落盘会话。"""

    def __init__(
        self,
        task: asyncio.Task,
        sink: dict,  # {"ws": WebSocket | None}
        queue: asyncio.Queue[str],
        session_id: str | None = None,
    ) -> None:
        self.task = task
        self.sink = sink
        self.queue = queue
        self.session_id = session_id

    def active(self) -> bool:
        return not self.task.done()


def ws_router() -> APIRouter:
    router = APIRouter()

    @router.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket, token: str = Query(...)) -> None:
        service: AuthService = websocket.app.state.auth
        chat: ChatService = websocket.app.state.chat
        runs: dict[tuple[int, str], RunHandle] = websocket.app.state.runs
        approval = getattr(chat, "approval", None)
        user = service.user_for_token(token)
        if user is None:
            raise WebSocketException(code=WS_CLOSE_UNAUTHORIZED)

        await websocket.accept()
        session_id = chat.ensure_session(user.id)
        if session_id is not None:
            # 连接即定当前会话；前端据此从 REST 拉历史回放，并同步该会话的模型
            await websocket.send_json(
                {
                    "type": "session_info",
                    "session_id": session_id,
                    "model": chat.session_model(user.id, session_id),
                }
            )

        def user_handles() -> list[RunHandle]:
            return [h for (uid, _sid), h in runs.items() if uid == user.id]

        # 重连接管：该用户所有活动 run 的事件出口统一切到本连接
        for h in user_handles():
            if h.active():
                h.sink["ws"] = websocket
        # 重发挂起审批（同 approval_id，前端按 id 幂等，不重复弹窗）
        if approval is not None:
            for pending in approval.pending_for(user.id):
                await websocket.send_json(approval_request_payload(pending))

        def make_on_event(sink: dict):
            async def on_event(ev: dict) -> None:
                target = sink["ws"]
                if target is None:
                    return  # 断线窗口：事件暂弃，会话已落盘可回放
                try:
                    await target.send_json(ev)
                except Exception:
                    sink["ws"] = None  # 连接已死，后续事件静默丢弃

            return on_event

        async def run_prompt(
            text: str,
            run_session_id: str | None,
            sink: dict,
            queue: asyncio.Queue[str],
            skill: str | None = None,
            expert: str | None = None,
            hidden: bool = False,
            images: list[dict] | None = None,
        ) -> None:
            # session_id / model / effort 启动时快照：run 期间切换不影响本 run
            try:
                await chat.run_prompt(
                    text,
                    user_id=user.id,
                    session_id=run_session_id,
                    on_event=make_on_event(sink),
                    model=chat.session_model(user.id, run_session_id),
                    effort=chat.session_effort(user.id, run_session_id),
                    steer_queue=queue,
                    skill=skill,
                    expert=expert,
                    hidden=hidden,
                    images=images,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # 编程类错误不能静默——尽力给用户一条可见的错误事件
                await make_on_event(sink)({"type": "error", "code": "internal", "message": str(exc)})

        async def steer(handle: RunHandle, text: str) -> None:
            """同会话运行中：进 steer 队列（当前 Turn 工具后注入 / Follow-up 续跑）。"""
            handle.queue.put_nowait(text)
            await websocket.send_json(
                {"type": "response", "req_id": msg.get("req_id"), "ok": True, "queued": True}
            )
            await websocket.send_json(
                {
                    "type": "queue_update",
                    "pending": handle.queue.qsize(),
                    "session_id": handle.session_id,
                }
            )

        try:
            while True:
                msg = await websocket.receive_json()
                cmd = msg.get("cmd")
                req_id = msg.get("req_id")
                if cmd == "echo":
                    await websocket.send_json(
                        {"type": "response", "req_id": req_id, "text": msg.get("text")}
                    )
                elif cmd == "prompt":
                    text = str(msg.get("text") or "").strip()
                    raw_images = msg.get("images")
                    images = raw_images if isinstance(raw_images, list) else []
                    image_error = _validate_images(images)
                    if image_error is not None:
                        await websocket.send_json(
                            {
                                "type": "response",
                                "req_id": req_id,
                                "ok": False,
                                "error": "invalid_images",
                                "message": image_error,
                            }
                        )
                        continue
                    if not text and not images:  # 纯图消息（多模态）允许空文本
                        await websocket.send_json(
                            {
                                "type": "response",
                                "req_id": req_id,
                                "ok": False,
                                "error": "empty_prompt",
                            }
                        )
                        continue
                    # 同会话运行中 → steer；其他会话在跑与本会话无关（并发 run，ADR-0005）
                    handle = runs.get((user.id, session_id))
                    if handle is not None and handle.active():
                        await steer(handle, text)
                        continue
                    response: dict = {"type": "response", "req_id": req_id, "ok": True}
                    if session_id is not None:
                        response["session_id"] = session_id
                    await websocket.send_json(response)
                    skill = msg.get("skill")  # 手动选技能（仅发起新 run 时；排队插话不带）
                    raw_expert = msg.get("expert")  # 专家模式：人格注入 system prompt，每轮随消息携带
                    expert = str(raw_expert) if isinstance(raw_expert, str) and raw_expert.strip() else None
                    hidden = bool(msg.get("hidden"))  # 隐藏触发词：只进模型上下文，不入库
                    run_sink: dict = {"ws": websocket}
                    run_queue: asyncio.Queue[str] = asyncio.Queue()
                    task = asyncio.create_task(
                        run_prompt(
                            text,
                            session_id,
                            run_sink,
                            run_queue,
                            skill=str(skill) if isinstance(skill, str) and skill.strip() else None,
                            expert=expert,
                            hidden=hidden,
                            images=[im for im in images if isinstance(im, dict)] or None,
                        )
                    )
                    runs[(user.id, session_id)] = RunHandle(
                        task=task, sink=run_sink, queue=run_queue, session_id=session_id
                    )
                elif cmd == "abort":
                    # target 缺省 = 当前会话;无 store 时 session_id 为 None,同样是合法键
                    target = msg.get("session_id") or session_id
                    h = runs.get((user.id, target))
                    aborted = h is not None and h.active()
                    if aborted:
                        h.task.cancel()
                        runs.pop((user.id, target), None)
                    await websocket.send_json(
                        {"type": "response", "req_id": req_id, "ok": True, "aborted": bool(aborted)}
                    )
                elif cmd == "approve":
                    approval_id = str(msg.get("approval_id") or "")
                    allow = bool(msg.get("allow"))
                    resolved = (
                        approval.resolve(approval_id, user.id, allow) if approval is not None else False
                    )
                    await websocket.send_json(
                        {
                            "type": "response",
                            "req_id": req_id,
                            "ok": True,
                            "resolved": resolved,
                        }
                    )
                    await websocket.send_json(
                        {
                            "type": "approval_resolved",
                            "approval_id": approval_id,
                            "allow": allow,
                        }
                    )
                elif cmd == "new_session":
                    session_id = chat.new_session(user.id) or session_id
                    await websocket.send_json(
                        {
                            "type": "response",
                            "req_id": req_id,
                            "ok": True,
                            "session_id": session_id,
                        }
                    )
                    await websocket.send_json(
                        {
                            "type": "session_info",
                            "session_id": session_id,
                            "model": chat.session_model(user.id, session_id),
                        }
                    )
                elif cmd == "switch_session":
                    target = str(msg.get("session_id") or "")
                    # 运行中切换不再受限（ADR-0005）：旧会话的 run 继续存活
                    if not chat.get_session(user.id, target):
                        await websocket.send_json(
                            {
                                "type": "response",
                                "req_id": req_id,
                                "ok": False,
                                "error": "session_not_found",
                            }
                        )
                    else:
                        session_id = target
                        await websocket.send_json(
                            {"type": "response", "req_id": req_id, "ok": True, "session_id": session_id}
                        )
                        # 前端据此从 REST 拉取该会话的活跃分支回放，并同步该会话的模型
                        await websocket.send_json(
                            {
                                "type": "session_info",
                                "session_id": session_id,
                                "model": chat.session_model(user.id, session_id),
                            }
                        )
                elif cmd == "branch":
                    entry_id = str(msg.get("entry_id") or "")
                    ok = chat.set_branch_point(user.id, session_id, entry_id)
                    await websocket.send_json(
                        {
                            "type": "response",
                            "req_id": req_id,
                            "ok": ok,
                            **({} if ok else {"error": "entry_not_found"}),
                        }
                    )
                elif cmd == "compact":
                    # 手动压缩（ticket 10）：目标会话运行中则拒绝（Q2-B 边界）
                    h = runs.get((user.id, session_id))
                    if h is not None and h.active():
                        await websocket.send_json(
                            {"type": "response", "req_id": req_id, "ok": False, "error": "agent_busy"}
                        )
                    else:
                        compacted = await chat.compact_session(
                            user_id=user.id,
                            session_id=session_id,
                            on_event=make_on_event({"ws": websocket}),
                        )
                        await websocket.send_json(
                            {"type": "response", "req_id": req_id, "ok": True, "compacted": compacted}
                        )
                elif cmd == "set_model":
                    # 会话级模型切换（ticket 14）：对下一 run 生效
                    model = str(msg.get("model") or "")
                    if chat.set_session_model(user.id, session_id, model):
                        await websocket.send_json(
                            {"type": "response", "req_id": req_id, "ok": True, "model": model}
                        )
                    else:
                        await websocket.send_json(
                            {"type": "response", "req_id": req_id, "ok": False, "error": "unknown_model"}
                        )
                elif cmd == "set_thinking_level":
                    # thinking level → API effort（ticket 14）
                    level = str(msg.get("level") or "")
                    if chat.set_session_effort(user.id, session_id, level):
                        await websocket.send_json(
                            {"type": "response", "req_id": req_id, "ok": True, "level": level}
                        )
                    else:
                        await websocket.send_json(
                            {"type": "response", "req_id": req_id, "ok": False, "error": "unknown_level"}
                        )
                else:
                    # 未知命令回落到扩展注册的命令（ticket 12）；仍无则 unknown_cmd
                    payload = {k: v for k, v in msg.items() if k not in ("cmd", "req_id")}
                    result = await chat.run_extension_command(str(cmd or ""), payload)
                    if result is not None:
                        await websocket.send_json(
                            {"type": "response", "req_id": req_id, "ok": True, "result": result}
                        )
                    else:
                        await websocket.send_json(
                            {"type": "response", "req_id": req_id, "ok": False, "error": "unknown_cmd"}
                        )
        except WebSocketDisconnect:
            # 断线不杀 run：该用户所有活动 run 的审批等待/工具执行继续，落盘照旧；重连接管
            for h in user_handles():
                if h.active():
                    h.sink["ws"] = None

    return router
