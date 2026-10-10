"""Seam：skills 模块——SKILL.md 解析、两层加载、索引文本与 load_skill 工具（票 18）。

对齐 CONTEXT.md 词条 Skill：声明式知识包，索引常驻系统提示，
全文经 load_skill 工具按需加载；全局 + 工作区两层，同名覆盖。
"""

from super_agent.app.skills import (
    SKILL_INDEX_MAX_CHARS,
    Skill,
    SkillTool,
    load_skills,
    parse_skill,
    skills_index_text,
)


def _skill_md(name: str | None, description: str | None, body: str) -> str:
    lines = ["---"]
    if name is not None:
        lines.append(f"name: {name}")
    if description is not None:
        lines.append(f"description: {description}")
    lines += ["---", "", body]
    return "\n".join(lines)


def test_parse_skill_reads_frontmatter_and_body() -> None:
    skill = parse_skill(_skill_md("deploy-checklist", "部署前的检查清单", "1. 跑测试\n2. 打 tag"))
    assert skill is not None
    assert skill.name == "deploy-checklist"
    assert skill.description == "部署前的检查清单"
    assert "打 tag" in skill.body


def test_parse_skill_name_falls_back_to_directory_name() -> None:
    """frontmatter 没写 name 时回退目录名——少写一个字段也能用。"""
    skill = parse_skill(_skill_md(None, "部署清单", "步骤"), fallback_name="deploy")
    assert skill is not None
    assert skill.name == "deploy"


def test_parse_skill_rejects_missing_description() -> None:
    """description 是索引的摘要来源，缺失即无效（坏格式隔离的依据）。"""
    assert parse_skill(_skill_md("x", None, "正文")) is None


def test_parse_skill_rejects_missing_frontmatter() -> None:
    assert parse_skill("没有 frontmatter 的纯文本") is None
    assert parse_skill("---\n只有一半") is None


# ---- load_skills：两层合并、同名覆盖、坏格式隔离 ----


def _write_skill(root, name: str, description: str, body: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(_skill_md(name, description, body), encoding="utf-8")


def test_load_skills_merges_layers_with_workspace_override(tmp_path) -> None:
    global_dir = tmp_path / "global"
    _write_skill(global_dir / "deploy", "deploy", "全局部署清单", "1. 全局步骤")
    _write_skill(global_dir / "lint", "lint", "代码规范", "规范正文")
    workspace = tmp_path / "ws"
    _write_skill(workspace / "deploy", "deploy", "工作区部署清单", "1. 工作区步骤")
    # 坏格式目录（缺 description）：跳过，不拖垮其余
    (workspace / "broken").mkdir(parents=True, exist_ok=True)
    (workspace / "broken" / "SKILL.md").write_text(
        _skill_md("broken", None, "无描述"), encoding="utf-8"
    )

    skills = load_skills(global_dir, workspace)
    by_name = {s.name: s for s in skills}
    assert set(by_name) == {"deploy", "lint"}  # broken 被隔离
    assert by_name["deploy"].description == "工作区部署清单"  # 工作区覆盖全局
    assert by_name["deploy"].body == "1. 工作区步骤"
    assert by_name["lint"].description == "代码规范"


def test_load_skills_uses_directory_name_as_fallback(tmp_path) -> None:
    """frontmatter 省略 name 时以目录名为加载键。"""
    global_dir = tmp_path / "global"
    _write_skill(global_dir / "deploy", None, "部署清单", "步骤")
    skills = load_skills(global_dir, None)
    assert [s.name for s in skills] == ["deploy"]


def test_load_skills_missing_dirs_are_fine(tmp_path) -> None:
    assert load_skills(tmp_path / "nope", tmp_path / "nada") == []
    assert load_skills(None, None) == []


# ---- skills_index_text：系统提示注入的索引段 ----


def test_index_text_lists_skills_with_guidance() -> None:
    skills = [
        Skill(name="deploy", description="部署清单", body="x"),
        Skill(name="lint", description="代码规范", body="y"),
    ]
    text = skills_index_text(skills)
    assert "- deploy — 部署清单" in text
    assert "- lint — 代码规范" in text
    assert "load_skill" in text  # 引导 agent 按需加载全文


def test_index_text_truncates_overlong_lists() -> None:
    skills = [Skill(name=f"s{i}", description="描述" * 100, body="x") for i in range(60)]
    text = skills_index_text(skills)
    assert len(text) <= SKILL_INDEX_MAX_CHARS + 60  # 截断上限 + 提示行余量
    assert "截断" in text


# ---- SkillTool：load_skill 按需加载全文 ----


async def test_load_skill_reads_body_by_name() -> None:
    tool = SkillTool([Skill(name="deploy", description="部署清单", body="1. 跑测试\n2. 打 tag")])
    result = await tool.execute("t1", {"name": "deploy"}, None, None)
    assert result.is_error is False
    assert "打 tag" in result.content


async def test_load_skill_unknown_name_is_error_with_available_names() -> None:
    """未知名 → is_error，并列出可用名字（模型据此自纠错）。"""
    tool = SkillTool([Skill(name="deploy", description="d", body="x")])
    result = await tool.execute("t1", {"name": "ghost"}, None, None)
    assert result.is_error is True
    assert "ghost" in result.content
    assert "deploy" in result.content


async def test_load_skill_missing_name_param_is_error() -> None:
    tool = SkillTool([Skill(name="deploy", description="d", body="x")])
    result = await tool.execute("t1", {}, None, None)
    assert result.is_error is True


# ---- 票：手动选择技能（Claude Code 式）——清单 API ----


def test_chat_skills_list(tmp_path) -> None:
    from super_agent.app.chat import ChatService

    ws_root = tmp_path / "workspaces"
    global_dir = tmp_path / "skills"
    _write_skill(global_dir / "deploy", "deploy", "部署清单", "步骤")
    user_ws = ws_root / "1" / "skills" / "personal"
    _write_skill(user_ws, "personal", "个人小抄", "私货")

    chat = ChatService(
        None, workspace_root=ws_root, skills_dir=global_dir, default_model=None
    )
    skills = chat.skills_list(1)
    by_name = {s["name"]: s for s in skills}
    assert by_name["deploy"]["description"] == "部署清单"
    assert by_name["personal"]["description"] == "个人小抄"
