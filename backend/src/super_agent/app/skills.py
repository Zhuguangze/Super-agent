"""Skill 系统（票 18，spec §8 V2-3）——声明式知识包：SKILL.md + 按需加载。

对齐 CONTEXT.md 词条 Skill：教 agent"怎么做好某类任务"；索引常驻系统提示
（截断上限，对齐 memory 注入先例），全文由 agent 经 load_skill 工具按需加载。
两层来源：全局 ``data/skills/``（管理员维护，全员共享）+ 工作区
``<workspace>/skills/``（用户自建），工作区同名覆盖全局。
与 Extension（代码级行为）、MCP Tool（进程级工具）三分互补。
"""

import re
from dataclasses import dataclass
from pathlib import Path

from super_agent.agent.types import AgentTool, AgentToolResult

SKILL_FILE_NAME = "SKILL.md"
SKILL_INDEX_MAX_CHARS = 2048


@dataclass
class Skill:
    """一个已解析的技能：name 即加载键（load_skill 的入参）。"""

    name: str
    description: str
    body: str


def parse_skill(text: str, *, fallback_name: str = "") -> Skill | None:
    """SKILL.md → Skill。frontmatter 必需；description 必需（索引用它做摘要）；
    name 缺省回退 fallback_name（目录名）。坏格式返回 None，由调用方隔离。"""
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    name: str | None = None
    description: str | None = None
    for line in parts[1].splitlines():
        m = re.match(r"^\s*(\w+)\s*:\s*(.+?)\s*$", line)
        if not m:
            continue
        if m.group(1) == "name":
            name = m.group(2)
        elif m.group(1) == "description":
            description = m.group(2)
    if not description:
        return None
    return Skill(
        name=(name or fallback_name).strip(),
        description=description.strip(),
        body=parts[2].strip(),
    )


def _scan_dir(root: Path | None) -> list[Skill]:
    """单层目录扫描：每个子目录一份 SKILL.md；缺文件/坏格式跳过。"""
    if root is None:
        return []
    skills: list[Skill] = []
    try:
        children = sorted(root.iterdir())
    except OSError:
        return []
    for child in children:
        if not child.is_dir():
            continue
        try:
            text = (child / SKILL_FILE_NAME).read_text(encoding="utf-8")
        except OSError:
            continue
        skill = parse_skill(text, fallback_name=child.name)
        if skill is not None:
            skills.append(skill)
    return skills


def load_skills(global_dir: Path | None, workspace_dir: Path | None) -> list[Skill]:
    """两层合并：全局打底，工作区同名覆盖（CONTEXT.md Skill 词条的作用域语义）。

    每 run 调用（对齐 extensions 的 per-run 加载先例）——新增/修改 skill
    下次 run 生效，无需重启。目录缺失/不可读视为无技能。
    """
    merged = {s.name: s for s in _scan_dir(global_dir)}
    for s in _scan_dir(workspace_dir):
        merged[s.name] = s
    return list(merged.values())


def skills_index_text(skills: list[Skill]) -> str:
    """系统提示注入的索引段（票 18）：只列 name + description，全文按需加载。

    超长截断（对齐 memory 注入先例的 2048 上限）——索引是常驻成本，
    细节留在 SKILL.md 里等 load_skill。
    """
    lines = [
        "## 可用技能（Skills）",
        "与当前任务相关的技能：先用 load_skill 工具读取全文，再按其步骤执行。",
        "",
    ]
    lines += [f"- {s.name} — {s.description}" for s in skills]
    text = "\n".join(lines)
    if len(text) > SKILL_INDEX_MAX_CHARS:
        text = text[:SKILL_INDEX_MAX_CHARS] + "\n…（技能索引过长已截断）"
    return text


class SkillTool(AgentTool):
    """load_skill 工具（票 18）：按名读技能全文。

    构造时持有本 run 已加载的技能列表（per-run 新建，天然热更）；
    只读、无副作用，默认并行执行。
    """

    name = "load_skill"
    description = (
        "读取一个技能（Skill）的完整内容。索引里列出的技能只在名称层面常驻，"
        "执行相关任务前先用本工具加载全文，再按其步骤行事。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "技能名（索引里 '- name — …' 的 name）"},
        },
        "required": ["name"],
    }

    def __init__(self, skills: list[Skill]) -> None:
        self._skills = {s.name: s for s in skills}

    async def execute(self, tool_call_id, params, signal, on_update) -> AgentToolResult:
        name = params.get("name")
        if not isinstance(name, str) or not name.strip():
            return AgentToolResult(content="参数错误: name 必须是技能名", is_error=True)
        skill = self._skills.get(name.strip())
        if skill is None:
            available = "、".join(self._skills) or "（无）"
            return AgentToolResult(
                content=f"技能不存在: {name}（可用: {available}）", is_error=True
            )
        return AgentToolResult(content=skill.body)
