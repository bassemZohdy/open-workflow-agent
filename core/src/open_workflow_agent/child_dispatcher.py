"""Bounded detached child delivery through the existing engine ownership path."""

from __future__ import annotations

import asyncio
import json
from collections import deque
from typing import TYPE_CHECKING

from .effects import EffectIdentity
from .errors import UnsupportedWorkflowFeature, WorkflowExecutionError
from .sandbox import resolve_and_compile_sandbox_workflow

if TYPE_CHECKING:
    from .engine import WorkflowEngine
    from .services import RuntimeServices
    from .workflow import WorkflowPlan


class ChildDispatcher:
    def __init__(self, services: RuntimeServices, engine: WorkflowEngine) -> None:
        self.services = services
        self.engine = engine
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._errors: deque[Exception] = deque(maxlen=128)
        self._cursor: str | None = None

    async def stop(self) -> None:
        tasks = list(self._workers.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._workers.clear()

    async def tick(self, *, job_key: str | None = None) -> None:
        jobs = self.services.children.pending_job_keys(
            self.engine.engine_name,
            job_key=job_key,
            after_key=self._cursor if job_key is None else None,
        )
        if not jobs and job_key is None:
            self._cursor = None
        for candidate_key in jobs:
            if len(self._workers) >= self.services.config.child_workflows.max_concurrency:
                break
            if job_key is None:
                self._cursor = candidate_key
            try:
                identity = self.services.children.job_identity(candidate_key)
                receipt = self.services.effect_journal.get(identity)
                if receipt is None:
                    raise WorkflowExecutionError("detached child decision is absent")
                child_id = receipt.output["child_id"]
                if (
                    child_id in self._workers
                    or child_id in self.engine._active
                    or child_id in self.engine._ownership_tasks
                ):
                    continue
                # Timer delivery owns its exact due/wake semantics. Reattaching
                # a queued child must never turn an early scan into manual resume.
                child = self.services.invocations.get(child_id)
                if child is None:
                    raise WorkflowExecutionError("detached child invocation is absent")
                if self.services.timers.resumable_for_invocation(
                    child_id, child.workflow_fingerprint
                ) or self.services.child_waits.for_invocation(child_id, child.workflow_fingerprint):
                    continue
                self._workers[child_id] = asyncio.create_task(self._dispatch(identity, child_id))
            except Exception as error:
                self._errors.append(error)
        await asyncio.sleep(0)

    async def _dispatch(self, identity: EffectIdentity, child_id: str) -> None:
        try:
            child = self.services.invocations.get(child_id)
            if child is None:
                raise WorkflowExecutionError("detached child invocation is absent")

            async def load_plan() -> WorkflowPlan:
                receipt = self.services.effect_journal.get(identity)
                if receipt is None or receipt.output["child_id"] != child_id:
                    raise WorkflowExecutionError("detached child decision is inconsistent")
                run_identity = EffectIdentity(
                    identity.invocation_id,
                    identity.workflow_fingerprint,
                    identity.task_reference,
                    identity.occurrence,
                    "run:workflow",
                )
                binding = self.services.children.get(run_identity)
                parent = self.services.invocations.get(identity.invocation_id)
                if binding is None or parent is None:
                    raise WorkflowExecutionError("detached child parent binding is absent")
                source = json.loads(binding.workflow_source_json)
                try:
                    plan = self.services.workflow_catalog.resolve(source["document"])
                except UnsupportedWorkflowFeature:
                    plan = await resolve_and_compile_sandbox_workflow(
                        source,
                        sandbox=self.services.config.sandbox,
                        trusted_catalogs=self.services.config.workflow.external_catalogs,
                        resolver=self.services.external_catalogs,
                        catalog=self.services.catalog,
                    )
                self.services.children.verify(
                    binding, parent, plan, self.services.invocations.read_input(child)
                )
                return plan

            await self.engine.dispatch_child(child, load_plan)
        except Exception as error:
            self._errors.append(error)
        finally:
            self._workers.pop(child_id, None)
