"""edit 工具——精确字符串替换（Claude Code 风格）：old_string 必须唯一，除非 replace_all。"""

from super_agent.agent.types import AgentTool, AgentToolResult
from super_agent.app.workspace import Workspace, WorkspaceError


class EditTool(AgentTool):
    name = "edit"
    description = (
        "对工作区内文本文件做精确替换：old_string 必须在文件中唯一出现，"
        "否则报错（可提供更多上下文使其唯一，或置 replace_all 全部替换）。"
        "先用 read 查看内容再编辑。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "工作区内目标文件路径"},
            "old_string": {"type": "string", "description": "待替换的原文（需唯一）"},
            "new_string": {"type": "string", "description": "替换后的文本"},
            "replace_all": {"type": "boolean", "description": "替换全部出现，默认 false"},
        },
        "required": ["path", "old_string", "new_string"],
    }
    execution_mode = "parallel"

    def __init__(self, workspace: Workspace) -> None:
        self._workspace = workspace

    async def execute(self, tool_call_id, params, signal, on_update) -> AgentToolResult:
        old_string = params.get("old_string")
        new_string = params.get("new_string")
        if not isinstance(old_string, str) or not isinstance(new_string, str):
            return AgentToolResult(content="参数错误: old_string/new_string 必须是字符串", is_error=True)
        if old_string == "":
            return AgentToolResult(content="参数错误: old_string 不能为空（新建文件用 write）", is_error=True)
        try:
            path = self._workspace.resolve(str(params.get("path", "")))
        except WorkspaceError as exc:
            return AgentToolResult(content=str(exc), is_error=True)
        if not path.exists() or path.is_dir():
            return AgentToolResult(content=f"文件不存在: {path}", is_error=True)
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as exc:
            return AgentToolResult(content=f"读取失败: {exc}", is_error=True)

        count = text.count(old_string)
        if count == 0:
            return AgentToolResult(content=f"未找到待替换文本（先 read 核对原文）: {old_string[:80]!r}", is_error=True)
        replace_all = bool(params.get("replace_all", False))
        if count > 1 and not replace_all:
            return AgentToolResult(
                content=f"old_string 出现 {count} 次，请提供更多上下文使其唯一，或置 replace_all=true",
                is_error=True,
            )
        new_text = text.replace(old_string, new_string) if replace_all else text.replace(old_string, new_string, 1)
        try:
            path.write_text(new_text, encoding="utf-8")
        except OSError as exc:
            return AgentToolResult(content=f"写入失败: {exc}", is_error=True)
        replaced = count if replace_all else 1
        return AgentToolResult(content=f"已编辑 {path.name}（替换 {replaced} 处）")
