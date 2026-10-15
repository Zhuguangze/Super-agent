"""队列排空工具——Steer / Follow-up 共用，支持 all / one-at-a-time 两种排空模式。"""

import asyncio


def _drain(queue: asyncio.Queue[str] | None, *, mode: str = "one-at-a-time") -> list[str]:
    """排空队列。mode="all" 一次性取全部；mode="one-at-a-time" 每轮只取一条。"""
    if queue is None:
        return []
    if mode == "all":
        items: list[str] = []
        while True:
            try:
                items.append(queue.get_nowait())
            except asyncio.QueueEmpty:
                return items
    # one-at-a-time（默认）：每轮只注入一条，剩余留到下一轮
    try:
        return [queue.get_nowait()]
    except asyncio.QueueEmpty:
        return []
