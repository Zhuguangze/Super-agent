"""app/tools——七个内置工具：正常路径、错误回填与越界拒绝。"""

import base64

import httpx
import pytest

from super_agent.app.tools import make_file_tools
from super_agent.app.tools.fetch import FetchTool, extract_title, html_to_text
from super_agent.app.workspace import user_workspace

TOOLS_BY_NAME: dict = {}


@pytest.fixture()
def ws_root(tmp_path):
    return tmp_path / "workspaces"


@pytest.fixture()
def tools(ws_root):
    made = {t.name: t for t in make_file_tools(user_workspace(ws_root, 1))}
    TOOLS_BY_NAME.clear()
    TOOLS_BY_NAME.update(made)
    return made


async def _run(tool, **params):
    return await tool.execute("t1", params, None, None)


# ---- 九工具齐全 + schema ----


def test_nine_tools_present(tools) -> None:
    assert set(tools) == {"read", "write", "edit", "grep", "find", "ls", "fetch", "bash", "web_search"}
    for tool in tools.values():
        assert tool.parameters.get("type") == "object"
        assert "required" in tool.parameters


# ---- read ----


async def test_read_returns_numbered_lines(tools, ws_root) -> None:
    (ws_root / "1").joinpath("a.txt").write_text("第一行\n第二行\n", encoding="utf-8")
    result = await _run(tools["read"], path="a.txt")


# ---- 票 23：read 图片生产者（image block） ----

_PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c626001000000ffff030000060005"
    "57bfabd40000000049454e44ae426082"
)


async def test_read_image_returns_image_block(tools, ws_root) -> None:
    """read 图片文件 → image block（base64）+ 文本描述。"""
    (ws_root / "1").joinpath("chart.png").write_bytes(_PNG_1PX)
    result = await _run(tools["read"], path="chart.png")
    assert result.is_error is False
    assert result.blocks is not None
    image = result.blocks[0]
    assert image["type"] == "image"
    assert image["media_type"] == "image/png"
    assert base64.b64decode(image["data"]) == _PNG_1PX
    assert "chart.png" in result.content  # 文本描述（回退/摘要用）


async def test_read_image_size_limit(tools, ws_root) -> None:
    """超限图片（>5MB）→ 清晰错误，不落富块。"""
    big = ws_root / "1" / "big.png"
    big.write_bytes(_PNG_1PX + b"\0" * (5 * 1024 * 1024))
    result = await _run(tools["read"], path="big.png")
    assert result.is_error is True
    assert "5" in result.content  # 提示大小上限
    assert result.blocks is None


async def test_read_other_binary_stays_error(tools, ws_root) -> None:
    """非图片路径零行为变化：不可解码二进制报错且无富块。"""
    (ws_root / "1").joinpath("data.bin").write_bytes(b"\xff\xfe\x00\x9c")  # 非法 UTF-8
    result = await _run(tools["read"], path="data.bin")
    assert result.is_error is True
    assert result.blocks is None


async def test_read_offset_limit_and_truncation(tools, ws_root) -> None:
    (ws_root / "1" / "a.txt").write_text("\n".join(f"l{i}" for i in range(1, 6)), encoding="utf-8")
    result = await _run(tools["read"], path="a.txt", offset=2, limit=2)
    assert result.content.splitlines() == ["2\tl2", "3\tl3", "…（共 5 行，已截断，用 offset 继续读）"]


async def test_read_errors_backfill_not_raise(tools, ws_root) -> None:
    (ws_root / "1" / "sub").mkdir()
    (ws_root / "1" / "bin.dat").write_bytes(b"\xff\xfe\x00")
    missing = await _run(tools["read"], path="nope.txt")
    assert missing.is_error and "文件不存在" in missing.content
    as_dir = await _run(tools["read"], path="sub")
    assert as_dir.is_error and "目录" in as_dir.content
    binary = await _run(tools["read"], path="bin.dat")
    assert binary.is_error and "UTF-8" in binary.content


# ---- write ----


async def test_write_creates_parents_and_reports(tools, ws_root) -> None:
    result = await _run(tools["write"], path="deep/dir/n.txt", content="内容\n")
    assert result.is_error is False
    assert (ws_root / "1" / "deep" / "dir" / "n.txt").read_text(encoding="utf-8") == "内容\n"
    assert "字节" in result.content


async def test_write_rejects_outside(tools) -> None:
    result = await _run(tools["write"], path="../evil.txt", content="x")
    assert result.is_error and "越出工作区" in result.content


# ---- edit ----


async def test_edit_replaces_unique_occurrence(tools, ws_root) -> None:
    (ws_root / "1" / "c.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    result = await _run(tools["edit"], path="c.py", old_string="y = 2", new_string="y = 3")
    assert result.is_error is False
    assert (ws_root / "1" / "c.py").read_text(encoding="utf-8") == "x = 1\ny = 3\n"


async def test_edit_ambiguous_requires_replace_all(tools, ws_root) -> None:
    (ws_root / "1" / "c.py").write_text("a\na\n", encoding="utf-8")
    ambiguous = await _run(tools["edit"], path="c.py", old_string="a", new_string="b")
    assert ambiguous.is_error and "2 次" in ambiguous.content
    all_replaced = await _run(tools["edit"], path="c.py", old_string="a", new_string="b", replace_all=True)
    assert all_replaced.is_error is False
    assert (ws_root / "1" / "c.py").read_text(encoding="utf-8") == "b\nb\n"


async def test_edit_missing_old_string(tools, ws_root) -> None:
    (ws_root / "1" / "c.py").write_text("x\n", encoding="utf-8")
    result = await _run(tools["edit"], path="c.py", old_string="zzz", new_string="y")
    assert result.is_error and "未找到" in result.content


# ---- grep ----


async def test_grep_finds_with_include_and_ignore_case(tools, ws_root) -> None:
    (ws_root / "1" / "a.py").write_text("DEF hello():\n", encoding="utf-8")
    (ws_root / "1" / "sub").mkdir()
    (ws_root / "1" / "sub" / "b.md").write_text("hello world\n", encoding="utf-8")

    case_hit = await _run(tools["grep"], pattern="def hello", path="a.py", ignore_case=True)
    assert case_hit.is_error is False
    assert case_hit.content == "a.py:1: DEF hello():"

    filtered = await _run(tools["grep"], pattern="hello", include="*.md")
    assert "sub/b.md:1" in filtered.content and "a.py" not in filtered.content

    none = await _run(tools["grep"], pattern="不存在的词")
    assert none.is_error is False and none.content == "无匹配"


async def test_grep_bad_regex_is_error(tools) -> None:
    result = await _run(tools["grep"], pattern="([unclosed")
    assert result.is_error and "正则" in result.content


# ---- find / ls ----


async def test_find_by_glob(tools, ws_root) -> None:
    (ws_root / "1" / "src").mkdir()
    (ws_root / "1" / "src" / "m.py").write_text("x", encoding="utf-8")
    (ws_root / "1" / "notes.md").write_text("y", encoding="utf-8")
    result = await _run(tools["find"], pattern="**/*.py")
    assert result.content == "src/m.py"
    none = await _run(tools["find"], pattern="**/*.rs")
    assert none.content.startswith("无匹配")


async def test_ls_lists_dirs_first_with_sizes(tools, ws_root) -> None:
    (ws_root / "1" / "z.txt").write_text("12345", encoding="utf-8")
    (ws_root / "1" / "adir").mkdir()
    result = await _run(tools["ls"])
    lines = result.content.splitlines()
    assert lines[0] == "adir/" and lines[1].startswith("z.txt") and "5 B" in lines[1]


# ---- fetch ----


async def test_fetch_html_to_text_with_title() -> None:
    page = "<html><head><title>示例页</title><style>b{}</style></head><body><p>第一段</p><br><script>x()</script><p>第二段</p></body></html>"
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text=page, headers={"content-type": "text/html"})
    )
    result = await FetchTool(transport).execute("t1", {"url": "https://example.com/x"}, None, None)
    assert result.is_error is False
    assert result.content.startswith("# 示例页")
    assert "第一段" in result.content and "第二段" in result.content
    assert "x()" not in result.content and "<p>" not in result.content


async def test_fetch_rejects_non_http_and_http_error() -> None:
    tool = FetchTool()
    bad = await tool.execute("t1", {"url": "ftp://example.com"}, None, None)
    assert bad.is_error and "http" in bad.content
    transport = httpx.MockTransport(lambda request: httpx.Response(404))
    not_found = await FetchTool(transport).execute("t1", {"url": "https://example.com/none"}, None, None)
    assert not_found.is_error and "404" in not_found.content


def test_html_helpers() -> None:
    assert extract_title("<title>A &amp; B</title>") == "A & B"
    assert html_to_text("<div>一<br>二</div>") == "一\n二"
