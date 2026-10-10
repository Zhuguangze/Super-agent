"""find 工具——按 glob 模式查找工作区文件。"""

from super_agent.agent.types import AgentTool, AgentToolResult
from super_agent.app.workspace import Workspace, WorkspaceError

MAX_RESULTS = 200


class FindTool(AgentTool):
    name = "find"
    description = (
        "在工作区内按 glob 模式查找文件路径（如 `**/*.py`、`docs/*.md`），"
        "返回工作区内相对路径列表。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "glob 模式，如 **/*.py"},
            "path": {"type": "string", "description": "查找起点目录，默认工作区根"},
        },
        "required": ["pattern"],
    }
    execution_mode = "parallel"

    def __init__(self, workspace: Workspace) -> None:
        self._workspace = workspace

    async def execute(self, tool_call_id, params, signal, on_update) -> AgentToolResult:
        pattern = params.get("pattern")
        if not isinstance(pattern, str) or pattern == "":
            return AgentToolResult(content="参数错误: pattern 必须是非空 glob", is_error=True)
        try:
            base = self._workspace.resolve(str(params.get("path") or ""))
        except WorkspaceError as exc:
            return AgentToolResult(content=str(exc), is_error=True)
        if not base.exists():
            return AgentToolResult(content=f"路径不存在: {base}", is_error=True)

        matches: list[str] = []
        try:
            for path in sorted(base.glob(pattern)):
                if path.is_file():
                    matches.append(path.relative_to(self._workspace.root).as_posix())
                    if len(matches) >= MAX_RESULTS:
                        break
        except (ValueError, OSError) as exc:
            return AgentToolResult(content=f"查找失败: {exc}", is_error=True)
        if not matches:
            return AgentToolResult(content=f"无匹配: {pattern}")
        return AgentToolResult(content="\n".join(matches))
