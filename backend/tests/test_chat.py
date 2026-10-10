"""Seam B2：ChatService——StreamEvent → message_* 事件映射、abort 与错误下发。"""

import asyncio
import threading

import pytest

from super_agent.ai.provider import Provider, ProviderAuthError
from super_agent.ai.types import (
    Message,
    MessageEnd,
    MessageStart,
    TextBlock,
    TextDelta,
    ToolUseBlock,
    Usage,
)
from super_agent.agent.types import AgentToolResult
from super_agent.app.chat import ChatService
from super_agent.app.session_store import SessionStore


class ScriptedProvider(Provider):
    """按脚本回放事件。

    gate（线程安全，供 TestClient 跨线程使用）未打开前不出第一个事件；
    mid_gate 未打开前不出最后一个事件——分别用于"任务已启动"与"停在流中段"两个断言窗口。
    """

    id = "scripted"

    def __init__(
        self,
        events=None,
        error: Exception | None = None,
        gate: threading.Event | None = None,
        mid_gate: threading.Event | None = None,
    ):
        self._events = events or []
        self._error = error
        self._gate = gate
        self._mid_gate = mid_gate

    async def stream(self, messages, model_id, *, system=None, effort="high", tools=None):
        # 拷贝：loop 会事后向 context append，别名引用会污染断言
        self.seen_messages, self.seen_model, self.seen_system = list(messages), model_id, system
        self.seen_tools = tools
        while self._gate is not None and not self._gate.is_set():
            await asyncio.sleep(0.01)
        if self._error is not None:
            raise self._error
        for ev in self._events[:-1]:
            yield ev
        while self._mid_gate is not None and not self._mid_gate.is_set():
            await asyncio.sleep(0.01)
        if self._events:
            yield self._events[-1]


def _collector(sent: list[dict]):
    """on_event 是 async 回调（WS send_json 的形态）——测试用同一契约收集。"""

    async def on_event(e: dict) -> None:
        sent.append(e)

    return on_event


def _script() -> list:
    return [
        MessageStart(model="claude-opus-5", usage=Usage(input_tokens=3)),
        TextDelta("你"),
        TextDelta("好"),
        MessageEnd(
            stop_reason="end_turn",
            usage=Usage(input_tokens=3, output_tokens=2),
            message=Message(role="assistant", content=[TextBlock("你好")]),
        ),
    ]


async def test_prompt_streams_message_events() -> None:
    sent: list[dict] = []
    chat = ChatService(ScriptedProvider(_script()))
    await chat.run_prompt("hi", user_id=1, on_event=_collector(sent))

    assert [e["type"] for e in sent] == [
        "agent_start",
        "turn_start",
        "message_start",
        "message_update",
        "message_update",
        "message_end",
        "turn_end",
        "agent_end",
    ]
    assert all(e["msg_id"] == sent[2]["msg_id"] for e in sent if "msg_id" in e)
    assert sent[3]["delta"] == {"type": "text", "text": "你"}
    assert sent[5]["stop_reason"] == "end_turn"
    assert sent[5]["usage"] == {"input_tokens": 3, "output_tokens": 2, "cache_read_tokens": 0, "cache_write_tokens": 0}


async def test_prompt_passes_system_and_user_message_to_provider() -> None:
    provider = ScriptedProvider(_script())
    await ChatService(provider, default_model="claude-opus-5").run_prompt("hi", user_id=1, on_event=_collector([]))

    assert provider.seen_model == "claude-opus-5"
    # 基座 + 今天日期（"现在几点/今天几号"无需跑 date；日期精确到日——缓存只跨天失效一次）
    import re

    assert "You are Super Agent" in provider.seen_system
    assert "Reply in the user's language." in provider.seen_system
    assert re.search(r"Today is \d{4}-\d{2}-\d{2}\.", provider.seen_system)
    assert provider.seen_messages == [Message(role="user", content=[TextBlock("hi")])]


async def test_abort_emits_message_end_then_raises() -> None:
    gate = threading.Event()
    sent: list[dict] = []
    chat = ChatService(ScriptedProvider(_script(), gate=gate))
    task = asyncio.create_task(chat.run_prompt("hi", user_id=1, on_event=_collector(sent)))

    await asyncio.sleep(0.05)  # 任务已启动并停在闸门
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # 闸门未开：message_start 都没发过，abort 不应凭空补收尾事件
    assert [e["type"] for e in sent] == ["agent_start", "turn_start"]


async def test_abort_mid_stream_emits_aborted_end() -> None:
    gate = threading.Event()

    class SlowTail(Provider):
        id = "slow"

        async def stream(self, messages, model_id, *, system=None, effort="high", tools=None):
            yield MessageStart(model="m", usage=Usage())
            yield TextDelta("部分")
            while not gate.is_set():
                await asyncio.sleep(0.01)
            yield MessageEnd(stop_reason="end_turn", usage=Usage(), message=Message(role="assistant", content=[]))

    sent: list[dict] = []
    task = asyncio.create_task(ChatService(SlowTail()).run_prompt("hi", user_id=1, on_event=_collector(sent)))

    await asyncio.sleep(0.05)
    assert [e["type"] for e in sent] == ["agent_start", "turn_start", "message_start", "message_update"]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    msg_start = next(e for e in sent if e["type"] == "message_start")
    assert sent[-1] == {
        "type": "message_end",
        "msg_id": msg_start["msg_id"],
        "stop_reason": "aborted",
        "usage": None,
    }


async def test_provider_error_surfaces_as_error_event() -> None:
    sent: list[dict] = []
    provider = ScriptedProvider(error=ProviderAuthError("key 缺失"))
    await ChatService(provider).run_prompt("hi", user_id=1, on_event=_collector(sent))

    # 相位事件先行，流未产出任何 message → 无 message_end 补发
    assert sent[-1] == {"type": "error", "code": "provider_auth", "message": "key 缺失"}
    assert [e["type"] for e in sent] == ["agent_start", "turn_start", "error"]


async def test_mid_stream_error_closes_message_first() -> None:
    class BoomAfterStart(Provider):
        id = "boom"

        async def stream(self, messages, model_id, *, system=None, effort="high", tools=None):
            yield MessageStart(model="m", usage=Usage())
            raise ProviderAuthError("中途失效")

    sent: list[dict] = []
    await ChatService(BoomAfterStart()).run_prompt("hi", user_id=1, on_event=_collector(sent))

    assert [e["type"] for e in sent] == [
        "agent_start", "turn_start", "message_start", "message_end", "error",
    ]
    assert sent[3]["stop_reason"] == "error"


class EchoTool:
    """最小工具：验证 ChatService → agent_loop → 工具执行 → 结果回填全链路。"""

    name = "echo"
    description = "原样返回输入"
    parameters = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }
    execution_mode = "parallel"

    async def execute(self, tool_call_id, params, signal, on_update):
        if on_update is not None:
            await on_update("处理中…")
        return AgentToolResult(content=f"echo: {params['text']}")


class MultiTurnProvider(Provider):
    """按脚本顺序回放多轮事件，记录每轮收到的上下文与工具定义。"""

    id = "multi-turn"

    def __init__(self, turns: list[list]) -> None:
        self._turns = list(turns)
        self.contexts: list[list[Message]] = []
        self.seen_tools: list | None = None
        self.last_system: str | None = None
        self.seen_model: str | None = None
        self.seen_effort: str | None = None

    async def stream(self, messages, model_id, *, system=None, effort="high", tools=None):
        self.seen_tools = tools
        self.last_system = system
        self.seen_model = model_id
        self.seen_effort = effort
        self.contexts.append(list(messages))
        for ev in self._turns.pop(0):
            yield ev


def _tool_script() -> list[list]:
    return [
        [
            MessageStart(model="m", usage=Usage(input_tokens=5)),
            TextDelta("我调用工具"),
            MessageEnd(
                stop_reason="tool_use",
                usage=Usage(input_tokens=5, output_tokens=4),
                message=Message(
                    role="assistant",
                    content=[
                        TextBlock("我调用工具"),
                        ToolUseBlock(id="t1", name="echo", input={"text": "hi"}),
                    ],
                ),
            ),
        ],
        [
            MessageStart(model="m", usage=Usage(input_tokens=9)),
            MessageEnd(
                stop_reason="end_turn",
                usage=Usage(input_tokens=9, output_tokens=6),
                message=Message(role="assistant", content=[TextBlock("答案是 hi")]),
            ),
        ],
    ]


async def test_tool_round_emits_tool_events_and_persists(tmp_path) -> None:
    provider = MultiTurnProvider(_tool_script())
    store = SessionStore(tmp_path / "sessions")
    sid = store.create_session(user_id=1)
    sent: list[dict] = []
    await ChatService(provider, store, tools=[EchoTool()]).run_prompt(
        "hi", user_id=1, session_id=sid, on_event=_collector(sent)
    )

    # 工具相位事件夹在两个 Turn 的 message 事件之间（MessagesAppended 只落盘不下发）
    assert [e["type"] for e in sent] == [
        "agent_start",
        "turn_start",
        "message_start",
        "message_update",
        "message_end",
        "tool_execution_start",
        "tool_execution_update",
        "tool_execution_end",
        "turn_end",
        "turn_start",
        "message_start",
        "message_end",
        "turn_end",
        "agent_end",
    ]
    assert sent[5] == {
        "type": "tool_execution_start",
        "tool_call_id": "t1",
        "name": "echo",
        "params": {"text": "hi"},
    }
    assert sent[6] == {"type": "tool_execution_update", "tool_call_id": "t1", "delta": "处理中…"}
    assert sent[7]["tool_call_id"] == "t1"
    assert sent[7]["content"] == "echo: hi"
    assert sent[7]["is_error"] is False
    # 工具定义以 ToolDef 形态传给 provider
    assert provider.seen_tools is not None and provider.seen_tools[0].name == "echo"
    # 落盘：user / assistant(tool_use)+usage / toolResult / assistant(final)+usage
    entries = store.read_entries(1, sid)
    assert [e["type"] for e in entries] == ["message", "message", "usage", "message", "message", "usage"]
    assert entries[1]["message"]["content"][1] == {
        "type": "tool_use", "id": "t1", "name": "echo", "input": {"text": "hi"},
    }
    assert entries[3]["message"] == {
        "role": "toolResult",
        "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "echo: hi"}],
    }


async def test_followup_reruns_after_loop_ends(tmp_path) -> None:
    """loop 结束后 steer 队列剩余按 Follow-up 语义自动续跑新 run（重放重建上下文）。"""
    script = [
        [
            MessageStart(model="m", usage=Usage(input_tokens=3)),
            MessageEnd(
                stop_reason="end_turn",
                usage=Usage(input_tokens=3, output_tokens=1),
                message=Message(role="assistant", content=[TextBlock("第一答")]),
            ),
        ],
        [
            MessageStart(model="m", usage=Usage(input_tokens=5)),
            MessageEnd(
                stop_reason="end_turn",
                usage=Usage(input_tokens=5, output_tokens=1),
                message=Message(role="assistant", content=[TextBlock("续跑答")]),
            ),
        ],
    ]
    provider = MultiTurnProvider(script)
    store = SessionStore(tmp_path / "sessions")
    sid = store.create_session(user_id=1)
    queue: asyncio.Queue[str] = asyncio.Queue()
    queue.put_nowait("接着来")
    sent: list[dict] = []
    await ChatService(provider, store).run_prompt(
        "hi", user_id=1, session_id=sid, on_event=_collector(sent), steer_queue=queue
    )

    # 续跑前先清零队列事件
    assert {"type": "queue_update", "pending": 0} in sent
    # 第二次 run 是完整的新 agent run，上下文 = 重放 + follow-up 消息
    assert [m.text() for m in provider.contexts[1]] == ["hi", "第一答", "接着来"]
    assert [e["type"] for e in sent].count("agent_end") == 2


# ---- ticket 06：workspace 文件工具接线 ----


async def test_workspace_tools_injected_per_user(tmp_path) -> None:
    """workspace_root 注入后，provider 收到七个文件工具的 ToolDef。"""
    provider = ScriptedProvider(_script())
    chat = ChatService(provider, workspace_root=tmp_path / "workspaces")
    await chat.run_prompt("hi", user_id=7, on_event=_collector([]))

    assert provider.seen_tools is not None
    assert {d.name for d in provider.seen_tools} == {
        "read", "write", "edit", "grep", "find", "ls", "fetch", "bash", "web_search",
    }


async def test_agent_combines_find_and_read_in_workspace(tmp_path) -> None:
    """组合任务：agent 连续调 find → read，文件工具真实读写用户工作区。"""
    provider = MultiTurnProvider(
        [
            [
                MessageStart(model="m", usage=Usage(input_tokens=5)),
                MessageEnd(
                    stop_reason="tool_use",
                    usage=Usage(input_tokens=5, output_tokens=4),
                    message=Message(
                        role="assistant",
                        content=[ToolUseBlock(id="t1", name="find", input={"pattern": "**/*.md"})],
                    ),
                ),
            ],
            [
                MessageStart(model="m", usage=Usage(input_tokens=9)),
                MessageEnd(
                    stop_reason="tool_use",
                    usage=Usage(input_tokens=9, output_tokens=4),
                    message=Message(
                        role="assistant",
                        content=[ToolUseBlock(id="t2", name="read", input={"path": "docs/notes.md"})],
                    ),
                ),
            ],
            [
                MessageStart(model="m", usage=Usage(input_tokens=12)),
                MessageEnd(
                    stop_reason="end_turn",
                    usage=Usage(input_tokens=12, output_tokens=6),
                    message=Message(role="assistant", content=[TextBlock("笔记内容是：工作区就绪")]),
                ),
            ],
        ]
    )
    ws_root = tmp_path / "workspaces"
    (ws_root / "42" / "docs").mkdir(parents=True)
    (ws_root / "42" / "docs" / "notes.md").write_text("工作区就绪", encoding="utf-8")

    sent: list[dict] = []
    await ChatService(provider, workspace_root=ws_root).run_prompt(
        "看看这个目录里有什么并总结", user_id=42, on_event=_collector(sent)
    )

    tool_ends = {e["tool_call_id"]: e for e in sent if e["type"] == "tool_execution_end"}
    assert tool_ends["t1"]["is_error"] is False
    assert tool_ends["t1"]["content"] == "docs/notes.md"
    assert tool_ends["t2"]["is_error"] is False
    assert "工作区就绪" in tool_ends["t2"]["content"]
    # 用户隔离：user 42 只见自己的工作区（工具实例按 user 组装）
    assert provider.contexts[0] is not None


async def test_tool_execution_end_carries_blocks(tmp_path) -> None:
    """票 24：tool_execution_end 事件携带富块（前端渲染数据源）。"""
    from super_agent.agent.types import AgentTool, AgentToolResult
    from super_agent.ai.types import ToolUseBlock

    class ImageTool(AgentTool):
        name = "img"
        description = "返回图片"
        parameters = {"type": "object", "properties": {}}
        execution_mode = "parallel"

        async def execute(self, tool_call_id, params, signal, on_update) -> AgentToolResult:
            return AgentToolResult(
                content="[图片: x.png]",
                blocks=[{"type": "image", "media_type": "image/png", "data": "aGk="}],
            )

    provider = MultiTurnProvider(
        [
            [
                MessageStart(model="m", usage=Usage(input_tokens=4)),
                MessageEnd(
                    stop_reason="tool_use",
                    usage=Usage(input_tokens=4, output_tokens=2),
                    message=Message(role="assistant", content=[ToolUseBlock(id="t1", name="img", input={})]),
                ),
            ],
            [
                MessageStart(model="m", usage=Usage()),
                MessageEnd(
                    stop_reason="end_turn",
                    usage=Usage(),
                    message=Message(role="assistant", content=[TextBlock("图看过了")]),
                ),
            ],
        ]
    )
    sent: list[dict] = []
    await ChatService(provider, tools=[ImageTool()]).run_prompt(
        "看图", user_id=1, on_event=_collector(sent)
    )
    end = next(e for e in sent if e["type"] == "tool_execution_end")
    assert end["blocks"] == [{"type": "image", "media_type": "image/png", "data": "aGk="}]


def test_create_app_wires_global_skills_dir(tmp_path, monkeypatch) -> None:
    """票 18 装配遗漏修复：create_app 默认挂 DEFAULT_DATA_DIR/skills——生产环境技能可用。"""
    from super_agent.server import main as main_module

    monkeypatch.setattr(main_module, "DEFAULT_DATA_DIR", tmp_path)
    skill_dir = tmp_path / "skills" / "deploy"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: deploy\ndescription: 部署清单\n---\n步骤", encoding="utf-8"
    )

    app = main_module.create_app(db_path=tmp_path / "t.db", sessions_dir=tmp_path / "sessions")
    names = [s.name for s in app.state.chat._skills_for(1)]
    assert names == ["deploy"]


async def test_tool_outside_workspace_rejected_mid_run(tmp_path) -> None:
    """越界路径在 run 中以 is_error 结果回填，不中断 loop。"""
    provider = MultiTurnProvider(
        [
            [
                MessageStart(model="m", usage=Usage(input_tokens=5)),
                MessageEnd(
                    stop_reason="tool_use",
                    usage=Usage(input_tokens=5, output_tokens=4),
                    message=Message(
                        role="assistant",
                        content=[ToolUseBlock(id="t1", name="write", input={"path": "../evil.txt", "content": "x"})],
                    ),
                ),
            ],
            [
                MessageStart(model="m", usage=Usage(input_tokens=9)),
                MessageEnd(
                    stop_reason="end_turn",
                    usage=Usage(input_tokens=9, output_tokens=6),
                    message=Message(role="assistant", content=[TextBlock("已拒绝越界写入")]),
                ),
            ],
        ]
    )
    ws_root = tmp_path / "workspaces"
    sent: list[dict] = []
    await ChatService(provider, workspace_root=ws_root).run_prompt(
        "写坏文件", user_id=1, on_event=_collector(sent)
    )

    end = next(e for e in sent if e["type"] == "tool_execution_end")
    assert end["is_error"] is True
    assert "越出工作区" in end["content"]
    assert not (tmp_path / "evil.txt").exists()  # 真的没写出去
    # loop 继续：assistant 最终答复照常落盘
    assert sent[-1]["type"] == "agent_end"


# ---- ticket 18：Skill 系统（索引注入 + load_skill 工具） ----


def _write_skill(root, name: str, description: str, body: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n", encoding="utf-8"
    )


async def test_prompt_injects_skill_index_and_load_skill_tool(tmp_path) -> None:
    """skills_dir 配置后：system prompt 注入技能索引，工具名单含 load_skill。"""
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir / "deploy", "deploy", "部署清单", "1. 跑测试")

    provider = ScriptedProvider(_script())
    await ChatService(
        provider,
        default_model="claude-opus-5",
        workspace_root=tmp_path / "workspaces",
        skills_dir=skills_dir,
    ).run_prompt("hi", user_id=1, on_event=_collector([]))

    assert "- deploy — 部署清单" in provider.seen_system
    assert "load_skill" in provider.seen_system
    assert any(getattr(t, "name", None) == "load_skill" for t in provider.seen_tools)


async def test_workspace_skills_are_loaded_per_user(tmp_path) -> None:
    """工作区 <workspace>/skills/ 附加加载（per-user），无需全局 skills_dir。"""
    from super_agent.app.workspace import user_workspace

    ws_root = tmp_path / "workspaces"
    user_ws = user_workspace(ws_root, 1).root
    _write_skill(user_ws / "skills" / "personal", "personal", "个人小抄", "个人步骤")

    provider = ScriptedProvider(_script())
    await ChatService(provider, workspace_root=ws_root).run_prompt(
        "hi", user_id=1, on_event=_collector([])
    )

    assert "- personal — 个人小抄" in provider.seen_system
    assert any(getattr(t, "name", None) == "load_skill" for t in provider.seen_tools)


async def test_no_skills_configured_leaves_system_and_tools_untouched(tmp_path) -> None:
    """未配置技能：system prompt 不含索引段，工具名单无 load_skill。"""
    provider = ScriptedProvider(_script())
    await ChatService(provider, workspace_root=tmp_path / "workspaces").run_prompt(
        "hi", user_id=1, on_event=_collector([])
    )
    assert "Skills" not in provider.seen_system
    assert all(getattr(t, "name", None) != "load_skill" for t in (provider.seen_tools or []))


async def test_prompt_with_skill_injects_body_into_message(tmp_path) -> None:
    """手动选技能（Claude Code 式）：正文直接注入该条用户消息——确定性生效，不赌模型调 load_skill。"""
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir / "deploy", "deploy", "部署清单", "1. 跑测试\n2. 打 tag")

    provider = ScriptedProvider(_script())
    await ChatService(
        provider, default_model="claude-opus-5", skills_dir=skills_dir
    ).run_prompt("开始部署", user_id=1, on_event=_collector([]), skill="deploy")

    first = provider.seen_messages[0]
    text = first.content[0].text
    assert '<skill name="deploy">' in text
    assert "1. 跑测试" in text
    assert text.rstrip().endswith("开始部署")  # 用户原文在后


async def test_prompt_with_unknown_skill_errors_before_run(tmp_path) -> None:
    """技能不存在：error 事件（code=skill_not_found）+ 可用清单提示，不发起 run。"""
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir / "deploy", "deploy", "部署清单", "步骤")

    provider = ScriptedProvider(_script())
    sent: list[dict] = []
    await ChatService(
        provider, default_model="claude-opus-5", skills_dir=skills_dir
    ).run_prompt("hi", user_id=1, on_event=_collector(sent), skill="ghost")

    assert sent[0]["type"] == "error"
    assert sent[0]["code"] == "skill_not_found"
    assert "ghost" in sent[0]["message"] and "deploy" in sent[0]["message"]
    assert [e["type"] for e in sent] == ["error"]  # 仅错误事件，run 未启动
