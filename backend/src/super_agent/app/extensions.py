"""扩展系统（ticket 12，spec §4.9）——Python 插件注册工具、命令与事件钩子。

目录：全局 ``<data>/extensions/`` + 工作区 ``<workspace>/extensions/``，每个
``*.py`` 暴露 ``register(api)``。加载失败只记错误、不影响主程序与其它插件。

API v1（命名对齐 pi ExtensionAPI）：
- ``api.register_tool(name, description, parameters, execute)``
- ``api.register_command(name, fn)``
- ``api.on(event, handler)``——十个事件：
  session_start / before_agent_start / tool_call（可拦截、可就地修改 params）/
  tool_result / context（转换上下文） / before_compaction（可阻断、可注入摘要指令）/
  compaction_end / compaction_failed / agent_settled / turn_end
"""

import importlib.util
import inspect
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from super_agent.agent.types import AgentTool, AgentToolResult
from super_agent.ai.types import Message

logger = logging.getLogger(__name__)

EXTENSION_EVENTS = (
    "session_start",
    "before_agent_start",
    "tool_call",
    "tool_result",
    "context",
    "before_compaction",
    "compaction_end",
    "compaction_failed",
    "agent_settled",
    "turn_end",
)

# 事件 handler 签名：
#   tool_call:          async (tool_name, params) -> str | None（返回 str 即拦截；可就地修改 params dict）
#   context:            async (messages: list[Message]) -> list[Message] | None
#   before_compaction:  async (payload: dict) -> dict | None（{"block": True} 阻断；{"instructions": str} 注入摘要要求）
#   其余:               async (payload: dict) -> None
ToolCallHook = Callable[[str, dict], Awaitable[str | None]]
ContextHook = Callable[[list[Message]], Awaitable[list[Message] | None]]


class ExtensionTool(AgentTool):
    """扩展注册的工具壳：扩展 execute 可同步可异步，异常转 is_error。"""

    execution_mode = "sequential"

    def __init__(self, name: str, description: str, parameters: dict, execute: Callable) -> None:
        self.name = name
        self.description = description
        self.parameters = parameters
        self._execute = execute

    async def execute(self, tool_call_id, params, signal, on_update) -> AgentToolResult:
        try:
            result = self._execute(tool_call_id, params, signal, on_update)
            if inspect.isawaitable(result):
                result = await result
            if isinstance(result, AgentToolResult):
                return result
            return AgentToolResult(content=str(result))
        except Exception as exc:
            return AgentToolResult(content=f"扩展工具异常: {exc}", is_error=True)


class ExtensionAPI:
    """传给插件 register(api) 的注册接口。"""

    def __init__(self, manager: "ExtensionManager", source: str) -> None:
        self._manager = manager
        self.source = source

    def register_tool(self, name: str, description: str, parameters: dict, execute: Callable) -> None:
        self._manager._tools.append(ExtensionTool(name, description, parameters, execute))

    def register_command(self, name: str, fn: Callable[..., Awaitable[dict]]) -> None:
        self._manager._commands[name] = fn

    def on(self, event: str, handler: Callable) -> None:
        if event not in EXTENSION_EVENTS:
            raise ValueError(f"未知事件: {event}（可用: {'/'.join(EXTENSION_EVENTS)}）")
        self._manager._handlers.setdefault(event, []).append(handler)


class ExtensionManager:
    """一个作用域（全局 + 某用户工作区）内已加载的扩展集合。"""

    def __init__(self) -> None:
        self._tools: list[AgentTool] = []
        self._commands: dict[str, Callable[..., Awaitable[dict]]] = {}
        self._handlers: dict[str, list[Callable]] = {}
        self.errors: list[str] = []

    # ---- 加载 ----

    @classmethod
    def load(cls, *dirs: Path | None) -> "ExtensionManager":
        manager = cls()
        for directory in dirs:
            if directory is not None:
                manager.load_into(directory)
        return manager

    def load_into(self, directory: Path) -> None:
        """附加加载一个目录（坏插件隔离：import/register 失败只记 errors）。"""
        if not directory.is_dir():
            return
        for path in sorted(directory.glob("*.py")):
            if path.name.startswith("_"):
                continue
            module_name = f"super_agent_ext_{path.stem}_{id(self):x}"
            try:
                spec = importlib.util.spec_from_file_location(module_name, path)
                if spec is None or spec.loader is None:
                    raise ImportError("无法构建 import spec")
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                register = getattr(module, "register", None)
                if not callable(register):
                    self.errors.append(f"{path.name}: 缺少 register(api) 函数")
                    continue
                register(ExtensionAPI(self, path.name))
            except Exception as exc:
                self.errors.append(f"{path.name} 加载失败: {exc}")
                logger.warning("扩展加载失败 %s: %s", path, exc)

    def fresh_copy(self) -> "ExtensionManager":
        """以已注册内容为底复制新实例（工作区扩展每 run 附加加载用）。"""
        manager = ExtensionManager()
        manager._tools = list(self._tools)
        manager._commands = dict(self._commands)
        manager._handlers = {event: list(handlers) for event, handlers in self._handlers.items()}
        return manager

    # ---- 查询 ----

    @property
    def tools(self) -> list[AgentTool]:
        return list(self._tools)

    def has_tool_call_hooks(self) -> bool:
        return bool(self._handlers.get("tool_call"))

    def has_compaction_hooks(self) -> bool:
        return bool(self._handlers.get("before_compaction"))

    # ---- 事件与钩子 ----

    async def emit(self, event: str, payload: dict) -> None:
        """通知类事件：逐个调用，单个 handler 异常不影响其它。"""
        for handler in self._handlers.get(event, []):
            try:
                result = handler(payload)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                logger.warning("扩展 %s 钩子异常: %s", event, exc)

    async def run_tool_call_hooks(self, tool_name: str, params: dict) -> str | None:
        """tool_call 钩子：任一返回 str 即拦截；handler 可就地修改 params dict。"""
        for hook in self._handlers.get("tool_call", []):
            try:
                result = hook(tool_name, params)
                if inspect.isawaitable(result):
                    result = await result
                if result is not None:
                    return str(result)
            except Exception as exc:
                logger.warning("扩展 tool_call 钩子异常: %s", exc)
        return None

    async def apply_context(self, messages: list[Message]) -> list[Message]:
        """context 钩子：返回新列表则替换，None/异常保持原样。"""
        for hook in self._handlers.get("context", []):
            try:
                result = hook(messages)
                if inspect.isawaitable(result):
                    result = await result
                if result is not None:
                    messages = list(result)
            except Exception as exc:
                logger.warning("扩展 context 钩子异常: %s", exc)
        return messages

    async def run_before_compaction(self, payload: dict) -> dict | None:
        """before_compaction 钩子：合并各 handler 的决策。

        返回 ``{"block": True}`` 阻断本次压缩，``{"instructions": str}`` 为摘要
        注入附加要求。多个 handler 的返回按序浅合并。
        """
        merged: dict | None = None
        for hook in self._handlers.get("before_compaction", []):
            try:
                result = hook(payload)
                if inspect.isawaitable(result):
                    result = await result
                if isinstance(result, dict):
                    merged = {**(merged or {}), **result}
            except Exception as exc:
                logger.warning("扩展 before_compaction 钩子异常: %s", exc)
        return merged

    async def run_command(self, name: str, payload: dict) -> dict | None:
        """扩展命令：未注册返回 None（WS 层回落 unknown_cmd）。"""
        fn = self._commands.get(name)
        if fn is None:
            return None
        result = fn(payload)
        if inspect.isawaitable(result):
            result = await result
        return dict(result or {})
