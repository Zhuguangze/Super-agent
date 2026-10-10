"""ticket 11：长期记忆——MemoryStore 四操作、索引同步、系统提示注入与跨会话复述。"""

from pathlib import Path

import pytest

from super_agent.ai.types import (
    Message,
    MessageEnd,
    MessageStart,
    TextBlock,
    ToolUseBlock,
    Usage,
)
from super_agent.app.chat import ChatService
from super_agent.app.memory import MemoryStore, MemoryError, to_slug
from super_agent.app.session_store import SessionStore
from super_agent.app.tools.memory import MemoryTool
from test_chat import MultiTurnProvider, _collector


@pytest.fixture()
def store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path / "memory" / "1")


def _make(store: MemoryStore, name: str = "prefers-python", description: str = "用户偏好 Python"):
    return store.create(name=name, description=description, type="user", content="用户明确偏好 Python 作为主力语言。")


# ---- slug 与隔离 ----


def test_to_slug_cleanups() -> None:
    assert to_slug("User Prefers Python") == "user-prefers-python"
    assert to_slug("用户/喜欢:Python!.md") == "用户-喜欢-python"
    assert to_slug("  ") == "memory"


def test_memory_isolated_per_user(tmp_path: Path) -> None:
    user1 = MemoryStore(tmp_path / "memory" / "1")
    user2 = MemoryStore(tmp_path / "memory" / "2")
    user1.create(name="secret", description="user1 的秘密", type="user", content="只有 user1 能看")

    assert "secret" in user1.index_text()
    assert "secret" not in user2.index_text()  # user2 索引为空
    with pytest.raises(MemoryError):
        user2.read_fact("secret")


def test_cannot_touch_index_file_itself(store: MemoryStore) -> None:
    with pytest.raises(MemoryError):
        store.read_fact("MEMORY")


# ---- 四操作 ----


def test_create_writes_frontmatter_and_index(store: MemoryStore) -> None:
    slug = _make(store)
    assert slug == "prefers-python"

    document = store.read_fact(slug)
    assert document.startswith("---\n")
    assert "name: prefers-python" in document
    assert "description: 用户偏好 Python" in document
    assert "type: user" in document
    assert "用户明确偏好 Python" in document

    index = store.index_text()
    assert "- [prefers-python](prefers-python.md) — 用户偏好 Python" in index


def test_create_rejects_duplicate_and_bad_type(store: MemoryStore) -> None:
    _make(store)
    with pytest.raises(MemoryError, match="已存在"):
        _make(store)
    with pytest.raises(MemoryError, match="type"):
        store.create(name="x", description="d", type="unknown", content="c")
    with pytest.raises(MemoryError, match="description"):
        store.create(name="y", description="  ", type="user", content="c")


def test_view_returns_index_or_fact(store: MemoryStore) -> None:
    assert store.index_text() == ""  # 无索引文件 → 空（工具层会提示为空）
    _make(store)
    assert "prefers-python" in store.index_text()
    assert "frontmatter 之外" not in store.read_fact("prefers-python")  # 全文含正文
    with pytest.raises(MemoryError, match="不存在"):
        store.read_fact("ghost")


def test_str_replace_updates_fact_and_index_description(store: MemoryStore) -> None:
    _make(store, description="用户偏好 Python")
    store.str_replace(
        "prefers-python",
        "description: 用户偏好 Python",
        "description: 用户主力语言是 Python",
    )
    assert "用户主力语言是 Python" in store.read_fact("prefers-python")
    # 索引行同步刷新
    assert "用户主力语言是 Python" in store.index_text()
    assert "用户偏好 Python" not in store.index_text()

    with pytest.raises(MemoryError, match="未找到"):
        store.str_replace("prefers-python", "不存在的文本", "x")
    with pytest.raises(MemoryError, match="唯一"):
        store.str_replace("prefers-python", "Python", "PY")  # 出现多次


def test_delete_removes_fact_and_index_line(store: MemoryStore) -> None:
    _make(store)
    store.delete("prefers-python")
    with pytest.raises(MemoryError, match="不存在"):
        store.read_fact("prefers-python")
    assert "prefers-python" not in store.index_text()
    with pytest.raises(MemoryError, match="不存在"):
        store.delete("prefers-python")


def test_index_truncation(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory" / "1")
    for i in range(50):
        store.create(name=f"fact-{i}", description="很长的描述" * 20, type="user", content="x")
    index = store.index_text()
    assert len(index) < 2048 + 60
    assert "已截断" in index


def test_agents_file_reading(tmp_path: Path) -> None:
    from super_agent.app.memory import read_agents_file

    ws_root = tmp_path / "ws" / "1"
    assert read_agents_file(ws_root) == ""
    ws_root.mkdir(parents=True)
    (ws_root / "AGENTS.md").write_text("本项目用 pnpm，禁止 npm。", encoding="utf-8")
    assert "pnpm" in read_agents_file(ws_root)


# ---- memory 工具 ----


async def _run_tool(store: MemoryStore, **params):
    return await MemoryTool(store).execute("t1", params, None, None)


async def test_tool_operations_roundtrip(store: MemoryStore) -> None:
    created = await _run_tool(
        store,
        operation="create",
        name="deploy-doc",
        description="部署文档位置",
        type="reference",
        content="部署手册在 docs/deploy.md",
    )
    assert created.is_error is False and "deploy-doc" in created.content

    viewed = await _run_tool(store, operation="view", name="deploy-doc")
    assert "docs/deploy.md" in viewed.content
    index = await _run_tool(store, operation="view")
    assert "deploy-doc" in index.content

    replaced = await _run_tool(
        store, operation="str_replace", name="deploy-doc",
        old_string="docs/deploy.md", new_string="docs/ops/deploy.md",
    )
    assert replaced.is_error is False
    deleted = await _run_tool(store, operation="delete", name="deploy-doc")
    assert deleted.is_error is False
    assert "deploy-doc" not in store.index_text()


async def test_tool_unknown_operation_and_errors(store: MemoryStore) -> None:
    bad = await _run_tool(store, operation="rename", name="x")
    assert bad.is_error and "operation" in bad.content
    ghost = await _run_tool(store, operation="delete", name="ghost")
    assert ghost.is_error and "不存在" in ghost.content


# ---- ChatService：系统提示注入与跨会话复述 ----


async def test_system_prompt_injects_index_and_agents(tmp_path: Path) -> None:
    provider = MultiTurnProvider(
        [
            [
                MessageStart(model="m", usage=Usage()),
                MessageEnd(
                    stop_reason="end_turn", usage=Usage(),
                    message=Message(role="assistant", content=[TextBlock("好")]),
                ),
            ],
            [
                MessageStart(model="m", usage=Usage()),
                MessageEnd(
                    stop_reason="end_turn", usage=Usage(),
                    message=Message(role="assistant", content=[TextBlock("好")]),
                ),
            ],
        ]
    )
    memory_root = tmp_path / "memory"
    ws_root = tmp_path / "ws"
    ws_root.mkdir(parents=True)
    MemoryStore(memory_root / "7").create(
        name="likes-rust", description="用户喜欢 Rust", type="user", content="正文"
    )
    (ws_root / "7").mkdir()
    (ws_root / "7" / "AGENTS.md").write_text("回复保持简短。", encoding="utf-8")

    chat = ChatService(provider, workspace_root=ws_root, memory_root=memory_root)
    await chat.run_prompt("hi", user_id=7, on_event=_collector([]))

    system_text = provider.last_system or ""
    assert "likes-rust" in system_text and "用户喜欢 Rust" in system_text
    assert "回复保持简短" in system_text
    # 他人无记忆则无注入
    chat2 = ChatService(provider, workspace_root=ws_root, memory_root=memory_root)
    await chat2.run_prompt("hi", user_id=8, on_event=_collector([]))
    assert "likes-rust" not in (provider.last_system or "")


async def test_memory_survives_across_sessions(tmp_path: Path) -> None:
    """跨会话复述验证：会话 A 记住 → 全新会话 B 的 system 含索引、agent 可 view 到全文。"""
    memory_root = tmp_path / "memory"
    store = SessionStore(tmp_path / "sessions")

    create_script = [
        [
            MessageStart(model="m", usage=Usage(input_tokens=5)),
            MessageEnd(
                stop_reason="tool_use",
                usage=Usage(input_tokens=5, output_tokens=4),
                message=Message(
                    role="assistant",
                    content=[
                        ToolUseBlock(
                            id="t1",
                            name="memory",
                            input={
                                "operation": "create",
                                "name": "user-language",
                                "description": "用户偏好中文回复",
                                "type": "user",
                                "content": "所有回复使用中文。",
                            },
                        )
                    ],
                ),
            ),
        ],
        [
            MessageStart(model="m", usage=Usage(input_tokens=9)),
            MessageEnd(
                stop_reason="end_turn",
                usage=Usage(input_tokens=9, output_tokens=2),
                message=Message(role="assistant", content=[TextBlock("已记住")]),
            ),
        ],
    ]
    chat = ChatService(MultiTurnProvider(create_script), store, memory_root=memory_root)
    sid_a = store.create_session(user_id=1)
    sent: list[dict] = []
    await chat.run_prompt("记住我喜欢中文", user_id=1, session_id=sid_a, on_event=_collector(sent))

    # 全新会话 B：system 里带索引；agent 调 memory view 拿到全文
    view_script = [
        [
            MessageStart(model="m", usage=Usage(input_tokens=5)),
            MessageEnd(
                stop_reason="tool_use",
                usage=Usage(input_tokens=5, output_tokens=3),
                message=Message(
                    role="assistant",
                    content=[ToolUseBlock(id="t1", name="memory", input={"operation": "view", "name": "user-language"})],
                ),
            ),
        ],
        [
            MessageStart(model="m", usage=Usage(input_tokens=9)),
            MessageEnd(
                stop_reason="end_turn",
                usage=Usage(input_tokens=9, output_tokens=2),
                message=Message(role="assistant", content=[TextBlock("您偏好中文回复")]),
            ),
        ],
    ]
    provider_b = MultiTurnProvider(view_script)
    chat_b = ChatService(provider_b, store, memory_root=memory_root)
    sid_b = store.create_session(user_id=1)  # 全新空会话
    sent_b: list[dict] = []
    await chat_b.run_prompt("我偏好什么语言？", user_id=1, session_id=sid_b, on_event=_collector(sent_b))

    # 系统提示含索引条目（MEMORY.md 常驻）
    assert "user-language" in (provider_b.last_system or "")
    # agent 经 view 拿到全文 → 工具结果回填进上下文（复述依据）
    view_end = next(e for e in sent_b if e["type"] == "tool_execution_end")
    assert "所有回复使用中文" in view_end["content"]
    # 新会话上下文极小（无历史串台），记忆完全来自系统提示 + 工具
    assert [m.role for m in provider_b.contexts[1]] == ["user", "assistant", "toolResult"]
    assert provider_b.contexts[1][0].text() == "我偏好什么语言？"
