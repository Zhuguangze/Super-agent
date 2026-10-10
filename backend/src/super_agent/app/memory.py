"""长期记忆（ADR-0003，ticket 11）——文件式：索引 + 单事实文件。

``data/memory/<user_id>/`` 下 MEMORY.md 索引 + ``<slug>.md``（frontmatter +
正文）。构造即隔离：每个 User 一个 MemoryStore 实例，目录边界即边界。
agent 经 memory 工具自主读写；索引全文常驻系统提示（设截断上限），
细节由 agent 按需 view——透明可审计，零新依赖。
"""

import re
from pathlib import Path

MEMORY_INDEX_NAME = "MEMORY.md"
MEMORY_INDEX_MAX_CHARS = 2048
AGENTS_FILE_NAME = "AGENTS.md"
AGENTS_MAX_CHARS = 2048
VALID_TYPES = ("user", "feedback", "project", "reference")

_INDEX_TEMPLATE = "# Memory Index\n"


def to_slug(name: str) -> str:
    """任意输入 → kebab-case slug（中文转拼音不在 v1 范围，保留中文；只清洗分隔与非法字符）。"""
    cleaned = re.sub(r"\.md$", "", name.strip())
    cleaned = re.sub(r"[^\w一-鿿-]+", "-", cleaned)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-.").lower()
    return cleaned or "memory"


class MemoryError(Exception):
    """记忆操作失败——工具层转 is_error 结果。"""


class MemoryStore:
    """单个用户的记忆读写与索引维护（root = data/memory/<user_id>）。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    # ---- 路径 ----

    def _fact_path(self, name: str) -> Path:
        slug = to_slug(name)
        if slug == MEMORY_INDEX_NAME.removesuffix(".md").lower():
            raise MemoryError("不能操作索引文件本身")
        return self.root / f"{slug}.md"

    @property
    def index_path(self) -> Path:
        return self.root / MEMORY_INDEX_NAME

    # ---- 读 ----

    def index_text(self) -> str:
        """MEMORY.md 全文（常驻系统提示用），超长截断。"""
        try:
            text = self.index_path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""
        if len(text) > MEMORY_INDEX_MAX_CHARS:
            text = text[:MEMORY_INDEX_MAX_CHARS] + "\n…（索引过长已截断，按需 view 细节）"
        return text

    def read_fact(self, name: str) -> str:
        try:
            return self._fact_path(name).read_text(encoding="utf-8")
        except FileNotFoundError:
            raise MemoryError(f"记忆不存在: {name}") from None
        except OSError as exc:
            raise MemoryError(f"读取失败: {exc}") from None

    # ---- 写 ----

    def create(self, *, name: str, description: str, type: str, content: str) -> str:
        """写单事实文件（frontmatter + 正文）并在索引中登记一行。"""
        if type not in VALID_TYPES:
            raise MemoryError(f"type 必须是 {'/'.join(VALID_TYPES)} 之一")
        if not description.strip():
            raise MemoryError("description 不能为空（索引用它做摘要）")
        path = self._fact_path(name)
        if path.exists():
            raise MemoryError(f"记忆已存在（用 str_replace 修改）: {name}")
        slug = path.stem
        document = (
            "---\n"
            f"name: {slug}\n"
            f"description: {description.strip()}\n"
            "metadata:\n"
            f"  type: {type}\n"
            "---\n\n"
            f"{content.strip()}\n"
        )
        try:
            path.write_text(document, encoding="utf-8")
        except OSError as exc:
            raise MemoryError(f"写入失败: {exc}") from None
        self._upsert_index_line(slug, description.strip())
        return slug

    def str_replace(self, name: str, old_string: str, new_string: str) -> str:
        """对事实文件全文精确替换；description 变更时同步刷新索引行。"""
        if not old_string:
            raise MemoryError("old_string 不能为空")
        path = self._fact_path(name)
        text = self.read_fact(name)
        count = text.count(old_string)
        if count == 0:
            raise MemoryError("未找到待替换文本（先 view 核对原文）")
        if count > 1:
            raise MemoryError(f"old_string 出现 {count} 次，需提供更多上下文使其唯一")
        new_text = text.replace(old_string, new_string, 1)
        try:
            path.write_text(new_text, encoding="utf-8")
        except OSError as exc:
            raise MemoryError(f"写入失败: {exc}") from None
        description = _frontmatter_field(new_text, "description")
        if description:
            self._upsert_index_line(path.stem, description)
        return path.stem

    def delete(self, name: str) -> str:
        """删除事实文件并从索引移除。"""
        path = self._fact_path(name)
        if not path.exists():
            raise MemoryError(f"记忆不存在: {name}")
        try:
            path.unlink()
        except OSError as exc:
            raise MemoryError(f"删除失败: {exc}") from None
        self._remove_index_line(path.stem)
        return path.stem

    # ---- 索引维护 ----

    def _upsert_index_line(self, slug: str, description: str) -> None:
        lines = self._index_lines()
        entry = f"- [{slug}]({slug}.md) — {description}"
        lines = [line for line in lines if not line.startswith(f"- [{slug}](")]
        lines.append(entry)
        self._write_index(lines)

    def _remove_index_line(self, slug: str) -> None:
        lines = [
            line for line in self._index_lines() if not line.startswith(f"- [{slug}](")
        ]
        self._write_index(lines)

    def _index_lines(self) -> list[str]:
        try:
            text = self.index_path.read_text(encoding="utf-8")
        except OSError:
            return [_INDEX_TEMPLATE.strip()]
        lines = [line for line in text.splitlines() if line.strip()]
        if not lines:
            lines = [_INDEX_TEMPLATE.strip()]
        return lines

    def _write_index(self, lines: list[str]) -> None:
        try:
            self.index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError as exc:
            raise MemoryError(f"索引写入失败: {exc}") from None


def read_agents_file(workspace_root: Path) -> str:
    """工作区级记忆：``<workspace>/AGENTS.md``（spec §4.6），存在才返回，超长截断。"""
    try:
        content = (workspace_root / AGENTS_FILE_NAME).read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if len(content) > AGENTS_MAX_CHARS:
        content = content[:AGENTS_MAX_CHARS] + "\n…（已截断）"
    return content


def _frontmatter_field(document: str, field: str) -> str | None:
    head = document.split("---", 2)[1] if document.startswith("---") else ""
    match = re.search(rf"(?m)^{field}: (.+)$", head)
    return match.group(1).strip() if match else None
