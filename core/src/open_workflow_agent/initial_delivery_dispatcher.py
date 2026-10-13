"""Bounded fair delivery of admitted initial invocations through their engine."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
from typing import TYPE_CHECKING

from .errors import WorkflowExecutionError

if TYPE_CHECKING:
    from .engine import WorkflowEngine
    from .persistence import ExecutionHandle
    from .services import RuntimeServices
    from .workflow import WorkflowPlan


class InitialDeliveryDispatcher:
    def __init__(self, services: RuntimeServices, engine: WorkflowEngine) -> None:
        self.services = services
        self.engine = engine
        self._task: asyncio.Task[None] | None = None
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._errors: deque[Exception] = deque(maxlen=128)
        self._cursor: str | None = None
        self._authorize: Callable[[ExecutionHandle, str | None, WorkflowPlan], bool] | None = None

    async def start(
        self, authorize: Callable[[ExecutionHandle, str | None, WorkflowPlan], bool]
    ) -> None:
        self._authorize = authorize
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        tasks = [task for task in (self._task, *self._workers.values()) if task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._workers.clear()
        self._task = None
        self._authorize = None

    async def _run(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception as error:
                self._errors.append(error)
            await asyncio.sleep(self.services.config.execution_timing.poll_interval_seconds)

    def _verify_authority(self, handle: ExecutionHandle, plan: WorkflowPlan) -> None:
        binding = self.services.initial_deliveries.get(handle)
        if binding is None:
            raise WorkflowExecutionError("initial delivery binding is absent")
        if self._authorize is None or not self._authorize(handle, binding.skill_id, plan):
            raise WorkflowExecutionError("initial delivery deployment authority is unavailable")

    async def tick(self) -> None:
        ids = self.services.initial_deliveries.pending_ids(
            self.engine.engine_name, after_id=self._cursor
        )
        if not ids:
            self._cursor = None
        for invocation_id in ids:
            if len(self._workers) >= self.services.config.execution_timing.max_concurrency:
                break
            self._cursor = invocation_id
            if invocation_id in self._workers or invocation_id in self.engine._ownership_tasks:
                continue
            try:
                handle = self.services.invocations.get(invocation_id)
                if handle is None:
                    raise WorkflowExecutionError("initial delivery invocation is absent")
                self.services.initial_deliveries.get(handle)
                # Original-definition and current authority checks precede lease
                # acquisition, SDK setup, native mutation and receiver I/O.
                if handle.status in {"running", "waiting"}:
                    plan = self.services.resolve_original_definition(handle)
                    self._verify_authority(handle, plan)
                self._workers[invocation_id] = asyncio.create_task(self._dispatch(invocation_id))
            except Exception as error:
                self._errors.append(error)
        await asyncio.sleep(0)

    async def _dispatch(self, invocation_id: str) -> None:
        try:
            handle = self.services.invocations.get(invocation_id)
            if handle is None:
                raise WorkflowExecutionError("initial delivery invocation is absent")
            await self.engine.dispatch_initial_delivery(handle, self._verify_authority)
        except Exception as error:
            self._errors.append(error)
        finally:
            self._workers.pop(invocation_id, None)
