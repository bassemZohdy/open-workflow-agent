"""One bounded timer poller dispatches common handles through their own engine."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from inspect import isawaitable
from typing import TYPE_CHECKING

from .child_dispatcher import ChildDispatcher
from .child_wait_dispatcher import ChildWaitDispatcher
from .errors import OwaError, UnsupportedWorkflowFeature
from .lifecycle import TERMINAL_INVOCATION_STATES
from .sandbox import resolve_and_compile_sandbox_workflow
from .timers import _CURRENT_TIMER_CLAIM, TimerClaim

if TYPE_CHECKING:
    from .engine import WorkflowEngine
    from .persistence import ExecutionHandle
    from .services import RuntimeServices
    from .workflow import WorkflowPlan


class TimerDispatcher:
    def __init__(
        self,
        services: RuntimeServices,
        engine: WorkflowEngine,
        *,
        clock: Callable[[], float],
        poll_seconds: float = 0.05,
        max_concurrency: int = 16,
        definition_preflight: Callable[[ExecutionHandle], WorkflowPlan | Awaitable[WorkflowPlan]]
        | None = None,
    ) -> None:
        self.services = services
        self.engine = engine
        self.clock = clock
        self.poll_seconds = poll_seconds
        self.max_concurrency = max_concurrency
        self._definition_preflight = definition_preflight or getattr(
            services, "resolve_original_definition", None
        )
        self.owner = uuid.uuid4().hex
        self._task: asyncio.Task[None] | None = None
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._errors: deque[Exception] = deque(maxlen=128)
        self._stopping = False
        self.children = ChildDispatcher(services, engine)
        self.child_waits = ChildWaitDispatcher(services, engine)

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stopping = True
        tasks = [
            t
            for t in [self._task, *self._workers.values()]
            if t is not None and t is not asyncio.current_task()
        ]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.children.stop()
        await self.child_waits.stop()
        self._task = None
        self._workers.clear()
        self._stopping = False

    async def _run(self) -> None:
        while True:
            try:
                await self.tick()
                await self.children.tick()
                await self.child_waits.tick()
            except Exception as error:
                self._errors.append(error)
            await asyncio.sleep(self.poll_seconds)

    async def tick(self, *, now: float | None = None, invocation_id: str | None = None) -> int:
        if self._stopping:
            return 0
        current = self.clock() if now is None else now
        admitted = 0
        for key in self.services.timers.due_keys(now=current, invocation_id=invocation_id):
            if len(self._workers) >= self.max_concurrency:
                break
            try:
                registration = self.services.timers.registration_for_key(key)
                handle = self.services.invocations.get(registration.identity.invocation_id)
                if handle is None or handle.engine != self.engine.engine_name:
                    continue
                if handle.status in TERMINAL_INVOCATION_STATES:
                    self.services.timers.close_invocation(
                        handle.invocation_id, handle.workflow_fingerprint
                    )
                    continue
                if (
                    handle.invocation_id in self._workers
                    or handle.invocation_id in self.engine._active
                    or handle.invocation_id in self.engine._ownership_tasks
                ):
                    continue
                if self._definition_preflight is None:
                    raise UnsupportedWorkflowFeature(
                        "timer recovery requires original definition verification"
                    )
                # Pure metadata/trust verification precedes the mutable claim.
                # SDK setup and execution remain inside the owning engine.
                checked = self._definition_preflight(handle)
                if isawaitable(checked):
                    await checked
                claim = self.services.timers.claim_due(owner=self.owner, now=current, timer_key=key)
                if claim is None:
                    continue
                self._workers[handle.invocation_id] = asyncio.create_task(self._dispatch(claim))
                admitted += 1
            except Exception as error:
                self._errors.append(error)
        await asyncio.sleep(0)
        return admitted

    async def _dispatch(self, claim: TimerClaim) -> None:
        registration = claim.registration
        invocation_id = registration.identity.invocation_id
        token = _CURRENT_TIMER_CLAIM.set(claim)
        try:
            self.services.timers.verify_claim(claim, now=self.clock())
            handle = self.services.invocations.get(invocation_id)
            if handle is None:
                return

            async def load_plan() -> WorkflowPlan:
                source = json.loads(registration.workflow_source_json)
                try:
                    return self.services.workflow_catalog.resolve(source["document"])
                except UnsupportedWorkflowFeature:
                    return await resolve_and_compile_sandbox_workflow(
                        source,
                        sandbox=self.services.config.sandbox,
                        trusted_catalogs=self.services.config.workflow.external_catalogs,
                        resolver=self.services.external_catalogs,
                        catalog=self.services.catalog,
                    )

            self.engine._timer_claims[invocation_id] = claim
            await self.engine._dispatch_timer(claim, load_plan)
        except OwaError as error:
            self._errors.append(error)
        except Exception as error:
            self._errors.append(error)
        finally:
            _CURRENT_TIMER_CLAIM.reset(token)
            self.engine._timer_claims.pop(invocation_id, None)
            self.services.timers.release_claim(claim)
            self._workers.pop(invocation_id, None)
