"""agent 层钩子——before_tool_call（可拦截）/ after_tool_call（可替换）/ finish_turn。

Tool Approval（ADR-0001，ticket 07）挂在 before_tool_call 上；Extension 系统（ticket 12）
的 tool_call / tool_result / turn_end 钩子也接到这里。
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

from super_agent.agent.types import AgentToolResult

@dataclass
class ToolCallBlock:
    """before_tool_call 拒绝工具调用时的决策：reason 为拒绝原因，terminate 请求终止整批。"""

    reason: str
    terminate: bool = False


BeforeToolCallFn = Callable[[str, dict, str], Awaitable[str | ToolCallBlock | None]]
"""(tool_name, params, tool_call_id) -> None 放行；str 或 ToolCallBlock 为拒绝。"""

AfterToolCallFn = Callable[[str, AgentToolResult], Awaitable[AgentToolResult]]
"""(tool_call_id, result) -> AgentToolResult（可替换）。"""

FinishTurnDecision = Literal["end", "continue"]
FinishTurnFn = Callable[[int], Awaitable[FinishTurnDecision | None]]
"""(turn_index) -> "end" 强制结束；"continue" 主动续跑；None 不干预。"""


@dataclass
class Hooks:
    before_tool_call: list[BeforeToolCallFn] = field(default_factory=list)
    after_tool_call: list[AfterToolCallFn] = field(default_factory=list)
    finish_turn: list[FinishTurnFn] = field(default_factory=list)

    def on_before_tool_call(self, fn: BeforeToolCallFn) -> None:
        self.before_tool_call.append(fn)

    def on_after_tool_call(self, fn: AfterToolCallFn) -> None:
        self.after_tool_call.append(fn)

    def on_finish_turn(self, fn: FinishTurnFn) -> None:
        self.finish_turn.append(fn)

    async def run_before_tool_call(
        self, name: str, params: dict, tool_call_id: str
    ) -> str | ToolCallBlock | None:
        """任一钩子返回非 None 即拦截，返回值为拒绝原因（str 或 ToolCallBlock）。"""
        for fn in self.before_tool_call:
            reason = await fn(name, params, tool_call_id)
            if reason is not None:
                return reason
        return None

    async def run_after_tool_call(self, tool_call_id: str, result: AgentToolResult) -> AgentToolResult:
        for fn in self.after_tool_call:
            result = await fn(tool_call_id, result)
        return result

    async def run_finish_turn(self, turn: int) -> FinishTurnDecision | None:
        """任一钩子返回非 None 决策即生效（短路，与 before_tool_call 一致）。"""
        for fn in self.finish_turn:
            decision = await fn(turn)
            if decision is not None:
                return decision
        return None
