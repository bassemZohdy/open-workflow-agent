"""Explicit, bounded-scope result memoization for deterministic workflow calls."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any


class ResultMemoization:
    """In-process result cache shared by workflow runs."""

    def __init__(self, *, max_entries: int = 1024) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self.max_entries = max_entries
        self.values: dict[str, Any] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def key(operation: str, arguments: Any) -> str:
        return json.dumps(
            [operation, arguments], sort_keys=True, default=str, separators=(",", ":")
        )

    async def get_or_compute(self, key: str, factory: Callable[[], Any | Awaitable[Any]]) -> Any:
        async with self._lock:
            if key in self.values:
                return self.values[key]
            result = factory()
            if hasattr(result, "__await__"):
                result = await result
            if len(self.values) >= self.max_entries:
                self.values.pop(next(iter(self.values)))
            self.values[key] = result
            return result
