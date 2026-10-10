"""内置文件/网页工具集（spec §4.4，每工具一文件、工厂函数风格对齐 pi）。

工具实例按用户创建——Workspace 是隔离边界，一切文件路径先经它 resolve。
"""

from super_agent.agent.types import AgentTool
from super_agent.app.workspace import Workspace
from super_agent.app.tools.bash import BashTool
from super_agent.app.tools.edit import EditTool
from super_agent.app.tools.fetch import FetchTool
from super_agent.app.tools.find import FindTool
from super_agent.app.tools.grep import GrepTool
from super_agent.app.tools.ls import LsTool
from super_agent.app.tools.memory import MemoryTool
from super_agent.app.tools.read import ReadTool
from super_agent.app.tools.search import TavilySearchProvider, WebSearchTool
from super_agent.app.tools.write import WriteTool

__all__ = [
    "BashTool",
    "EditTool",
    "FetchTool",
    "FindTool",
    "GrepTool",
    "LsTool",
    "MemoryTool",
    "ReadTool",
    "TavilySearchProvider",
    "WebSearchTool",
    "WriteTool",
    "make_file_tools",
]

# 文件类工具默认免审批（ADR-0001：bash 是 v1 唯一的默认审批工具）
DEFAULT_ALLOWLIST = frozenset(
    {"read", "write", "edit", "grep", "find", "ls", "fetch", "web_search", "memory"}
)


def make_file_tools(workspace: Workspace, bash_timeout: float = 60.0) -> list[AgentTool]:
    """按用户工作区实例化九个内置工具（文件七个 + bash + web_search）。

    web_search 的搜索后端默认 Tavily（key 缺失时执行报错，工具照常注册）；
    bash 的默认超时来自 Settings（bash_timeout）。
    """
    return [
        ReadTool(workspace),
        WriteTool(workspace),
        EditTool(workspace),
        GrepTool(workspace),
        FindTool(workspace),
        LsTool(workspace),
        FetchTool(),
        BashTool(workspace, default_timeout=bash_timeout),
        WebSearchTool(TavilySearchProvider()),
    ]
