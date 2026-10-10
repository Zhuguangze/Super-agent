"""可动态调整上限的计数信号量（ticket：Settings 热生效）。

asyncio.Semaphore 的上限不可变，这里用 Condition 实现 acquire/release，
limit 收紧时不打断已持有者（新请求按新上限排队），放宽时唤醒等待者。
"""

import asyncio


class AdjustableSemaphore:
    def __init__(self, limit: int) -> None:
        self._limit = max(1, limit)
        self._active = 0
        self._cond = asyncio.Condition()

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def active(self) -> int:
        return self._active

    async def set_limit(self, limit: int) -> None:
        async with self._cond:
            self._limit = max(1, limit)
            self._cond.notify_all()

    async def acquire(self) -> None:
        async with self._cond:
            while self._active >= self._limit:
                await self._cond.wait()
            self._active += 1

    async def release(self) -> None:
        async with self._cond:
            self._active -= 1
            self._cond.notify_all()

    async def __aenter__(self) -> "AdjustableSemaphore":
        await self.acquire()
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.release()
