"""Tool Approval（ADR-0001）——审批以内置 before_tool_call 钩子落地。

语义：工具名不在白名单 → 执行前推送 approval_request 事件并挂起等待；
用户 approve(allow) 命令或超时兜底决定放行/拒绝。白名单内直接执行。
ApprovalService 挂在 app 级（跨 WS 连接存活）：断线重连后按 approval_id
重发同一请求，前端幂等去重，不产生重复弹窗。

app 层不知道 WebSocket：approval_request 经 on_event 回调下发。
"""

import asyncio
from collections.abc import Awaitable, Callable
from uuid import uuid4

DEFAULT_APPROVAL_TIMEOUT = 120.0

# 钩子签名对齐 agent.hooks 的 before_tool_call：(name, params, call_id) -> str | None
BeforeToolCallHook = Callable[[str, dict, str], Awaitable[str | None]]


class PendingApproval:
    def __init__(self, approval_id: str, user_id: int, name: str, params: dict) -> None:
        self.id = approval_id
        self.user_id = user_id
        self.name = name
        self.params = params
        self.future: asyncio.Future[bool] | None = None


class ApprovalService:
    """按用户管理挂起中的审批；白名单可配置（settings.json 持久化见 settings.py）。

    enabled=False 时全部工具直接执行（回到 pi 的无审批行为，Settings 开关控制）。
    """

    def __init__(
        self,
        allowlist: set[str] | None = None,
        timeout: float = DEFAULT_APPROVAL_TIMEOUT,
        enabled: bool = True,
    ) -> None:
        self._allowlist = allowlist or set()
        self._timeout = timeout
        self.enabled = enabled
        self._pending: dict[str, PendingApproval] = {}  # approval_id → 挂起请求

    # ---- 白名单 ----

    @property
    def allowlist(self) -> set[str]:
        return self._allowlist

    def set_allowlist(self, names: set[str]) -> None:
        self._allowlist = set(names)

    def is_allowed(self, name: str) -> bool:
        return not self.enabled or name in self._allowlist

    # ---- 挂起请求 ----

    def pending_for(self, user_id: int) -> list[PendingApproval]:
        return [p for p in self._pending.values() if p.user_id == user_id]

    def resolve(self, approval_id: str, user_id: int, allow: bool) -> bool:
        """用户裁决：命中挂起请求则置结果并返回 True；否则 False（幂等保护）。"""
        pending = self._pending.get(approval_id)
        if pending is None or pending.user_id != user_id or pending.future is None:
            return False
        if not pending.future.done():
            pending.future.set_result(allow)
        return True

    def _abandon(self, approval_id: str) -> None:
        self._pending.pop(approval_id, None)


def make_approval_hook(
    service: ApprovalService,
    *,
    user_id: int,
    on_event: Callable[[dict], Awaitable[None]],
) -> BeforeToolCallHook:
    """生成挂进 Hooks 的 before_tool_call 钩子：白名单放行，否则走审批流。"""

    async def hook(name: str, params: dict, call_id: str) -> str | None:
        if service.is_allowed(name):
            return None
        approval_id = f"apr_{uuid4().hex[:12]}"
        pending = PendingApproval(approval_id, user_id, name, params)
        pending.future = asyncio.get_running_loop().create_future()
        service._pending[approval_id] = pending
        await on_event(
            {
                "type": "approval_request",
                "approval_id": approval_id,
                "tool": name,
                "params": params,
            }
        )
        try:
            allowed = await asyncio.wait_for(pending.future, timeout=service._timeout)
        except asyncio.TimeoutError:
            return "审批超时未确认，已自动拒绝"
        finally:
            service._abandon(approval_id)
        return None if allowed else "用户拒绝了该工具调用"

    return hook


def approval_request_payload(pending: PendingApproval) -> dict:
    """重连重发用：与首次推送同构（同 approval_id），前端按 id 幂等。"""
    return {
        "type": "approval_request",
        "approval_id": pending.id,
        "tool": pending.name,
        "params": pending.params,
    }
