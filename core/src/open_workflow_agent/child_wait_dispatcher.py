"""Bounded recovery of parent-owned waits through existing native ownership."""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from typing import TYPE_CHECKING

from .errors import UnsupportedWorkflowFeature, WorkflowExecutionError
from .sandbox import resolve_and_compile_sandbox_workflow

if TYPE_CHECKING:
    from .engine import WorkflowEngine
    from .services import RuntimeServices
    from .workflow import WorkflowPlan


class ChildWaitDispatcher:
    def __init__(self, services: RuntimeServices, engine: WorkflowEngine) -> None:
        self.services = services
        self.engine = engine
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._errors: deque[Exception] = deque(maxlen=128)
        self._cursor: str | None = None
        self._cancel_cursor: str | None = None

    async def stop(self) -> None:
        tasks = list(self._workers.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._workers.clear()

    async def tick(self, *, run_key: str | None = None) -> None:
        executor = getattr(self.engine, "executor", None)
        keys = (
            [run_key]
            if run_key
            else self.services.child_waits.pending_keys(
                self.engine.engine_name,
                now=executor._absolute_clock() if executor is not None else time.time(),
                after_key=self._cursor,
            )
        )
        if not keys and run_key is None:
            self._cursor = None
        for key in keys:
            if len(self._workers) >= self.services.config.child_workflows.max_concurrency:
                break
            if run_key is None:
                self._cursor = key
            try:
                registration = self.services.child_waits.registration_for_key(key)
                if registration is None:
                    raise WorkflowExecutionError("child wait registration is absent")
                parent_id = registration.identity.invocation_id
                if (
                    parent_id in self._workers
                    or parent_id in self.engine._active
                    or parent_id in self.engine._ownership_tasks
                ):
                    continue
                self._workers[parent_id] = asyncio.create_task(self._dispatch(key, parent_id))
            except Exception as error:
                self._errors.append(error)
        if run_key is None:
            await self._reconcile_cancellation()
        await asyncio.sleep(0)

    async def _reconcile_cancellation(self) -> None:
        scoped = " AND binding.binding_key>?" if self._cancel_cursor else ""
        params = (self._cancel_cursor,) if self._cancel_cursor else ()
        rows = self.services.children.connection.execute(
            "SELECT binding.binding_key,binding.parent_id FROM child_workflow_bindings AS binding "
            "JOIN invocations AS parent ON parent.invocation_id=binding.parent_id "
            "JOIN invocations AS child ON child.invocation_id=binding.child_id "
            "LEFT JOIN detached_child_jobs AS jobs ON jobs.binding_key=binding.binding_key "
            "WHERE parent.status IN ('cancelled','faulted','completed') "
            "AND child.status IN ('running','waiting') AND jobs.job_key IS NULL"
            + scoped
            + " ORDER BY binding.binding_key LIMIT 128",
            params,
        ).fetchall()
        if not rows:
            self._cancel_cursor = None
        for key, parent_id in rows:
            self._cancel_cursor = key
            try:
                parent = self.services.invocations.get(parent_id)
                if parent is None:
                    raise WorkflowExecutionError("child cancellation parent is absent")
                if parent.engine != self.engine.engine_name:
                    continue
                await self.engine._cancel_synchronous_children(parent)
            except Exception as error:
                self._errors.append(error)

    async def _dispatch(self, key: str, parent_id: str) -> None:
        try:
            registration = self.services.child_waits.registration_for_key(key)
            if registration is None:
                raise WorkflowExecutionError("child wait registration is absent")

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
                        grpc_enabled=self.services.grpc_enabled,
                        enabled_function_calls=self.services.role_function_calls,
                    )

            await self.engine.dispatch_child_wait(registration, load_plan)
        except Exception as error:
            self._errors.append(error)
        finally:
            self._workers.pop(parent_id, None)
