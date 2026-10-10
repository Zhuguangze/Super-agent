"""WS 通道——命令上行、事件下行（spec §4.2）。

握手即认证：token 无效立即以 4401 关闭；有效后进入命令循环。
v1 命令：echo（开发通路）、prompt（对话，运行中到达则排队）、abort（中断当前流）、
new_session（新开对话即新 Session）、approve（工具审批裁决，ADR-0001）。
运行中到达的 prompt 进 steer 队列（当前 Turn 工具执行后消费 / loop 结束后按
Follow-up 自动续跑，语义见 CONTEXT.md）；队列事件为 queue_update。

运行注册表（app.state.runs，user_id → RunHandle）让 run 的生命周期独立于
单条 WS 连接：断线不杀 run（事件暂弃，落盘照旧），重连接管事件出口并按
同一 approval_id 重发挂起审批——前端幂等去重，不产生重复弹窗。
"""

import asyncio

from fastapi import APIRouter, Query, WebSocket, WebSocketException
from starlette.websockets import WebSocketDisconnect

from super_agent.app.approval import approval_request_payload
from super_agent.app.chat import ChatService
from super_agent.server.auth import AuthService

WS_CLOSE_UNAUTHORIZED = 4401


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


def ws_router() -> APIRouter:
    router = APIRouter()

    @router.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket, token: str = Query(...)) -> None:
        service: AuthService = websocket.app.state.auth
        chat: ChatService = websocket.app.state.chat
        runs: dict[int, RunHandle] = websocket.app.state.runs
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

        existing = runs.get(user.id)
        reconnecting = existing is not None and not existing.task.done()
        if reconnecting:
            existing.sink["ws"] = websocket
            # 重发挂起审批（同 approval_id，前端按 id 幂等，不重复弹窗）
            if approval is not None:
                for pending in approval.pending_for(user.id):
                    await websocket.send_json(approval_request_payload(pending))
            await websocket.send_json({"type": "queue_update", "pending": existing.queue.qsize()})
        else:
            runs.pop(user.id, None)
        handle = existing if reconnecting else None
        sink: dict = handle.sink if handle else {"ws": websocket}
        queue: asyncio.Queue[str] = handle.queue if handle else asyncio.Queue()

        async def on_event(ev: dict) -> None:
            target = sink["ws"]
            if target is None:
                return  # 断线窗口：事件暂弃，会话已落盘可回放
            try:
                await target.send_json(ev)
            except Exception:
                sink["ws"] = None  # 连接已死，后续事件静默丢弃

        async def run_prompt(text: str, run_session_id: str | None, skill: str | None = None) -> None:
            # session_id / model / effort 启动时快照：run 期间切换不影响本 run
            try:
                await chat.run_prompt(
                    text,
                    user_id=user.id,
                    session_id=run_session_id,
                    on_event=on_event,
                    model=chat.session_model(user.id, run_session_id),
                    effort=chat.session_effort(user.id, run_session_id),
                    steer_queue=queue,
                    skill=skill,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # 编程类错误不能静默——尽力给用户一条可见的错误事件
                await on_event({"type": "error", "code": "internal", "message": str(exc)})

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
                    if not text:
                        await websocket.send_json(
                            {
                                "type": "response",
                                "req_id": req_id,
                                "ok": False,
                                "error": "empty_prompt",
                            }
                        )
                        continue
                    if handle is not None and not handle.task.done():
                        # 运行中：进队列（Steer / Follow-up 由 ChatService 消费）
                        handle.queue.put_nowait(text)
                        await websocket.send_json(
                            {
                                "type": "response",
                                "req_id": req_id,
                                "ok": True,
                                "queued": True,
                            }
                        )
                        await websocket.send_json(
                            {"type": "queue_update", "pending": handle.queue.qsize()}
                        )
                        continue
                    response: dict = {"type": "response", "req_id": req_id, "ok": True}
                    if session_id is not None:
                        response["session_id"] = session_id
                    await websocket.send_json(response)
                    skill = msg.get("skill")  # 手动选技能（仅发起新 run 时；排队插话不带）
                    task = asyncio.create_task(
                        run_prompt(text, session_id, skill=str(skill) if isinstance(skill, str) and skill.strip() else None)
                    )
                    handle = RunHandle(task=task, sink=sink, queue=queue, session_id=session_id)
                    runs[user.id] = handle
                elif cmd == "abort":
                    aborted = handle is not None and not handle.task.done()
                    if handle is not None and aborted:
                        handle.task.cancel()
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
                    busy = handle is not None and not handle.task.done()
                    if busy:
                        await websocket.send_json(
                            {
                                "type": "response",
                                "req_id": req_id,
                                "ok": False,
                                "error": "agent_busy",
                            }
                        )
                    elif not chat.get_session(user.id, target):
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
                    # 手动压缩（ticket 10）：与 run 互斥，事件经 on_event 下发
                    busy = handle is not None and not handle.task.done()
                    if busy:
                        await websocket.send_json(
                            {"type": "response", "req_id": req_id, "ok": False, "error": "agent_busy"}
                        )
                    else:
                        compacted = await chat.compact_session(
                            user_id=user.id, session_id=session_id, on_event=on_event
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
            current = runs.get(user.id)
            if current is not None and not current.task.done():
                # 断线不杀 run：审批等待/工具执行继续，落盘照旧；重连接管
                current.sink["ws"] = None

    return router
