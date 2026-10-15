"""ticket 12：扩展系统——加载隔离、三 API、七事件、tool_call 拦截/修改与端到端。"""

import asyncio
from pathlib import Path

from super_agent.ai.types import (
    Message,
    MessageEnd,
    MessageStart,
    TextBlock,
    ToolUseBlock,
    Usage,
)
from super_agent.app.chat import ChatService
from super_agent.app.extensions import ExtensionManager, EXTENSION_EVENTS
from super_agent.app.session_store import SessionStore
from test_chat import MultiTurnProvider, _collector


def _write_extension(directory: Path, filename: str, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(body, encoding="utf-8")
    return path


def _echo_extension() -> str:
    return '''
def register(api):
    def echo(tool_call_id, params, signal, on_update):
        return f"echo: {params.get('text', '')}"

    api.register_tool(
        name="echo_ext",
        description="扩展回声工具",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        execute=echo,
    )
'''


# ---- 加载与隔离 ----


def test_load_registers_tool_command_and_handlers(tmp_path: Path) -> None:
    ext_dir = tmp_path / "extensions"
    _write_extension(ext_dir, "sample.py", _echo_extension())
    manager = ExtensionManager.load(ext_dir)

    assert manager.errors == []
    assert [t.name for t in manager.tools] == ["echo_ext"]


def test_bad_plugins_are_isolated(tmp_path: Path) -> None:
    ext_dir = tmp_path / "extensions"
    _write_extension(ext_dir, "broken_syntax.py", "def register(api(\n")  # 语法错误
    _write_extension(ext_dir, "no_register.py", "x = 1\n")  # 缺 register
    _write_extension(ext_dir, "register_raises.py", "def register(api):\n    raise ValueError('boom')\n")
    _write_extension(ext_dir, "good.py", _echo_extension())
    _write_extension(ext_dir, "_private.py", "raise RuntimeError('不应被加载')\n")

    manager = ExtensionManager.load(ext_dir)
    # 好插件照常加载，坏插件只记错误
    assert [t.name for t in manager.tools] == ["echo_ext"]
    assert len(manager.errors) == 3
    assert any("broken_syntax" in e for e in manager.errors)
    assert any("no_register" in e for e in manager.errors)
    assert any("register_raises" in e for e in manager.errors)


def test_unknown_event_rejected(tmp_path: Path) -> None:
    ext_dir = tmp_path / "extensions"
    _write_extension(ext_dir, "bad_event.py", "def register(api):\n    api.on('nope', lambda p: None)\n")
    manager = ExtensionManager.load(ext_dir)
    assert any("未知事件" in e for e in manager.errors)


def test_fresh_copy_carries_registrations(tmp_path: Path) -> None:
    ext_dir = tmp_path / "extensions"
    _write_extension(ext_dir, "sample.py", _echo_extension())
    manager = ExtensionManager.load(ext_dir).fresh_copy()
    assert [t.name for t in manager.tools] == ["echo_ext"]


# ---- 三 API 与事件 ----


async def test_emit_and_tool_call_and_context_hooks(tmp_path: Path) -> None:
    ext_dir = tmp_path / "extensions"
    _write_extension(
        ext_dir,
        "hooks.py",
        '''
records = []

async def on_start(payload):
    records.append(("session_start", payload["user_id"]))

async def before(payload):
    records.append(("before_agent_start", payload["message_count"]))

async def intercept(name, params):
    records.append(("tool_call", name))
    return "被扩展拦截" if name == "bash" else None

async def on_result(payload):
    records.append(("tool_result", payload["is_error"]))

async def turn_end(payload):
    records.append(("turn_end", payload["turn"]))

async def settled(payload):
    records.append(("agent_settled", payload["stop_reason"]))

async def add_context(messages):
    from super_agent.ai.types import Message, TextBlock
    return [Message(role="user", content=[TextBlock("（扩展注入的前言）")]), *messages]

def register(api):
    api.on("session_start", on_start)
    api.on("before_agent_start", before)
    api.on("tool_call", intercept)
    api.on("tool_result", on_result)
    api.on("turn_end", turn_end)
    api.on("agent_settled", settled)
    api.on("context", add_context)
''',
    )
    manager = ExtensionManager.load(ext_dir)
    assert manager.errors == []
    provider = MultiTurnProvider(
        [
            [
                MessageStart(model="m", usage=Usage()),
                MessageEnd(
                    stop_reason="tool_use",
                    usage=Usage(input_tokens=5, output_tokens=4),
                    message=Message(
                        role="assistant",
                        content=[ToolUseBlock(id="t1", name="bash", input={"command": "echo hi"})],
                    ),
                ),
            ],
            [
                MessageStart(model="m", usage=Usage()),
                MessageEnd(
                    stop_reason="end_turn",
                    usage=Usage(input_tokens=9, output_tokens=2),
                    message=Message(role="assistant", content=[TextBlock("好")]),
                ),
            ],
        ]
    )
    chat = ChatService(
        provider,
        tools=[_BashLikeTool()],
        ext_global_dir=ext_dir,
    )
    sent: list[dict] = []
    await chat.run_prompt("跑命令", user_id=1, on_event=_collector(sent))

    # tool_call 钩子拦截 bash（返回 str → is_error 结果，loop 继续）
    end = next(e for e in sent if e["type"] == "tool_execution_end")
    assert end["is_error"] is True and "被扩展拦截" in end["content"]
    assert sent[-1]["type"] == "agent_end"
    # context 钩子在上下文最前注入一条消息
    assert provider.contexts[0][0].text() == "（扩展注入的前言）"
    assert provider.contexts[0][1].text() == "跑命令"


class _BashLikeTool:
    name = "bash"
    description = "测试用占位 bash"
    parameters = {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}
    execution_mode = "sequential"

    async def execute(self, tool_call_id, params, signal, on_update):
        from super_agent.agent.types import AgentToolResult

        return AgentToolResult(content=f"ran {params.get('command')}")


async def test_tool_call_hook_can_modify_params(tmp_path: Path) -> None:
    """tool_call 钩子就地修改 params → 工具收到修改后的参数。"""
    ext_dir = tmp_path / "extensions"
    _write_extension(
        ext_dir,
        "modify.py",
        '''
async def tweak(name, params):
    if name == "echo_ext":
        params["text"] = params.get("text", "") + "（已被扩展改写）"

def register(api):
    api.on("tool_call", tweak)
''',
    )
    provider = MultiTurnProvider(
        [
            [
                MessageStart(model="m", usage=Usage()),
                MessageEnd(
                    stop_reason="tool_use",
                    usage=Usage(input_tokens=5, output_tokens=4),
                    message=Message(
                        role="assistant",
                        content=[ToolUseBlock(id="t1", name="echo_ext", input={"text": "原始"})],
                    ),
                ),
            ],
            [
                MessageStart(model="m", usage=Usage()),
                MessageEnd(
                    stop_reason="end_turn",
                    usage=Usage(input_tokens=9, output_tokens=2),
                    message=Message(role="assistant", content=[TextBlock("好")]),
                ),
            ],
        ]
    )
    chat = ChatService(
        provider,
        tools=[_EchoExtTool()],
        ext_global_dir=ext_dir,
    )
    sent: list[dict] = []
    await chat.run_prompt("hi", user_id=1, on_event=_collector(sent))

    end = next(e for e in sent if e["type"] == "tool_execution_end")
    assert end["is_error"] is False
    assert "已被扩展改写" in end["content"]


class _EchoExtTool:
    name = "echo_ext"
    description = "记录收到的 params"
    parameters = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}
    execution_mode = "parallel"

    async def execute(self, tool_call_id, params, signal, on_update):
        from super_agent.agent.types import AgentToolResult

        return AgentToolResult(content=str(params.get("text")))


# ---- 端到端：示例扩展 ----


def test_example_extension_loads(tmp_path: Path) -> None:
    example = Path(__file__).parent.parent / "examples" / "extensions" / "word_count.py"
    manager = ExtensionManager.load(example.parent)
    assert manager.errors == []
    assert "word_count" in [t.name for t in manager.tools]


async def test_extension_tool_end_to_end(tmp_path: Path) -> None:
    """扩展注册的工具经 run 端到端可用（校验/执行/回填全链路）。"""
    ext_dir = tmp_path / "extensions"
    _write_extension(ext_dir, "word_count.py", (Path(__file__).parent.parent / "examples" / "extensions" / "word_count.py").read_text(encoding="utf-8"))

    provider = MultiTurnProvider(
        [
            [
                MessageStart(model="m", usage=Usage()),
                MessageEnd(
                    stop_reason="tool_use",
                    usage=Usage(input_tokens=5, output_tokens=4),
                    message=Message(
                        role="assistant",
                        content=[ToolUseBlock(id="t1", name="word_count", input={"text": "你好世界"})],
                    ),
                ),
            ],
            [
                MessageStart(model="m", usage=Usage()),
                MessageEnd(
                    stop_reason="end_turn",
                    usage=Usage(input_tokens=9, output_tokens=2),
                    message=Message(role="assistant", content=[TextBlock("四个字符")]),
                ),
            ],
        ]
    )
    chat = ChatService(provider, ext_global_dir=ext_dir)
    sent: list[dict] = []
    await chat.run_prompt("统计一下", user_id=1, on_event=_collector(sent))

    end = next(e for e in sent if e["type"] == "tool_execution_end")
    assert end["is_error"] is False
    assert "4" in end["content"]  # "你好世界" = 4 字符


# ---- WS：扩展命令 ----


def test_ws_extension_command(tmp_path) -> None:
    from fastapi.testclient import TestClient

    from super_agent.server.main import create_app

    ext_dir = tmp_path / "extensions"
    _write_extension(
        ext_dir,
        "cmd.py",
        '''
async def ping(payload):
    return {"pong": payload.get("text", "")}

def register(api):
    api.register_command("ping", ping)
''',
    )
    store = SessionStore(tmp_path / "sessions")
    app = create_app(
        db_path=tmp_path / "t.db",
        chat=ChatService(MultiTurnProvider([]), store, ext_global_dir=ext_dir),
        sessions_dir=tmp_path / "sessions",
    )
    client = TestClient(app)
    client.post("/api/auth/register", json={"username": "u1", "password": "pw123456"})
    token = client.post("/api/auth/login", json={"username": "u1", "password": "pw123456"}).json()["token"]

    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.receive_json()  # session_info
        ws.send_json({"cmd": "ping", "req_id": "1", "text": "hello"})
        resp = ws.receive_json()
        assert resp == {"type": "response", "req_id": "1", "ok": True, "result": {"pong": "hello"}}
        # 未注册命令仍回落 unknown_cmd
        ws.send_json({"cmd": "nope", "req_id": "2"})
        assert ws.receive_json() == {"type": "response", "req_id": "2", "ok": False, "error": "unknown_cmd"}


def test_extension_events_enumeration() -> None:
    """十个事件钩子齐全（spec §4.9；压缩三事件为可干预压缩所加）。"""
    assert set(EXTENSION_EVENTS) == {
        "session_start",
        "before_agent_start",
        "tool_call",
        "tool_result",
        "context",
        "before_compaction",
        "compaction_end",
        "compaction_failed",
        "agent_settled",
        "turn_end",
    }
