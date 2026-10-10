"""ticket 13：MCP stdio 客户端——握手、工具发现、调用转发、崩溃隔离与审批管道。"""

import sys
from pathlib import Path

import pytest

from super_agent.ai.types import (
    Message,
    MessageEnd,
    MessageStart,
    TextBlock,
    ToolUseBlock,
    Usage,
)
from super_agent.app.approval import ApprovalService
from super_agent.app.chat import ChatService
from super_agent.app.mcp_client import McpRegistry, load_mcp_config
from test_chat import MultiTurnProvider, _collector

FIXTURE_SERVER = Path(__file__).parent / "fixtures" / "fake_mcp_server.py"


def _server_command() -> list[str]:
    return [sys.executable, str(FIXTURE_SERVER)]


# ---- 配置加载 ----


def test_load_mcp_config(tmp_path: Path) -> None:
    config = tmp_path / "mcp.json"
    assert load_mcp_config(config) == {}  # 缺文件 → 空（MCP 可选）
    config.write_text(
        '{"servers": {"fake": {"command": "python", "args": ["x.py"], "env": {"A": "1"}}}}',
        encoding="utf-8",
    )
    servers = load_mcp_config(config)
    assert servers["fake"]["command"] == "python"
    config.write_text("not json{", encoding="utf-8")
    assert load_mcp_config(config) == {}


# ---- 连接与工具 ----


async def test_connect_and_list_tools(tmp_path: Path) -> None:
    from super_agent.app.mcp_client import McpServerConnection

    conn = McpServerConnection("fake", _server_command()[0], _server_command()[1:])
    await conn.start()
    try:
        infos = await conn.list_tools()
        assert [i.name for i in infos] == ["echo", "fail"]
        assert infos[0].input_schema["required"] == ["text"]
    finally:
        await conn.close()


async def test_call_tool_success_and_is_error(tmp_path: Path) -> None:
    from super_agent.app.mcp_client import McpServerConnection

    conn = McpServerConnection("fake", _server_command()[0], _server_command()[1:])
    await conn.start()
    try:
        ok = await conn.call_tool("echo", {"text": "你好"})
        assert ok.is_error is False
        assert ok.content == "fake echo: 你好"

        failed = await conn.call_tool("fail", {})
        assert failed.is_error is True and "故意失败" in failed.content

        unknown = await conn.call_tool("nope", {})
        assert unknown.is_error is True and "未知工具" in unknown.content
    finally:
        await conn.close()


async def test_server_crash_gives_clear_error(tmp_path: Path) -> None:
    """服务器进程被杀 → 调用报错清晰，不影响调用方（异常转 is_error）。"""
    from super_agent.app.mcp_client import McpServerConnection

    conn = McpServerConnection("fake", _server_command()[0], _server_command()[1:])
    await conn.start()
    try:
        assert conn._proc is not None
        conn._proc.kill()
        await conn._proc.wait()

        result = await conn.call_tool("echo", {"text": "x"})
        assert result.is_error is True
        assert "退出" in result.content or "失败" in result.content
    finally:
        await conn.close()


async def test_start_failure_isolated(tmp_path: Path) -> None:
    """命令不存在的服务器 → errors 记录、不注册工具，其余服务器不受影响。"""
    import json

    registry = McpRegistry()
    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps(
            {
                "servers": {
                    "ghost": {"command": "definitely-not-a-command-xyz"},
                    "fake": {"command": sys.executable, "args": [str(FIXTURE_SERVER)]},
                }
            }
        ),
        encoding="utf-8",
    )
    await registry.start_from_config(config)

    assert any("ghost" in e for e in registry.errors)
    assert "fake" in registry.connections
    tool_names = [t.name for t in registry.tools]
    assert "mcp__fake__echo" in tool_names  # 命名空间注册
    assert not any(t.name.startswith("mcp__ghost") for t in registry.tools)


async def test_registry_tool_summaries_for_allowlist_ui(tmp_path: Path) -> None:
    """票 19：工具摘要（name/server/description）——Settings 白名单勾选的数据源。"""
    import json

    registry = McpRegistry()
    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps({"servers": {"fake": {"command": sys.executable, "args": [str(FIXTURE_SERVER)]}}}),
        encoding="utf-8",
    )
    await registry.start_from_config(config)

    summaries = registry.tool_summaries()
    by_name = {s["name"]: s for s in summaries}
    assert set(by_name) == {"mcp__fake__echo", "mcp__fake__fail"}
    assert by_name["mcp__fake__echo"]["server"] == "fake"
    assert by_name["mcp__fake__echo"]["description"]  # fake 服务器的 echo 有描述


# ---- ChatService 端到端：同一审批管道 ----


# ---- 票 19：MCP 动态工具白名单（发现 + 审批免检） ----


async def test_chat_mcp_tools_lists_discovered_tools(tmp_path: Path) -> None:
    import json

    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps({"servers": {"fake": {"command": sys.executable, "args": [str(FIXTURE_SERVER)]}}}),
        encoding="utf-8",
    )
    chat = ChatService(MultiTurnProvider(_mcp_call_script()), mcp_config_path=config)
    tools = await chat.mcp_tools()
    assert {t["name"] for t in tools} == {"mcp__fake__echo", "mcp__fake__fail"}
    assert all(t["server"] == "fake" for t in tools)
    assert all(t["description"] for t in tools)


async def test_chat_mcp_tools_empty_without_config() -> None:
    """未配置 mcp.json：MCP 未启用，清单为空（Settings 不渲染 MCP 区块）。"""
    chat = ChatService(MultiTurnProvider(_mcp_call_script()))
    assert await chat.mcp_tools() == []


# ---- 票 20：MCP 管理面板（状态 + 重连） ----


async def test_mcp_restart_picks_up_config_changes(tmp_path: Path) -> None:
    """重连后新 server 的工具注册、移除的注销（Settings 保存即热生效）。"""
    import json

    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps({"servers": {"fake": {"command": sys.executable, "args": [str(FIXTURE_SERVER)]}}}),
        encoding="utf-8",
    )
    chat = ChatService(MultiTurnProvider(_mcp_call_script()), mcp_config_path=config)
    assert {t["server"] for t in await chat.mcp_tools()} == {"fake"}

    config.write_text(
        json.dumps(
            {
                "servers": {
                    "fake": {"command": sys.executable, "args": [str(FIXTURE_SERVER)]},
                    "fake2": {"command": sys.executable, "args": [str(FIXTURE_SERVER)]},
                }
            }
        ),
        encoding="utf-8",
    )
    assert await chat.mcp_restart() == []
    assert {t["server"] for t in await chat.mcp_tools()} == {"fake", "fake2"}


async def test_mcp_status_reports_servers_and_errors(tmp_path: Path) -> None:
    """状态：running/failed 按 server 区分，失败含原因；config 原样返回。"""
    import json

    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps(
            {
                "servers": {
                    "fake": {"command": sys.executable, "args": [str(FIXTURE_SERVER)]},
                    "ghost": {"command": "definitely-not-a-command-xyz"},
                }
            }
        ),
        encoding="utf-8",
    )
    chat = ChatService(MultiTurnProvider(_mcp_call_script()), mcp_config_path=config)
    status = await chat.mcp_status()
    assert status["configured"] is True
    servers = {s["name"]: s for s in status["servers"]}
    assert servers["fake"]["status"] == "running"
    assert servers["ghost"]["status"] == "failed"
    assert servers["ghost"]["error"]
    assert "mcp__fake__echo" in {t["name"] for t in status["tools"]}
    assert set(status["config"]["servers"]) == {"fake", "ghost"}


async def test_mcp_restart_without_config_is_noop() -> None:
    chat = ChatService(MultiTurnProvider(_mcp_call_script()))
    assert await chat.mcp_restart() == []
    status = await chat.mcp_status()
    assert status["configured"] is False
    assert status["servers"] == []


# ---- 票 21：远程 MCP（streamable-http，官方 SDK） ----


@pytest.fixture()
def remote_server():
    """FastMCP streamable-http 测试服务器：后台线程 + 随机端口，yield 端点 URL。"""
    import socket
    import threading
    import time

    import uvicorn
    from mcp.server.mcpserver import MCPServer

    mcp = MCPServer("remote-test")

    @mcp.tool()
    def remote_echo(text: str) -> str:
        """回声工具（远程）"""
        return f"remote echo: {text}"

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = uvicorn.Config(
        mcp.streamable_http_app(), host="127.0.0.1", port=port, log_level="warning"
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started, "测试服务器未启动"
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    thread.join(timeout=5)


async def test_remote_url_server_registers_tools(tmp_path: Path, remote_server) -> None:
    """url 型条目走 streamable-http：工具以 mcp__<server>__<tool> 注册，调用转发。"""
    import json

    registry = McpRegistry()
    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps({"servers": {"remote": {"url": remote_server}}}),
        encoding="utf-8",
    )
    await registry.start_from_config(config)
    try:
        assert registry.failed == {}
        assert "mcp__remote__remote_echo" in [t.name for t in registry.tools]
        result = await registry.connections["remote"].call_tool("remote_echo", {"text": "hi"})
        assert result.content == "remote echo: hi"
        assert result.is_error is False
    finally:
        for connection in registry.connections.values():
            await connection.close()


async def test_remote_and_stdio_coexist(tmp_path: Path, remote_server) -> None:
    """mcp.json 混合 stdio 与 url 条目，两路工具共存。"""
    import json

    registry = McpRegistry()
    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps(
            {
                "servers": {
                    "fake": {"command": sys.executable, "args": [str(FIXTURE_SERVER)]},
                    "remote": {"url": remote_server},
                }
            }
        ),
        encoding="utf-8",
    )
    await registry.start_from_config(config)
    try:
        names = [t.name for t in registry.tools]
        assert "mcp__fake__echo" in names and "mcp__remote__remote_echo" in names
        summaries = {s["server"] for s in registry.tool_summaries()}
        assert summaries == {"fake", "remote"}
    finally:
        for connection in registry.connections.values():
            await connection.close()


async def test_remote_bad_url_fails_isolated(tmp_path: Path) -> None:
    """连不上的远程 server：failed 记录原因，其余 server 不受影响。"""
    import json

    registry = McpRegistry()
    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps(
            {
                "servers": {
                    "dead": {"url": "http://127.0.0.1:9/mcp"},
                    "fake": {"command": sys.executable, "args": [str(FIXTURE_SERVER)]},
                }
            }
        ),
        encoding="utf-8",
    )
    await registry.start_from_config(config)
    try:
        assert "dead" in registry.failed
        assert "fake" in registry.connections
    finally:
        for connection in registry.connections.values():
            await connection.close()


async def test_chat_lists_remote_tools(tmp_path: Path, remote_server) -> None:
    """ChatService 集成：mcp.json 的 url 型 server 工具进同一清单与管道。"""
    import json

    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps({"servers": {"remote": {"url": remote_server}}}),
        encoding="utf-8",
    )
    chat = ChatService(MultiTurnProvider(_mcp_call_script()), mcp_config_path=config)
    try:
        tools = await chat.mcp_tools()
        assert {t["name"] for t in tools} == {"mcp__remote__remote_echo"}
        assert all(t["server"] == "remote" for t in tools)
    finally:
        if chat._mcp is not None:
            for connection in chat._mcp.connections.values():
                await connection.close()


async def test_allowlisted_mcp_tool_skips_approval(tmp_path: Path) -> None:
    """票 19 验收：白名单含 mcp__fake__echo → 直接执行，全程无 approval_request。

    白名单机制本就按名匹配（任意字符串名单 PUT 已收），此测试是
    "勾选后免审批" 的端到端回归锁（预期写时即绿）。
    """
    import json

    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps({"servers": {"fake": {"command": sys.executable, "args": [str(FIXTURE_SERVER)]}}}),
        encoding="utf-8",
    )
    approval = ApprovalService(allowlist={"mcp__fake__echo"})
    sent: list[dict] = []

    async def on_event(ev: dict) -> None:
        sent.append(ev)

    provider = MultiTurnProvider(_mcp_call_script())
    chat = ChatService(provider, mcp_config_path=config, approval=approval)
    await chat.run_prompt("用 MCP 工具", user_id=1, on_event=on_event)

    assert not any(e["type"] == "approval_request" for e in sent)
    end = next(e for e in sent if e["type"] == "tool_execution_end")
    assert end["is_error"] is False
    assert "fake echo: hi" in end["content"]


def _mcp_call_script() -> list:
    return [
        [
            MessageStart(model="m", usage=Usage(input_tokens=5)),
            MessageEnd(
                stop_reason="tool_use",
                usage=Usage(input_tokens=5, output_tokens=4),
                message=Message(
                    role="assistant",
                    content=[ToolUseBlock(id="t1", name="mcp__fake__echo", input={"text": "hi"})],
                ),
            ),
        ],
        [
            MessageStart(model="m", usage=Usage()),
            MessageEnd(
                stop_reason="end_turn",
                usage=Usage(input_tokens=9, output_tokens=2),
                message=Message(role="assistant", content=[TextBlock("done")]),
            ),
        ],
    ]


async def test_mcp_tool_end_to_end_and_approval(tmp_path: Path) -> None:
    """MCP 工具注册进统一工具表（provider 可见），默认不在白名单 → 走审批后执行。"""
    import json

    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps({"servers": {"fake": {"command": sys.executable, "args": [str(FIXTURE_SERVER)]}}}),
        encoding="utf-8",
    )
    approval = ApprovalService(allowlist=set())  # 全部需审批
    sent: list[dict] = []

    async def on_event(ev: dict) -> None:
        sent.append(ev)
        if ev.get("type") == "approval_request":
            assert ev["tool"] == "mcp__fake__echo"
            assert approval.resolve(ev["approval_id"], user_id=1, allow=True) is True

    provider = MultiTurnProvider(_mcp_call_script())
    chat = ChatService(provider, mcp_config_path=config, approval=approval)
    await chat.run_prompt("用 MCP 工具", user_id=1, on_event=on_event)

    # provider 工具定义含命名空间名
    assert provider.seen_tools is not None
    assert "mcp__fake__echo" in {d.name for d in provider.seen_tools}
    # 审批放行后真调用 → 结果回填
    end = next(e for e in sent if e["type"] == "tool_execution_end")
    assert end["is_error"] is False
    assert "fake echo: hi" in end["content"]


async def test_mcp_tool_denied_by_approval(tmp_path: Path) -> None:
    import json

    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps({"servers": {"fake": {"command": sys.executable, "args": [str(FIXTURE_SERVER)]}}}),
        encoding="utf-8",
    )
    approval = ApprovalService(allowlist=set())
    sent: list[dict] = []

    async def on_event(ev: dict) -> None:
        sent.append(ev)
        if ev.get("type") == "approval_request":
            assert approval.resolve(ev["approval_id"], user_id=1, allow=False) is True

    provider = MultiTurnProvider(_mcp_call_script())
    chat = ChatService(provider, mcp_config_path=config, approval=approval)
    await chat.run_prompt("用 MCP 工具", user_id=1, on_event=on_event)

    end = next(e for e in sent if e["type"] == "tool_execution_end")
    assert end["is_error"] is True and "用户拒绝" in end["content"]
    assert sent[-1]["type"] == "agent_end"  # 拒绝不拖垮 loop
