"""app 层——对话编排：Agent Loop 的驱动、事件映射与落盘（ticket 05；后续并入 AgentSession）。

不依赖 server 层：事件经 on_event 回调下发，dict 形态与 spec §4.1 对齐。
- 每次 prompt 驱动一个完整 agent_loop（多 Turn、工具执行、Steer 消费）
- loop 结束后 steer 队列若有剩余，按 Follow-up 语义自动续跑新 run
- 持久化：user 消息先落盘；每个 Turn 的 assistant 消息（含工具调用块）与
  toolResult 回填消息分别落盘；abort 落半截消息
"""

import asyncio
import json
from datetime import datetime
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import uuid4

from super_agent.ai.provider import (
    DEFAULT_EFFORT,
    MODELS,
    Effort,
    Provider,
    ProviderError,
)
from super_agent.ai.types import (
    ImageBlock,
    Message,
    MessageEnd,
    MessageStart,
    TextBlock,
    TextDelta,
    ThinkingBlock,
    ThinkingDelta,
    Usage,
)
from super_agent.agent.hooks import Hooks
from super_agent.agent.loop import agent_loop
from super_agent.agent.types import (
    AgentEnd,
    AgentStart,
    AgentTool,
    MessagesAppended,
    ToolExecutionEnd,
    ToolExecutionStart,
    ToolExecutionUpdate,
    TurnEnd,
    TurnStart,
)
from super_agent.app.approval import ApprovalService, make_approval_hook
from super_agent.app.compaction import (
    AUTO_COMPACT_INPUT_TOKENS,
    COMPACT_KEEP_LAST,
    is_summary_message,
    summarize,
    transcribe,
)
from super_agent.app.extensions import ExtensionManager
from super_agent.app.mcp_client import McpRegistry, load_mcp_config
from super_agent.app.memory import MemoryStore, read_agents_file
from super_agent.app.semaphore import AdjustableSemaphore
from super_agent.app.session_store import SessionStore
from super_agent.app.skills import SkillTool, load_skills, skills_index_text
from super_agent.app.tools import make_file_tools
from super_agent.app.tools.memory import MemoryTool
from super_agent.app.workspace import user_workspace

SYSTEM_PROMPT = "You are Super Agent, a general-purpose task agent. Reply in the user's language."

EventHandler = Callable[[dict[str, Any]], Awaitable[None]]


class _RunState:
    """单次 run 内的映射状态（msg_id、累积的部分输出、收尾数据）。"""

    def __init__(self) -> None:
        self.msg_id: str | None = None
        self.started = False
        self.partial: list = []
        self.stop_reason: str | None = None
        self.usage_out: Usage | None = None
        self.final_message: Message | None = None
        self.ext: ExtensionManager | None = None


def _accumulate(partial: list, block) -> None:
    """流式增量并入累积内容：同类块续写，类型切换则新开块。"""
    if partial and type(partial[-1]) is type(block):
        partial[-1].text += block.text
    else:
        partial.append(block)


def _drain(queue: asyncio.Queue[str] | None) -> list[str]:
    if queue is None:
        return []
    items: list[str] = []
    while True:
        try:
            items.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            return items


class ChatService:
    """把一次 prompt 变成一条事件流（agent 相位 + message 流），并落盘到该用户的 Session。"""

    def __init__(
        self,
        provider: Provider,
        store: SessionStore | None = None,
        default_model: str | None = None,
        tools: Sequence[AgentTool] = (),
        workspace_root: Path | None = None,
        approval: ApprovalService | None = None,
        memory_root: Path | None = None,
        ext_global_dir: Path | None = None,
        mcp_config_path: Path | None = None,
        max_concurrency: int = 4,
        skills_dir: Path | None = None,
    ) -> None:
        self._provider = provider
        self._store = store
        self._default_model = default_model
        self._tools = list(tools)
        self._workspace_root = workspace_root
        self._approval = approval
        self._memory_root = memory_root
        # 全局扩展启动时加载一次；工作区扩展每 run 附加加载（ticket 12）
        self._global_ext = ExtensionManager.load(ext_global_dir) if ext_global_dir else ExtensionManager()
        for error in self._global_ext.errors:
            logging.warning("扩展加载问题: %s", error)
        # MCP 服务器懒启动（首次 run 时连接，ticket 13）；mcp.json 可选
        self._mcp_config_path = mcp_config_path
        self._mcp: McpRegistry | None = None
        self._mcp_lock = asyncio.Lock()
        if mcp_config_path is not None and not load_mcp_config(mcp_config_path):
            logging.info("mcp.json 缺失或无 servers，MCP 未启用")
        # Skill 全局目录（票 18）：data/skills/，管理员维护；工作区 skills/ 每 run 附加
        self._skills_dir = skills_dir
        # 全局并发 Agent Loop 上限（ticket 15）：超出自动排队；Settings 可热更
        self._loop_sem = AdjustableSemaphore(max(1, max_concurrency))
        # bash 默认超时（Settings 的 bash_timeout，秒）
        self._bash_timeout = 60.0
        # 分叉指针（ticket 09）：(user_id, session_id) → 下一次 append 的 parent entry
        # 内存态即可：分叉后新消息落在文件尾，重放沿"最后一条 message"回溯自然走新分支
        self._branch_points: dict[tuple[int, str], str] = {}
        # 会话级模型与 thinking level（ticket 14）：切换对下一 run 生效，model_change 落盘
        self._session_models: dict[tuple[int, str], str] = {}
        self._session_efforts: dict[tuple[int, str], Effort] = {}
        # 用户最近一次使用的模型（新会话继承它而非固定默认，Settings 视图需求）
        self._last_model: dict[int, str] = {}

    # ---- 会话管理（ticket 09 将扩展为完整列表/切换/分支） ----

    @property
    def approval(self) -> ApprovalService | None:
        """审批服务（WS 层 approve 命令需要；无审批配置时为 None）。"""
        return self._approval

    def ensure_session(self, user_id: int) -> str | None:
        """当前会话：最近更新的 Session，没有则新建；无 store 时返回 None。"""
        if self._store is None:
            return None
        return self._store.latest_session(user_id) or self._store.create_session(user_id)

    def new_session(self, user_id: int) -> str | None:
        """新开对话即新 Session。"""
        if self._store is None:
            return None
        return self._store.create_session(user_id)

    def get_session(self, user_id: int, session_id: str) -> bool:
        """会话是否存在且属于该用户（切换/引用校验用）。"""
        if self._store is None:
            return False
        return self._store.get_session(user_id, session_id) is not None

    # ---- 分支（ticket 09，ADR-0002 树结构） ----

    def set_branch_point(self, user_id: int, session_id: str | None, entry_id: str) -> bool:
        """把下一次 append 的 parent 设为 entry_id（从任意历史条目分叉）。

        entry_id 必须属于该会话；成功后对话即在新分支上继续。
        """
        if self._store is None or session_id is None:
            return False
        if not any(e["id"] == entry_id for e in self._store.read_entries(user_id, session_id)):
            return False
        self._branch_points[(user_id, session_id)] = entry_id
        return True

    def delete_session(self, user_id: int, session_id: str) -> bool:
        self._branch_points.pop((user_id, session_id), None)
        if self._store is None:
            return False
        return self._store.delete_session(user_id, session_id)

    # ---- 对话 ----

    def _skills_for(self, user_id: int) -> list:
        """本 run 可用的技能：全局 skills/ + 工作区 skills/（同名覆盖，per-run 热更）。"""
        if self._skills_dir is None and self._workspace_root is None:
            return []
        ws_skills = (
            user_workspace(self._workspace_root, user_id).root / "skills"
            if self._workspace_root is not None
            else None
        )
        return load_skills(self._skills_dir, ws_skills)

    def skills_list(self, user_id: int) -> list[dict]:
        """可用技能清单（Composer 手动选择菜单的数据源）。"""
        return [{"name": s.name, "description": s.description} for s in self._skills_for(user_id)]

    def resolve_skill(self, user_id: int, name: str) -> tuple[str | None, list[dict]]:
        """按名取技能正文；返回 (body, 可用清单)——未知名时清单用于报错提示。"""
        skills = self._skills_for(user_id)
        for s in skills:
            if s.name == name:
                return s.body, [{"name": x.name, "description": x.description} for x in skills]
        return None, [{"name": x.name, "description": x.description} for x in skills]

    def _tools_for(self, user_id: int, ext: ExtensionManager) -> list[AgentTool]:
        """该用户本次 run 的工具集：静态工具 + 内置工具 + MCP 工具 + 扩展工具。"""
        tools = list(self._tools)
        if self._workspace_root is not None:
            tools.extend(make_file_tools(user_workspace(self._workspace_root, user_id), bash_timeout=self._bash_timeout))
        if self._memory_root is not None:
            tools.append(MemoryTool(MemoryStore(self._memory_root / str(user_id))))
        if self._mcp is not None:
            tools.extend(self._mcp.tools)
        tools.extend(ext.tools)
        skills = self._skills_for(user_id)
        if skills:
            tools.append(SkillTool(skills))
        return tools

    def set_bash_timeout(self, seconds: float) -> None:
        """Settings 热更：bash 默认超时（下次 run 的工具实例生效）。"""
        self._bash_timeout = max(1.0, min(300.0, float(seconds)))

    @property
    def bash_timeout(self) -> float:
        return self._bash_timeout

    async def _ensure_mcp(self) -> None:
        """首次 run 时连接 mcp.json 里的服务器；失败隔离（MCP 可选能力）。"""
        if self._mcp_config_path is None:
            return
        async with self._mcp_lock:
            if self._mcp is None:
                registry = McpRegistry()
                await registry.start_from_config(self._mcp_config_path)
                for error in registry.errors:
                    logging.warning("MCP 启动问题: %s", error)
                self._mcp = registry

    async def mcp_tools(self) -> list[dict]:
        """已发现的 MCP 工具摘要（票 19）：Settings 白名单勾选的数据源。

        首次调用触发 MCP 懒启动（与 run 同机制）；未配置 mcp.json 返回 []。
        """
        await self._ensure_mcp()
        if self._mcp is None:
            return []
        return self._mcp.tool_summaries()

    async def mcp_status(self) -> dict:
        """MCP 面板状态（票 20）：mcp.json 现内容 + 每 server 运行态 + 已发现工具。"""
        await self._ensure_mcp()
        if self._mcp is None or self._mcp_config_path is None:
            return {"configured": False, "config": {"servers": {}}, "servers": [], "tools": []}
        rows = [
            {"name": name, "status": "failed", "error": self._mcp.failed.get(name, "未知错误")}
            for name in self._mcp.failed
        ]
        rows += [
            {"name": name, "status": "running", "error": ""}
            for name in self._mcp.connections
        ]
        return {
            "configured": True,
            "config": {"servers": load_mcp_config(self._mcp_config_path)},
            "servers": rows,
            "tools": self._mcp.tool_summaries(),
        }

    def mcp_save_config(self, servers: dict) -> None:
        """写回 mcp.json（票 20：格式校验由 REST 层完成后调用）。"""
        if self._mcp_config_path is None:
            return
        self._mcp_config_path.parent.mkdir(parents=True, exist_ok=True)
        self._mcp_config_path.write_text(
            json.dumps({"servers": servers}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    async def mcp_restart(self) -> list[str]:
        """重连全部 MCP 服务器（票 20：配置保存后热生效）。

        先起新 registry 再关旧连接——短暂重叠后旧连接关闭，in-flight 调用
        以 is_error 回填（连接已关闭），run 本身不中断。返回启动错误。
        """
        if self._mcp_config_path is None:
            return []
        old: McpRegistry | None
        async with self._mcp_lock:
            registry = McpRegistry()
            await registry.start_from_config(self._mcp_config_path)
            old = self._mcp
            self._mcp = registry
        for error in registry.errors:
            logging.warning("MCP 重启问题: %s", error)
        if old is not None:
            for connection in old.connections.values():
                try:
                    await connection.close()
                except Exception:
                    logging.warning("旧 MCP 连接关闭失败", exc_info=True)
        return registry.errors

    def _ext_manager_for(self, user_id: int) -> ExtensionManager:
        """每 run 的扩展作用域：全局注册内容 + 该用户工作区 extensions/ 附加加载。"""
        manager = self._global_ext.fresh_copy()
        if self._workspace_root is not None:
            workspace = user_workspace(self._workspace_root, user_id).root
            manager.load_into(workspace / "extensions")
        for error in manager.errors:
            logging.warning("扩展加载问题（user %s）: %s", user_id, error)
        return manager

    async def run_extension_command(self, name: str, payload: dict) -> dict | None:
        """WS 未知命令回落到全局扩展注册的命令；未注册返回 None。"""
        return await self._global_ext.run_command(name, payload)

    def _system_prompt(self, user_id: int, expert: str | None = None) -> str:
        """系统提示 = 基座 + 今天日期 + 该用户的记忆索引 + 工作区约定（均截断，ticket 11）。

        日期精确到日：模型不用为"现在几点/今天几号"跑 date；缓存只跨天失效一次。
        expert（专家模式）：以独立小节追加人格，随每轮消息由前端携带——
        同一会话内文本不变，不破坏 prompt 缓存。
        """
        parts = [SYSTEM_PROMPT, f"Today is {datetime.now():%Y-%m-%d}."]
        if self._memory_root is not None:
            memory = MemoryStore(self._memory_root / str(user_id))
            index = memory.index_text()
            if index:
                parts.append(
                    "## 长期记忆索引（MEMORY.md）\n"
                    "与用户相关的持久事实在下面索引里；需要细节时用 memory 工具 view，"
                    "用户说\"记住…\"时用 memory 工具落一条。\n\n" + index
                )
            if self._workspace_root is not None:
                workspace = user_workspace(self._workspace_root, user_id).root
                agents = read_agents_file(workspace)
                if agents:
                    parts.append("## 工作区约定（AGENTS.md）\n" + agents)
        skills = self._skills_for(user_id)
        if skills:
            parts.append(skills_index_text(skills))
        if expert:
            parts.append(
                "## 当前专家身份\n"
                f"{expert}\n"
                "以该身份与用户交流：介绍自己时忠实于上述身份与能力范围；"
                "执行任务时保持该领域专家的口吻与视角。"
            )
        return "\n\n".join(parts)

    async def run_prompt(
        self,
        text: str,
        *,
        user_id: int,
        session_id: str | None = None,
        on_event: EventHandler,
        model: str | None = None,
        effort: Effort = DEFAULT_EFFORT,
        steer_queue: asyncio.Queue[str] | None = None,
        skill: str | None = None,
        expert: str | None = None,
        hidden: bool = False,
        images: list[dict] | None = None,
    ) -> None:
        """驱动一次 Agent Loop；结束后队列剩余按 Follow-up 语义自动续跑。

        skill（Claude Code 式手动选择）：正文注入该 run 的每条用户消息——
        确定性生效，不依赖模型自觉 load_skill。每轮结束检查真实 input_tokens，
        接近窗口上限即自动压缩（ticket 10）。
        expert（专家模式）：注入该 run 的 system prompt，人格跨轮持续。
        hidden：触发词不入库（仅首轮生效；Follow-up 是用户真实插话，正常落盘）。
        images（多模态）：随首条用户消息进入上下文的图片块（扁平字典，票 24 形状）；
        Follow-up 插话为纯文本。
        并发上限（ticket 15 / ADR-0005）：多个会话的 run 可并行；超出 max_concurrency
        的 run 在信号量上排队。
        """
        # ADR-0005：本 run 的所有下行事件统一携带 session_id（单连接多路复用的路由前提）
        if session_id is not None:
            base_on_event = on_event

            async def on_event(ev: dict) -> None:
                await base_on_event({**ev, "session_id": session_id})

        async with self._loop_sem:
            first = True
            while True:
                usage = await self._run_single(
                    text,
                    user_id=user_id,
                    session_id=session_id,
                    on_event=on_event,
                    model=model,
                    effort=effort,
                    steer_queue=steer_queue,
                    skill=skill,
                    expert=expert,
                    hidden=hidden and first,
                    images=images if first else None,
                )
                first = False
                if (
                    usage is not None
                    and session_id is not None
                    and usage.input_tokens >= AUTO_COMPACT_INPUT_TOKENS
                ):
                    await self.compact_session(
                        user_id=user_id, session_id=session_id, on_event=on_event, model=model, effort=effort
                    )
                followups = _drain(steer_queue)
                if not followups:
                    return
                await on_event({"type": "queue_update", "pending": 0})
                text = followups[0]

    # ---- 压缩（ticket 10） ----

    async def set_max_concurrency(self, limit: int) -> None:
        """Settings 热更：并发上限收紧不打断在跑 run，放宽即唤醒排队。"""
        await self._loop_sem.set_limit(limit)

    def set_default_model(self, model: str | None) -> None:
        """Settings 热更：默认模型（未显式切换模型的会话用）；None = 尚未设置。"""
        self._default_model = model

    @property
    def default_model(self) -> str | None:
        return self._default_model

    @property
    def max_concurrency(self) -> int:
        return self._loop_sem.limit

    def session_model(self, user_id: int, session_id: str | None) -> str | None:
        """会话当前模型：显式切换 > 该用户上次使用的模型 > 默认模型（可 None=尚未配置）。"""
        if session_id is not None:
            explicit = self._session_models.get((user_id, session_id))
            if explicit is not None:
                return explicit
        return self._last_model.get(user_id) or self._default_model

    def session_effort(self, user_id: int, session_id: str | None) -> Effort:
        """会话当前 thinking level（映射 API effort）。"""
        if session_id is None:
            return DEFAULT_EFFORT
        return self._session_efforts.get((user_id, session_id), DEFAULT_EFFORT)

    def set_session_model(self, user_id: int, session_id: str | None, model: str) -> bool:
        """切换会话模型（对下一 run 生效）；非法模型拒绝。model_change 落盘。"""
        if model not in MODELS:
            return False
        if session_id is not None:
            self._session_models[(user_id, session_id)] = model
            if self._store is not None:
                self._store.append_model_change(user_id, session_id, model)
        return True

    def set_session_effort(self, user_id: int, session_id: str | None, level: str) -> bool:
        """切换会话 thinking level（low/medium/high/xhigh/max）。"""
        if level not in ("low", "medium", "high", "xhigh", "max"):
            return False
        if session_id is not None:
            self._session_efforts[(user_id, session_id)] = level  # type: ignore[assignment]
        return True

    async def compact_session(
        self,
        *,
        user_id: int,
        session_id: str | None,
        on_event: EventHandler,
        model: str | None = None,
        effort: Effort = DEFAULT_EFFORT,
    ) -> bool:
        """把活跃分支早期内容压缩为摘要（手动 compact 命令与自动触发共用）。

        返回是否真的压缩。消息不足、无会话或摘要失败都不拖垮会话。
        """
        if self._store is None or session_id is None:
            return False
        await on_event({"type": "compaction_start"})
        try:
            entries = self._store.read_active_branch(user_id, session_id)
            message_entries = [e for e in entries if e["type"] == "message"]
            if len(message_entries) <= COMPACT_KEEP_LAST:
                await on_event({"type": "compaction_end", "compacted": False})
                return False
            all_messages = self._store.replay_messages(user_id, session_id)
            keep = min(COMPACT_KEEP_LAST, len(all_messages) - 1)
            keep_messages = all_messages[-keep:]
            if any(is_summary_message(m) for m in keep_messages):
                # 保留段里已是摘要合成消息——真实消息不足，无意义
                await on_event({"type": "compaction_end", "compacted": False})
                return False
            old_messages = all_messages[:-keep]
            first_kept_entry_id = message_entries[-keep]["id"]
            transcript = transcribe(old_messages)
            summary = await summarize(
                self._provider, model or self._default_model, effort, transcript
            )
            self._store.append_compaction(
                user_id, session_id, summary=summary, first_kept_entry_id=first_kept_entry_id
            )
            await on_event(
                {"type": "compaction_end", "compacted": True, "summary_chars": len(summary)}
            )
            return True
        except Exception:
            # 摘要生成失败等：不拖垮会话，仅上报未压缩
            await on_event({"type": "compaction_end", "compacted": False})
            return False

    # ---- 内部：单次 run ----

    async def _run_single(
        self,
        text: str,
        *,
        user_id: int,
        session_id: str | None,
        on_event: EventHandler,
        model: str | None,
        effort: Effort,
        steer_queue: asyncio.Queue[str] | None,
        skill: str | None = None,
        expert: str | None = None,
        hidden: bool = False,
        images: list[dict] | None = None,
    ) -> None:
        state = _RunState()
        state.ext = self._ext_manager_for(user_id)
        await self._ensure_mcp()

        # 手动选定的技能（Claude Code 式）：正文注入消息，确定性生效；
        # 未知名 → error 事件收尾，不发起 run
        if skill:
            body, available = self.resolve_skill(user_id, skill)
            if body is None:
                names = "、".join(s["name"] for s in available) or "（无）"
                await on_event(
                    {
                        "type": "error",
                        "code": "skill_not_found",
                        "message": f"技能不存在: {skill}（可用: {names}）",
                    }
                )
                return None
            text = f'<skill name="{skill}">\n{body}\n</skill>\n\n{text}'

        # 模型凭证校验（Settings："只有已配置的模型才有回答"）。
        # 仅对声明了 configured 的 provider 生效（真实路由）——未声明的（测试替身等）
        # 不做模型校验。use_model 回落链：显式指定 > 会话显式切换 > 上次使用 > 默认。
        use_model = model or self.session_model(user_id, session_id)
        configured = getattr(self._provider, "configured", None)
        if configured is not None:
            if use_model is None:
                await on_event(
                    {
                        "type": "error",
                        "code": "model_not_configured",
                        "message": "尚未添加任何模型——请到设置页添加模型配置后再对话",
                    }
                )
                return None
            if not configured(use_model):
                await on_event(
                    {
                        "type": "error",
                        "code": "model_not_configured",
                        "message": f"API 配置错误：模型 {use_model} 未配置 API key——请在设置页添加凭证或切换到已配置的模型",
                    }
                )
                return None

        # 多模态：图片块随首条用户消息进入上下文；纯图消息允许空文本
        content: list[ContentBlock] = []
        if text:
            content.append(TextBlock(text))
        for im in images or []:
            content.append(
                ImageBlock(
                    media_type=str(im.get("media_type", "")),
                    data=str(im.get("data", "")),
                )
            )
        user_message = Message(role="user", content=content)
        history = self._replay(user_id, session_id) if session_id else []
        # hidden（专家开场触发词）：只进本轮模型上下文，不入库——
        # 回放时模型的开场自我介绍就是会话的第一条消息，触发词不留痕
        if not hidden:
            self._append_entry(user_id, session_id, user_message)

        async def handle(ev: object) -> None:
            await self._on_loop_event(ev, state, on_event, user_id, session_id)

        hooks = Hooks()
        if self._approval is not None:
            # ADR-0001：审批以内置 before_tool_call 钩子落地（白名单放行，否则挂起等待）
            hooks.on_before_tool_call(
                make_approval_hook(
                    self._approval,
                    user_id=user_id,
                    session_id=session_id,
                    on_event=on_event,
                )
            )
        if state.ext.has_tool_call_hooks():
            # 扩展 tool_call 钩子：返回 str 拦截，或就地修改 params dict（ticket 12）
            async def ext_tool_call_hook(name: str, params: dict, call_id: str) -> str | None:
                return await state.ext.run_tool_call_hooks(name, params)  # type: ignore[union-attr]

            hooks.on_before_tool_call(ext_tool_call_hook)

        try:
            await state.ext.emit("session_start", {"user_id": user_id, "session_id": session_id})
            context = await state.ext.apply_context([*history, user_message])
            await state.ext.emit(
                "before_agent_start",
                {"user_id": user_id, "session_id": session_id, "message_count": len(context)},
            )
            await agent_loop(
                self._provider,
                use_model,
                self._tools_for(user_id, state.ext),
                context,
                handle,
                system=self._system_prompt(user_id, expert=expert),
                effort=effort,
                hooks=hooks,
                steer_queue=steer_queue,
            )
            self._last_model[user_id] = use_model  # 记住本次模型，新会话继承（Settings 需求）
        except asyncio.CancelledError:
            if state.started:
                await on_event(
                    {
                        "type": "message_end",
                        "msg_id": state.msg_id,
                        "stop_reason": "aborted",
                        "usage": None,
                    }
                )
            else:
                # ADR-0005：模型尚未产出就被取消(删除运行中会话/定点 abort)——
                # 也要给前端一个收尾事件,否则该会话的运行态永远挂起
                await on_event(
                    {"type": "agent_end", "stop_reason": "aborted", "usage": {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}}
                )
            self._append_entry(
                user_id, session_id, Message(role="assistant", content=state.partial),
                stop_reason="aborted",
            )
            raise
        except ProviderError as exc:
            if state.started:
                await on_event(
                    {
                        "type": "message_end",
                        "msg_id": state.msg_id,
                        "stop_reason": "error",
                        "usage": None,
                    }
                )
            await on_event({"type": "error", "code": exc.code, "message": str(exc)})
        return state.usage_out  # 自动压缩凭真实 input_tokens 判断（ticket 10）

    async def _on_loop_event(
        self,
        ev: object,
        state: _RunState,
        on_event: EventHandler,
        user_id: int,
        session_id: str | None,
    ) -> None:
        """agent 事件 → WS dict 映射 + 落盘。"""
        if isinstance(ev, AgentStart):
            await on_event({"type": "agent_start"})
        elif isinstance(ev, TurnStart):
            await on_event({"type": "turn_start", "turn": ev.turn})
        elif isinstance(ev, MessageStart):
            state.started = True
            state.msg_id = f"msg_{uuid4().hex[:12]}"
            await on_event(
                {"type": "message_start", "msg_id": state.msg_id, "role": "assistant"}
            )
        elif isinstance(ev, TextDelta):
            _accumulate(state.partial, TextBlock(ev.delta))
            await on_event(
                {
                    "type": "message_update",
                    "msg_id": state.msg_id,
                    "delta": {"type": "text", "text": ev.delta},
                }
            )
        elif isinstance(ev, ThinkingDelta):
            _accumulate(state.partial, ThinkingBlock(ev.delta))
            await on_event(
                {
                    "type": "message_update",
                    "msg_id": state.msg_id,
                    "delta": {"type": "thinking", "text": ev.delta},
                }
            )
        elif isinstance(ev, MessageEnd):
            state.stop_reason = ev.stop_reason
            state.usage_out = ev.usage
            state.final_message = ev.message
            await on_event(
                {
                    "type": "message_end",
                    "msg_id": state.msg_id,
                    "stop_reason": ev.stop_reason,
                    "usage": asdict(ev.usage),
                }
            )
            # 每个 Turn 的 assistant 消息都以 SDK 最终消息落盘（含 thinking 签名与工具调用块）
            self._append_entry(
                user_id,
                session_id,
                ev.message,
                usage=ev.usage,
                stop_reason=ev.stop_reason,
            )
        elif isinstance(ev, ToolExecutionStart):
            await on_event(
                {
                    "type": "tool_execution_start",
                    "tool_call_id": ev.tool_call_id,
                    "name": ev.name,
                    "params": ev.params,
                }
            )
        elif isinstance(ev, ToolExecutionUpdate):
            await on_event(
                {
                    "type": "tool_execution_update",
                    "tool_call_id": ev.tool_call_id,
                    "delta": ev.delta,
                }
            )
        elif isinstance(ev, ToolExecutionEnd):
            await on_event(
                {
                    "type": "tool_execution_end",
                    "tool_call_id": ev.tool_call_id,
                    "content": ev.result.content,
                    "is_error": ev.result.is_error,
                    "blocks": ev.result.blocks,  # 票 24：富块给前端渲染
                }
            )
            if state.ext is not None:
                await state.ext.emit(
                    "tool_result",
                    {
                        "tool_call_id": ev.tool_call_id,
                        "is_error": ev.result.is_error,
                        "content_chars": len(ev.result.content),
                    },
                )
        elif isinstance(ev, MessagesAppended):
            for message in ev.messages:  # toolResult 与 steer 消息落盘（不发 WS）
                self._append_entry(user_id, session_id, message)
        elif isinstance(ev, TurnEnd):
            await on_event(
                {"type": "turn_end", "turn": ev.turn, "stop_reason": ev.stop_reason}
            )
            if state.ext is not None:
                await state.ext.emit(
                    "turn_end", {"turn": ev.turn, "stop_reason": ev.stop_reason}
                )
        elif isinstance(ev, AgentEnd):
            await on_event(
                {
                    "type": "agent_end",
                    "stop_reason": ev.stop_reason,
                    "usage": asdict(ev.usage),
                }
            )
            if state.ext is not None:
                await state.ext.emit("agent_settled", {"stop_reason": ev.stop_reason})

    def _replay(self, user_id: int, session_id: str) -> list[Message]:
        if self._store is None:
            return []
        return self._store.replay_messages(user_id, session_id)

    def _append_entry(
        self,
        user_id: int,
        session_id: str | None,
        message: Message,
        *,
        usage: Usage | None = None,
        stop_reason: str | None = None,
    ) -> None:
        if self._store is None or session_id is None:
            return
        # 分叉指针消费一次：分叉后首条消息挂指定条目，之后自然沿新分支的叶继续
        parent = self._branch_points.pop((user_id, session_id), None)
        self._store.append_message(
            user_id,
            session_id,
            message,
            usage=usage,
            stop_reason=stop_reason,
            parent_entry_id=parent,
        )
