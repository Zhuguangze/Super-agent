"""write 工具——整文件写入（覆盖），父目录自动创建。"""

import asyncio

from super_agent.agent.types import AgentTool, AgentToolResult
from super_agent.app.workspace import Workspace, WorkspaceError


class WriteTool(AgentTool):
    name = "write"
    description = (
        "把 content 整体写入工作区内的文件（覆盖已有内容），父目录不存在则自动创建。"
        "修改现有文件的一部分请优先用 edit。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "工作区内目标文件路径"},
            "content": {"type": "string", "description": "完整文件内容（UTF-8 文本）"},
        },
        "required": ["path", "content"],
    }
    execution_mode = "parallel"

    def __init__(self, workspace: Workspace) -> None:
        self._workspace = workspace

    async def execute(self, tool_call_id, params, signal, on_update) -> AgentToolResult:
        content = params.get("content")
        if not isinstance(content, str):
            return AgentToolResult(content="参数错误: content 必须是字符串", is_error=True)
        try:
            path = self._workspace.resolve(str(params.get("path", "")))
        except WorkspaceError as exc:
            return AgentToolResult(content=str(exc), is_error=True)
        if path.is_dir():
            return AgentToolResult(content=f"目标是目录: {path}", is_error=True)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(path.write_text, content, encoding="utf-8")
        except OSError as exc:
            return AgentToolResult(content=f"写入失败: {exc}", is_error=True)
        lines = content.count("\n") + (0 if content.endswith("\n") or not content else 1)
        return AgentToolResult(content=f"已写入 {path.name}（{len(content.encode('utf-8'))} 字节，{lines} 行）")
