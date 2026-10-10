"""read 工具——读文本文件（行号前缀便于 edit 定位）；图片文件返回 image block（票 23）。"""

import asyncio
import base64

from super_agent.agent.types import AgentTool, AgentToolResult
from super_agent.app.workspace import Workspace, WorkspaceError

DEFAULT_LIMIT = 2000
IMAGE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
IMAGE_MAX_BYTES = 5 * 1024 * 1024


class ReadTool(AgentTool):
    name = "read"
    description = (
        "读取工作区内文件。文本文件带行号前缀（cat -n 风格），"
        "支持 offset（起始行，从 1 计）与 limit（最多行数）；"
        "图片文件（png/jpg/gif/webp，≤5MB）返回图片本体。目录返回错误。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "工作区内相对路径（或工作区内绝对路径）"},
            "offset": {"type": "integer", "description": "起始行号（1 计），默认从头"},
            "limit": {"type": "integer", "description": f"最多读取行数，默认 {DEFAULT_LIMIT}"},
        },
        "required": ["path"],
    }
    execution_mode = "parallel"

    def __init__(self, workspace: Workspace) -> None:
        self._workspace = workspace

    async def execute(self, tool_call_id, params, signal, on_update) -> AgentToolResult:
        try:
            path = self._workspace.resolve(str(params.get("path", "")))
            offset = max(1, int(params.get("offset") or 1))
            limit = max(1, int(params.get("limit") or DEFAULT_LIMIT))
        except (WorkspaceError, ValueError) as exc:
            return AgentToolResult(content=f"参数错误: {exc}", is_error=True)
        if not path.exists():
            return AgentToolResult(content=f"文件不存在: {path}", is_error=True)
        if path.is_dir():
            return AgentToolResult(content=f"是目录不是文件（用 ls 列出）: {path}", is_error=True)
        media_type = IMAGE_MEDIA_TYPES.get(path.suffix.lower())
        if media_type is not None:
            return await self._read_image(path, media_type)
        try:
            text = await asyncio.to_thread(path.read_text, encoding="utf-8")
        except UnicodeDecodeError:
            return AgentToolResult(content=f"非 UTF-8 文本（可能是二进制文件）: {path}", is_error=True)
        except OSError as exc:
            return AgentToolResult(content=f"读取失败: {exc}", is_error=True)

        lines = text.splitlines()
        start = min(offset - 1, len(lines))
        selected = lines[start : start + limit]
        numbered = "\n".join(f"{start + i + 1}\t{line}" for i, line in enumerate(selected))
        note = ""
        if start + limit < len(lines):
            note = f"\n…（共 {len(lines)} 行，已截断，用 offset 继续读）"
        return AgentToolResult(content=f"{numbered}{note}" if numbered else "（空文件）")

    async def _read_image(self, path, media_type: str) -> AgentToolResult:
        """图片 → image block（base64，≤5MB）；content 为文本描述（回退/摘要用）。"""
        try:
            data = await asyncio.to_thread(path.read_bytes)
        except OSError as exc:
            return AgentToolResult(content=f"读取失败: {exc}", is_error=True)
        if len(data) > IMAGE_MAX_BYTES:
            return AgentToolResult(
                content=f"图片过大（{len(data) / 1024 / 1024:.1f}MB），上限 5MB: {path.name}",
                is_error=True,
            )
        return AgentToolResult(
            content=f"[图片: {path.name}，{media_type}，{len(data) // 1024}KB]",
            blocks=[
                {"type": "image", "media_type": media_type, "data": base64.b64encode(data).decode("ascii")}
            ],
        )
