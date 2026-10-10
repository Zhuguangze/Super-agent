"""MCP 客户端（ticket 13/21，spec §4.8）——stdio 手写 + 远程 streamable-http（官方 SDK）。

stdio 传输是 newline-delimited JSON-RPC 2.0，v1 需要的面很小
（initialize / tools/list / tools/call），手写零成本；远程传输（票 21）
协议面大（会话管理/SSE 流/重连），引入官方 mcp SDK。

配置（mcp.json）：``{"servers": {"<name>": {"command": "...", "args": [], "env": {}}
或 {"url": "http://..."}}}}``；工具以 ``mcp__<server>__<tool>`` 命名空间注册，
与内置工具走同一执行与审批管道（不在审批白名单 → 默认需审批，ADR-0001）。
"""

import asyncio
import json
import logging
import os
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from super_agent.agent.types import AgentTool, AgentToolResult

PROTOCOL_VERSION = "2024-11-05"
REQUEST_TIMEOUT = 30.0
TOOLS_LIST_TIMEOUT = 10.0


class McpError(Exception):
    """MCP 通信失败——工具层转 is_error 结果。"""


@dataclass
class McpToolInfo:
    name: str
    description: str
    input_schema: dict


@dataclass
class McpCallResult:
    content: str
    is_error: bool


@dataclass
class _Pending:
    future: asyncio.Future


class McpServerConnection:
    """与一个 stdio MCP 服务器的连接：子进程 + 请求-响应关联。"""

    def __init__(self, name: str, command: str, args: list[str] | None = None, env: dict | None = None) -> None:
        self.name = name
        self._command = command
        self._args = args or []
        self._env = env or {}
        self._proc: asyncio.subprocess.Process | None = None
        self._req_id = 0
        self._pending: dict[int, _Pending] = {}
        self._reader_task: asyncio.Task | None = None
        self.start_error: str | None = None

    # ---- 生命周期 ----

    async def start(self) -> None:
        """启动进程并完成 initialize 握手；失败抛 McpError（调用方隔离）。"""
        try:
            # 强制子进程 stdio 用 UTF-8（Windows 默认本地编码会污染 JSON 流）
            env = {**os.environ, **self._env, "PYTHONIOENCODING": "utf-8"}
            self._proc = await asyncio.create_subprocess_exec(
                self._command,
                *self._args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except OSError as exc:
            raise McpError(f"MCP 服务器 {self.name} 启动失败: {exc}") from None
        self._reader_task = asyncio.create_task(self._read_loop())
        try:
            await self._request(
                "initialize",
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "super-agent", "version": "0.1"},
                },
                timeout=REQUEST_TIMEOUT,
            )
        except McpError:
            await self.close()
            raise
        await self._notify("notifications/initialized", {})

    async def close(self) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
            self._reader_task = None
        if self._proc is not None and self._proc.returncode is None:
            self._proc.kill()
        self._fail_pending("MCP 服务器连接已关闭")

    # ---- 收发 ----

    async def _read_loop(self) -> None:
        """逐行读 JSON-RPC 消息，按 id 配对响应；通知忽略。"""
        assert self._proc is not None and self._proc.stdout is not None
        try:
            while True:
                line = await self._proc.stdout.readline()
                if not line:
                    break  # 进程退出（EOF）
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                req_id = message.get("id")
                if req_id is None:
                    continue  # 通知（如日志）忽略
                pending = self._pending.pop(int(req_id), None)
                if pending is None or pending.future.done():
                    continue
                if "error" in message:
                    error = message["error"]
                    pending.future.set_exception(
                        McpError(f"MCP 错误 {error.get('code')}: {error.get('message')}")
                    )
                else:
                    pending.future.set_result(message.get("result"))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._fail_pending(f"MCP 服务器 {self.name} 读取失败: {exc}")

    def _fail_pending(self, reason: str) -> None:
        for pending in self._pending.values():
            if not pending.future.done():
                pending.future.set_exception(McpError(reason))
        self._pending.clear()

    async def _request(self, method: str, params: dict, timeout: float) -> Any:
        if self._proc is None or self._proc.stdin is None:
            raise McpError(f"MCP 服务器 {self.name} 未连接")
        if self._proc.returncode is not None:
            raise McpError(f"MCP 服务器 {self.name} 已退出（code {self._proc.returncode}）")
        self._req_id += 1
        req_id = self._req_id
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = _Pending(future=future)
        message = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        try:
            self._proc.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
            await self._proc.stdin.drain()
        except (OSError, RuntimeError) as exc:
            self._pending.pop(req_id, None)
            raise McpError(f"MCP 服务器 {self.name} 写入失败: {exc}") from None
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise McpError(f"MCP 服务器 {self.name} 请求超时: {method}") from None

    async def _notify(self, method: str, params: dict) -> None:
        assert self._proc is not None and self._proc.stdin is not None
        message = {"jsonrpc": "2.0", "method": method, "params": params}
        try:
            self._proc.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
            await self._proc.stdin.drain()
        except (OSError, RuntimeError) as exc:
            raise McpError(f"MCP 服务器 {self.name} 通知失败: {exc}") from None

    # ---- MCP 方法 ----

    async def list_tools(self) -> list[McpToolInfo]:
        result = await self._request("tools/list", {}, timeout=TOOLS_LIST_TIMEOUT)
        tools: list[McpToolInfo] = []
        for item in (result or {}).get("tools", []):
            tools.append(
                McpToolInfo(
                    name=str(item.get("name") or ""),
                    description=str(item.get("description") or ""),
                    input_schema=item.get("inputSchema") or {"type": "object", "properties": {}},
                )
            )
        return tools

    async def call_tool(self, tool_name: str, params: dict) -> McpCallResult:
        try:
            result = await self._request("tools/call", {"name": tool_name, "arguments": params}, timeout=REQUEST_TIMEOUT)
        except McpError as exc:
            return McpCallResult(content=str(exc), is_error=True)
        if result is None:
            return McpCallResult(content="MCP 服务器返回空结果", is_error=True)
        parts: list[str] = []
        for block in result.get("content", []):
            if block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
        return McpCallResult(content="\n".join(parts), is_error=bool(result.get("isError", False)))


class McpRemoteConnection:
    """远程 MCP 连接（票 21）：官方 SDK streamable-http，接口对齐 stdio 连接。

    生命周期挂在 AsyncExitStack 上（transport + session 两个上下文）；
    SDK 异常统一包成 McpError，registry 的失败隔离照常工作。
    """

    def __init__(self, name: str, url: str) -> None:
        self.name = name
        self._url = url
        self._stack: AsyncExitStack | None = None
        self._session: Any | None = None

    async def start(self) -> None:
        from mcp.client.session import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        stack = AsyncExitStack()
        try:
            read_stream, write_stream = await stack.enter_async_context(
                streamable_http_client(self._url)
            )
            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            await session.initialize()
        except asyncio.CancelledError:
            await stack.aclose()
            raise
        except Exception as exc:
            # SDK 异常（MCPError/httpx 错误等）统一包成 McpError——registry 失败隔离照常工作
            await stack.aclose()
            raise McpError(f"远程 MCP 连接失败: {exc}") from exc
        self._stack = stack
        self._session = session

    async def list_tools(self) -> list[McpToolInfo]:
        try:
            result = await self._session.list_tools()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise McpError(f"远程工具列表获取失败: {exc}") from exc
        return [
            McpToolInfo(
                name=t.name,
                description=t.description or "",
                input_schema=getattr(t, "input_schema", None) or getattr(t, "inputSchema", {}),
            )
            for t in result.tools
        ]

    async def call_tool(self, name: str, params: dict) -> McpCallResult:
        try:
            result = await self._session.call_tool(name, params)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise McpError(f"远程调用失败: {exc}") from exc
        parts = [
            block.text
            for block in result.content
            if getattr(block, "type", "") == "text" and getattr(block, "text", "")
        ]
        is_error = getattr(result, "is_error", None)
        if is_error is None:
            is_error = getattr(result, "isError", False)  # SDK 1.x 兼容
        return McpCallResult(content="\n".join(parts), is_error=bool(is_error))

    async def close(self) -> None:
        if self._stack is not None:
            try:
                await self._stack.aclose()
            except Exception:
                logging.warning("远程 MCP 连接关闭失败", exc_info=True)
            self._stack = None
            self._session = None


class McpTool(AgentTool):
    """MCP 工具壳：命名空间命名，转发 call，走统一审批与事件管道。"""

    execution_mode = "sequential"

    def __init__(self, connection: McpServerConnection, info: McpToolInfo) -> None:
        self.name = f"mcp__{connection.name}__{info.name}"
        self.description = info.description or f"MCP 工具（{connection.name}/{info.name}）"
        self.parameters = info.input_schema
        self.server = connection.name  # 来源 server（票 19：白名单 UI 标注用）
        self._connection = connection
        self._remote_name = info.name

    async def execute(self, tool_call_id, params, signal, on_update) -> AgentToolResult:
        result = await self._connection.call_tool(self._remote_name, params)
        return AgentToolResult(content=result.content, is_error=result.is_error)


@dataclass
class McpRegistry:
    """全部 MCP 服务器的连接与工具（启动时装配，失败隔离）。"""

    connections: dict[str, McpServerConnection] = field(default_factory=dict)
    tools: list[McpTool] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)  # server 名 → 失败原因（票 20）
    _started: bool = field(default=False, repr=False)

    async def start_from_config(self, config_path: Path) -> None:
        """读 mcp.json 并连接全部服务器；单个失败不拖垮其余。"""
        if self._started:
            return
        self._started = True
        servers = load_mcp_config(config_path)
        for name, spec in servers.items():
            url = spec.get("url")
            if isinstance(url, str) and url.strip():
                connection = McpRemoteConnection(name, url.strip())  # 票 21：远程 streamable-http
            elif spec.get("command"):
                connection = McpServerConnection(
                    name, str(spec.get("command") or ""), list(spec.get("args") or []), dict(spec.get("env") or {})
                )
            else:
                message = f"server {name}: 需提供 command（stdio）或 url（远程）之一"
                self.errors.append(message)
                self.failed[name] = message
                logging.warning(message)
                continue
            try:
                await connection.start()
            except McpError as exc:
                self.errors.append(str(exc))
                self.failed[name] = str(exc)
                logging.warning("%s", exc)
                continue
            self.connections[name] = connection
            try:
                infos = await connection.list_tools()
            except McpError as exc:
                self.errors.append(str(exc))
                self.failed[name] = str(exc)
                logging.warning("%s", exc)
                continue
            for info in infos:
                self.tools.append(McpTool(connection, info))

    def tool_summaries(self) -> list[dict]:
        """已发现工具的摘要（票 19）：Settings 白名单勾选与 MCP 面板的数据源。"""
        return [
            {"name": t.name, "server": t.server, "description": t.description}
            for t in self.tools
        ]


def load_mcp_config(path: Path) -> dict[str, dict]:
    """mcp.json → {server_name: {command, args, env}}；缺文件/坏格式返回空（MCP 可选）。"""
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    servers = body.get("servers")
    if not isinstance(servers, dict):
        return {}
    return {str(name): spec for name, spec in servers.items() if isinstance(spec, dict)}
