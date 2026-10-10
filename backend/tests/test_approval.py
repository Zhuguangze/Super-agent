"""app/approval.py——白名单放行、审批等待、拒绝/超时回填（ADR-0001）。"""

import asyncio

import pytest

from super_agent.app.approval import ApprovalService, make_approval_hook
from super_agent.app.chat import ChatService
from super_agent.ai.types import (
    Message,
    MessageEnd,
    MessageStart,
    TextBlock,
    TextDelta,
    ToolUseBlock,
    Usage,
)
from test_chat import MultiTurnProvider


def _hook(service: ApprovalService, user_id: int = 1):
    events: list[dict] = []

    async def on_event(ev: dict) -> None:
        events.append(ev)

    return make_approval_hook(service, user_id=user_id, on_event=on_event), events


async def test_allowlisted_tool_passes_without_approval() -> None:
    service = ApprovalService(allowlist={"read"})
    hook, events = _hook(service)
    assert await hook("read", {"path": "a.txt"}, "t1") is None
    assert events == []  # 白名单内不发审批事件


async def test_approval_flow_allow() -> None:
    service = ApprovalService(allowlist=set())
    hook, events = _hook(service)

    task = asyncio.create_task(hook("bash", {"command": "ls"}, "t1"))
    await asyncio.sleep(0.01)

    # 审批请求已推送，挂起中可查询
    assert [e["type"] for e in events] == ["approval_request"]
    request = events[0]
    assert request["tool"] == "bash"
    assert service.pending_for(1) != []

    # 用户放行 → 钩子返回 None（放行）
    assert service.resolve(request["approval_id"], user_id=1, allow=True) is True
    assert await task is None
    assert service.pending_for(1) == []  # 已清理


async def test_approval_flow_deny_backfills_reason() -> None:
    service = ApprovalService(allowlist=set())
    hook, events = _hook(service)

    task = asyncio.create_task(hook("bash", {"command": "rm -rf /"}, "t1"))
    await asyncio.sleep(0.01)
    request = events[0]
    assert service.resolve(request["approval_id"], user_id=1, allow=False) is True
    reason = await task
    assert reason is not None and "用户拒绝" in reason


async def test_approval_timeout_auto_denies() -> None:
    service = ApprovalService(allowlist=set(), timeout=0.05)
    hook, _ = _hook(service)
    reason = await hook("bash", {"command": "ls"}, "t1")
    assert reason is not None and "超时" in reason
    assert service.pending_for(1) == []


async def test_approval_isolated_by_user() -> None:
    service = ApprovalService(allowlist=set())
    hook, events = _hook(service, user_id=7)

    task = asyncio.create_task(hook("bash", {"command": "ls"}, "t1"))
    await asyncio.sleep(0.01)
    approval_id = events[0]["approval_id"]
    # 其他用户的裁决无效
    assert service.resolve(approval_id, user_id=8, allow=True) is False
    assert not task.done()
    assert service.resolve(approval_id, user_id=7, allow=True) is True
    assert await task is None
    assert [p.user_id for p in service.pending_for(7)] == []


def test_set_allowlist_updates_runtime() -> None:
    service = ApprovalService(allowlist=set())
    assert service.is_allowed("bash") is False
    service.set_allowlist({"bash"})
    assert service.is_allowed("bash") is True


async def test_reconnect_payload_matches_first_request() -> None:
    """重连重发：payload 与首次推送同构同 id——前端按 id 幂等，不重复弹窗。"""
    from super_agent.app.approval import approval_request_payload

    service = ApprovalService(allowlist=set())
    hook, events = _hook(service, user_id=5)
    task = asyncio.create_task(hook("bash", {"command": "ls -la"}, "t1"))
    await asyncio.sleep(0.01)

    pending = service.pending_for(5)[0]
    resent = approval_request_payload(pending)
    assert resent == events[0]  # 与首次 approval_request 完全一致（同 approval_id）
    assert service.resolve(resent["approval_id"], 5, True) is True
    assert await task is None


def test_settings_allowlist_roundtrip(tmp_path) -> None:
    from super_agent.app.settings import SettingsStore

    store = SettingsStore(tmp_path / "settings.json")
    assert store.load_allowlist() == set()  # 缺文件 → 空集
    store.save_allowlist({"bash", "read"})
    assert SettingsStore(tmp_path / "settings.json").load_allowlist() == {"bash", "read"}


# ---- ChatService 集成：钩子挂进 loop，拒绝后 agent 继续推理 ----


def _bash_then_reply(tool_id: str) -> list:
    return [
        [
            MessageStart(model="m", usage=Usage(input_tokens=5)),
            MessageEnd(
                stop_reason="tool_use",
                usage=Usage(input_tokens=5, output_tokens=4),
                message=Message(
                    role="assistant",
                    content=[ToolUseBlock(id=tool_id, name="bash", input={"command": "echo hi"})],
                ),
            ),
        ],
        [
            MessageStart(model="m", usage=Usage(input_tokens=9)),
            TextDelta("好的，已处理"),
            MessageEnd(
                stop_reason="end_turn",
                usage=Usage(input_tokens=9, output_tokens=6),
                message=Message(role="assistant", content=[TextBlock("好的，已处理")]),
            ),
        ],
    ]


async def test_chat_run_pauses_for_approval_then_proceeds(tmp_path) -> None:
    """approval_request 事件下发 → resolve(allow) → bash 真执行 → loop 继续。"""
    service = ApprovalService(allowlist=set())
    provider = MultiTurnProvider(_bash_then_reply("t1"))
    sent: list[dict] = []

    async def on_event(ev: dict) -> None:
        sent.append(ev)
        if ev.get("type") == "approval_request":
            # 模拟前端在收到请求后回调 approve 命令
            assert service.resolve(ev["approval_id"], user_id=1, allow=True) is True

    await ChatService(provider, workspace_root=tmp_path / "ws", approval=service).run_prompt(
        "跑个命令", user_id=1, on_event=on_event
    )

    types = [e["type"] for e in sent]
    assert "approval_request" in types
    end = next(e for e in sent if e["type"] == "tool_execution_end")
    assert end["is_error"] is False and "hi" in end["content"]
    assert sent[-1]["type"] == "agent_end"


async def test_chat_denied_tool_backfills_error_and_loop_continues(tmp_path) -> None:
    service = ApprovalService(allowlist=set())
    provider = MultiTurnProvider(_bash_then_reply("t1"))
    sent: list[dict] = []

    async def on_event(ev: dict) -> None:
        sent.append(ev)
        if ev.get("type") == "approval_request":
            assert service.resolve(ev["approval_id"], user_id=1, allow=False) is True

    await ChatService(provider, workspace_root=tmp_path / "ws", approval=service).run_prompt(
        "跑个命令", user_id=1, on_event=on_event
    )

    end = next(e for e in sent if e["type"] == "tool_execution_end")
    assert end["is_error"] is True and "用户拒绝" in end["content"]
    assert sent[-1]["type"] == "agent_end"  # 拒绝不拖垮 loop
