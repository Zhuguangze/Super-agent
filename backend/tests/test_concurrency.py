"""ticket 15：全局并发 Agent Loop 上限——超出排队，完成后放行。"""

import asyncio

from super_agent.ai.types import (
    Message,
    MessageEnd,
    MessageStart,
    TextBlock,
    Usage,
)
from super_agent.app.chat import ChatService
from test_chat import _collector


class GatedProvider:
    """闸门 provider：记录进入调用数与当前并发，闸门控制节奏。"""

    id = "gated-concurrency"

    def __init__(self, gate: asyncio.Event) -> None:
        self._gate = gate
        self.calls = 0
        self.active = 0
        self.peak = 0

    async def stream(self, messages, model_id, *, system=None, effort="high", tools=None):
        self.calls += 1
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            yield MessageStart(model="m", usage=Usage())
            while not self._gate.is_set():
                await asyncio.sleep(0.01)
            yield MessageEnd(
                stop_reason="end_turn",
                usage=Usage(input_tokens=1, output_tokens=1),
                message=Message(role="assistant", content=[TextBlock("done")]),
            )
        finally:
            self.active -= 1


async def test_concurrency_limit_queues_extra_runs() -> None:
    """max_concurrency=1：第二个 run 排队，第一个完成后才开始。"""
    gate = asyncio.Event()
    provider = GatedProvider(gate)
    chat = ChatService(provider, max_concurrency=1)

    task1 = asyncio.create_task(chat.run_prompt("first", user_id=1, on_event=_collector([])))
    await asyncio.sleep(0.05)
    assert provider.calls == 1  # 第一个已进入
    assert provider.active == 1

    task2 = asyncio.create_task(chat.run_prompt("second", user_id=2, on_event=_collector([])))
    await asyncio.sleep(0.05)
    assert provider.calls == 1  # 第二个在信号量上排队，未进 provider
    assert task2.done() is False

    gate.set()
    await asyncio.wait_for(asyncio.gather(task1, task2), timeout=5)
    assert provider.calls == 2  # 第一个完成后第二个被放行
    assert provider.peak == 1  # 全程并发峰值 = 1


async def test_unlimited_when_concurrency_high() -> None:
    """并发上限足够时，多个 run 立即并行。"""
    gate = asyncio.Event()
    provider = GatedProvider(gate)
    chat = ChatService(provider, max_concurrency=4)

    tasks = [
        asyncio.create_task(chat.run_prompt(f"run{i}", user_id=i + 1, on_event=_collector([])))
        for i in range(3)
    ]
    await asyncio.sleep(0.05)
    assert provider.calls == 3  # 全部并行进入
    assert provider.peak == 3

    gate.set()
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)
