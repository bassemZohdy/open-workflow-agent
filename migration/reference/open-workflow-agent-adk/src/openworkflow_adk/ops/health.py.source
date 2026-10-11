"""Health and graceful-shutdown primitives for workflow hosts."""

from __future__ import annotations

import asyncio
import signal
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any


class WorkflowHealth:
    """Track readiness and in-flight runs for an HTTP or service host."""

    def __init__(self) -> None:
        self.ready = True
        self._active = 0
        self._idle = asyncio.Event()
        self._idle.set()

    def health(self) -> dict[str, str]:
        return {"status": "ok"}

    def readiness(self) -> dict[str, object]:
        return {"ready": self.ready, "in_flight": self._active}

    @asynccontextmanager
    async def run(self) -> AsyncIterator[None]:
        if not self.ready:
            raise RuntimeError("workflow host is draining")
        self._active += 1
        self._idle.clear()
        try:
            yield
        finally:
            self._active -= 1
            if self._active == 0:
                self._idle.set()

    async def shutdown(self, timeout: float = 30.0) -> bool:
        """Stop accepting runs and wait for active runs to drain."""
        self.ready = False
        try:
            await asyncio.wait_for(self._idle.wait(), timeout=timeout)
        except (TimeoutError, asyncio.TimeoutError):
            return False
        return True


class WorkflowHost:
    """Host adapter that exposes probes and drains runs on SIGTERM."""

    def __init__(self, shutdown_timeout: float = 30.0) -> None:
        self.health = WorkflowHealth()
        self.shutdown_timeout = shutdown_timeout
        self._shutdown_task: asyncio.Task[bool] | None = None

    def install_signal_handlers(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        loop = loop or asyncio.get_running_loop()
        try:
            loop.add_signal_handler(signal.SIGTERM, self._begin_shutdown)
        except (NotImplementedError, RuntimeError):
            pass

    def _begin_shutdown(self) -> None:
        if self._shutdown_task is None:
            self._shutdown_task = asyncio.create_task(
                self.health.shutdown(timeout=self.shutdown_timeout)
            )

    async def execute(self, document: Any, input: dict[str, Any] | None = None, **kwargs: Any):
        from openworkflow_adk.runtime import run_workflow

        async with self.health.run():
            return await run_workflow(document, input, **kwargs)

    async def shutdown(self) -> bool:
        return await self.health.shutdown(timeout=self.shutdown_timeout)
