"""memory 工具——单工具多操作（view / create / str_replace / delete，ADR-0003）。

Claude Code memory 工具风格：索引（MEMORY.md）常驻系统提示，agent 据索引
按需 view 细节；create/str_replace/delete 自主维护记忆文件与索引的一致性。
"""

from super_agent.agent.types import AgentTool, AgentToolResult
from super_agent.app.memory import MemoryStore, MemoryError, VALID_TYPES


class MemoryTool(AgentTool):
    name = "memory"
    description = (
        "长期记忆维护。operation 取值："
        "view（name 省略看索引 MEMORY.md，给定看单条记忆全文）；"
        "create（name/description/type/content 新建一条记忆）；"
        "str_replace（name/old_string/new_string 精确修改）；"
        "delete（name 删除）。"
        "用户说'记住…'时主动落一条记忆；回答前先看索引里有没有相关记忆。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": ["view", "create", "str_replace", "delete"]},
            "name": {"type": "string", "description": "记忆 slug（如 prefers-python）"},
            "description": {"type": "string", "description": "create：一行摘要（索引展示用）"},
            "type": {"type": "string", "enum": list(VALID_TYPES), "description": "create：记忆类型"},
            "content": {"type": "string", "description": "create：记忆正文（frontmatter 之外）"},
            "old_string": {"type": "string", "description": "str_replace：待替换原文"},
            "new_string": {"type": "string", "description": "str_replace：替换后文本"},
        },
        "required": ["operation"],
    }
    execution_mode = "sequential"

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    async def execute(self, tool_call_id, params, signal, on_update) -> AgentToolResult:
        operation = params.get("operation")
        name = params.get("name")
        try:
            if operation == "view":
                if isinstance(name, str) and name.strip():
                    return AgentToolResult(content=self._store.read_fact(name.strip()))
                index = self._store.index_text()
                return AgentToolResult(content=index or "（记忆索引为空）")
            if operation == "create":
                slug = self._store.create(
                    name=str(name or ""),
                    description=str(params.get("description") or ""),
                    type=str(params.get("type") or ""),
                    content=str(params.get("content") or ""),
                )
                return AgentToolResult(content=f"已记住：{slug}（索引已更新）")
            if operation == "str_replace":
                slug = self._store.str_replace(
                    str(name or ""),
                    str(params.get("old_string") or ""),
                    str(params.get("new_string") or ""),
                )
                return AgentToolResult(content=f"已更新：{slug}")
            if operation == "delete":
                slug = self._store.delete(str(name or ""))
                return AgentToolResult(content=f"已删除：{slug}")
            return AgentToolResult(
                content=f"参数错误: operation 必须是 view/create/str_replace/delete，得到 {operation!r}",
                is_error=True,
            )
        except MemoryError as exc:
            return AgentToolResult(content=str(exc), is_error=True)
