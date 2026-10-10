"""ls 工具——列出工作区目录内容（目录在前，带大小）。"""

from super_agent.agent.types import AgentTool, AgentToolResult
from super_agent.app.workspace import Workspace, WorkspaceError


class LsTool(AgentTool):
    name = "ls"
    description = "列出工作区内某目录的内容：子目录带 `/` 后缀在前，文件附字节数。path 默认工作区根。"
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "目录路径，默认工作区根"},
        },
        "required": [],
    }
    execution_mode = "parallel"

    def __init__(self, workspace: Workspace) -> None:
        self._workspace = workspace

    async def execute(self, tool_call_id, params, signal, on_update) -> AgentToolResult:
        try:
            base = self._workspace.resolve(str(params.get("path") or ""))
        except WorkspaceError as exc:
            return AgentToolResult(content=str(exc), is_error=True)
        if not base.exists():
            return AgentToolResult(content=f"路径不存在: {base}", is_error=True)
        if not base.is_dir():
            return AgentToolResult(content=f"不是目录: {base}", is_error=True)

        try:
            entries = sorted(base.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError as exc:
            return AgentToolResult(content=f"列目录失败: {exc}", is_error=True)
        if not entries:
            return AgentToolResult(content="（空目录）")
        lines = []
        for entry in entries:
            if entry.is_dir():
                lines.append(f"{entry.name}/")
            else:
                try:
                    size = entry.stat().st_size
                except OSError:
                    size = 0
                lines.append(f"{entry.name}  ({size} B)")
        return AgentToolResult(content="\n".join(lines))
