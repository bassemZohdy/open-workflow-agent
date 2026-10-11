"""Adaptive per-endpoint backpressure with hysteresis."""

from __future__ import annotations

import asyncio
from collections import defaultdict


class BackpressureController:
    """Limit concurrent work per endpoint and pause on queue pressure."""

    def __init__(self, *, max_inflight: int = 8, high_watermark: int = 16, low_watermark: int = 4):
        if not 0 <= low_watermark < high_watermark:
            raise ValueError("watermarks must satisfy 0 <= low < high")
        self.max_inflight = max_inflight
        self.high_watermark = high_watermark
        self.low_watermark = low_watermark
        self._semaphores: dict[str, asyncio.Semaphore] = defaultdict(
            lambda: asyncio.Semaphore(max_inflight)
        )
        self._depth: dict[str, int] = defaultdict(int)
        self._paused: set[str] = set()
        self._conditions: dict[str, asyncio.Condition] = defaultdict(asyncio.Condition)

    def update_depth(self, endpoint: str, depth: int) -> None:
        if depth < 0:
            raise ValueError("queue depth must be non-negative")
        self._depth[endpoint] = depth
        if depth >= self.high_watermark:
            self._paused.add(endpoint)
        elif depth <= self.low_watermark:
            self._paused.discard(endpoint)
            condition = self._conditions[endpoint]
            asyncio.create_task(self._notify(condition))

    async def _notify(self, condition: asyncio.Condition) -> None:
        async with condition:
            condition.notify_all()

    async def acquire(self, endpoint: str) -> None:
        condition = self._conditions[endpoint]
        async with condition:
            await condition.wait_for(lambda: endpoint not in self._paused)
        await self._semaphores[endpoint].acquire()

    def release(self, endpoint: str) -> None:
        self._semaphores[endpoint].release()
