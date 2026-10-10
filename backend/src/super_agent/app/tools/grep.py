"""grep 工具——工作区内正则搜索（递归），输出 file:line: text。"""

import re
from pathlib import Path

from super_agent.agent.types import AgentTool, AgentToolResult
from super_agent.app.workspace import Workspace, WorkspaceError

MAX_HITS = 200
MAX_LINE_CHARS = 400


class GrepTool(AgentTool):
    name = "grep"
    description = (
        "在工作区内按正则表达式递归搜索文本文件，输出 `相对路径:行号: 内容`。"
        "include 用 glob 过滤文件名（如 `*.py`）；ignore_case 忽略大小写。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Python 正则表达式"},
            "path": {"type": "string", "description": "搜索起点目录，默认工作区根"},
            "include": {"type": "string", "description": "文件名 glob 过滤，如 *.py"},
            "ignore_case": {"type": "boolean", "description": "忽略大小写，默认 false"},
        },
        "required": ["pattern"],
    }
    execution_mode = "parallel"

    def __init__(self, workspace: Workspace) -> None:
        self._workspace = workspace

    async def execute(self, tool_call_id, params, signal, on_update) -> AgentToolResult:
        pattern = params.get("pattern")
        if not isinstance(pattern, str) or pattern == "":
            return AgentToolResult(content="参数错误: pattern 必须是非空正则", is_error=True)
        try:
            base = self._workspace.resolve(str(params.get("path") or ""))
        except WorkspaceError as exc:
            return AgentToolResult(content=str(exc), is_error=True)
        try:
            regex = re.compile(pattern, re.IGNORECASE if params.get("ignore_case") else 0)
        except re.error as exc:
            return AgentToolResult(content=f"正则编译失败: {exc}", is_error=True)
        if not base.exists():
            return AgentToolResult(content=f"路径不存在: {base}", is_error=True)

        include = params.get("include")
        hits: list[str] = []
        truncated = False
        for file in self._iter_files(base):
            if isinstance(include, str) and include and not file.match(include):
                continue
            try:
                text = file.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue  # 二进制或不可读文件跳过
            for lineno, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    rel = file.relative_to(self._workspace.root).as_posix()
                    shown = line.strip()[:MAX_LINE_CHARS]
                    hits.append(f"{rel}:{lineno}: {shown}")
                    if len(hits) >= MAX_HITS:
                        truncated = True
                        break
            if truncated:
                break
        if not hits:
            return AgentToolResult(content="无匹配")
        tail = "\n…（结果已截断）" if truncated else ""
        return AgentToolResult(content="\n".join(hits) + tail)

    def _iter_files(self, base: Path):
        if base.is_file():
            yield base
            return
        for path in sorted(base.rglob("*")):
            if path.is_file():
                yield path
