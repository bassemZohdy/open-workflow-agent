"""Engine SPI and the deterministic portable execution implementation."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from ._version import __version__
from .errors import (
    InvocationCancelled,
    InvocationStateConflict,
    InvocationStateError,
    OwaError,
    WorkflowExecutionError,
)
from .lifecycle import (
    INVOCATION_STATES,
    TERMINAL_INVOCATION_STATES,
    ActiveInvocation,
    LifecycleControl,
)
from .observability import WorkflowEvent
from .ownership import ExecutionLease
from .persistence import ExecutionHandle
from .sandbox import SandboxWorkflowExecutor
from .services import RuntimeServices
from .workflow import (
    SUPPORTED_FUNCTION_CALLS,
    SUPPORTED_PROTOCOL_CALLS,
    SUPPORTED_TASKS,
    WorkflowPlan,
)
from .workflow_profile import WORKFLOW_DSL, workflow_profile


@dataclass(frozen=True, slots=True)
class EngineCapabilities:
    engine: str
    portable_profile: str = "1"
    tasks: tuple[str, ...] = SUPPORTED_TASKS
    protocols: tuple[str, ...] = SUPPORTED_PROTOCOL_CALLS
    functions: tuple[str, ...] = SUPPORTED_FUNCTION_CALLS
    policies: tuple[str, ...] = ("retry", "timeout")
    resume: bool = True
    streaming: bool = False
    cancellation: bool = True
    waiting: bool = True
    native_task_nodes: bool = False
    native_scope_step_limit: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "runtime": "open-workflow-agent",
            "runtimeVersion": __version__,
            "engine": self.engine,
            "workflowDsl": WORKFLOW_DSL,
            "workflowProfile": workflow_profile(),
            "portableProfile": self.portable_profile,
            "tasks": list(self.tasks),
            "protocols": list(self.protocols),
            "functions": list(self.functions),
            "policies": list(self.policies),
            "features": {
                "nativeTaskExecution": {
                    "nodes": self.native_task_nodes,
                    "scopeStepLimit": self.native_scope_step_limit,
                },
                "resume": self.resume,
                "streaming": self.streaming,
                "cancellation": self.cancellation,
                "waiting": self.waiting,
                "events": {"emit": True, "listen": True, "durable": False},
                "cloudEvents": {
                    "lifecycle": True,
                    "specversion": "1.0",
                    "delivery": "bounded_snapshot",
                    "durable": False,
                },
                "scheduling": {
                    "after": True,
                    "every": True,
                    "cron": False,
                    "on": False,
                    "durable": True,
                    "owner": "single_runtime",
                },
                "subWorkflows": {
                    "run": True,
                    "separateInvocation": True,
                    "localCatalog": True,
                    "externalCatalog": False,
                },
            },
        }


@dataclass(slots=True)
class InvocationResult:
    invocation_id: str
    session_id: str
    status: str
    output: Any = None
    error: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "invocation_id": self.invocation_id,
            "session_id": self.session_id,
            "status": self.status,
            "output": self.output,
        }
        if self.error:
            value["error"] = self.error
        return value


class WorkflowEngine:
    engine_name = "base"
    _execution_ownership_seconds: float = 30

    def __init__(self) -> None:
        self._active: dict[str, ActiveInvocation] = {}
        self._restart_resume_inputs: dict[str, Any] = {}
        self._execution_leases: dict[str, ExecutionLease] = {}
        self._ownership_tasks: dict[str, asyncio.Task[Any]] = {}

    async def _wait_for_ownership_renewal(self) -> None:
        await asyncio.sleep(self._execution_ownership_seconds / 3)

    @asynccontextmanager
    async def _own_execution(self, handle: ExecutionHandle) -> AsyncIterator[None]:
        store = self.services.execution_ownership
        lease = store.acquire(handle, seconds=self._execution_ownership_seconds)
        self._execution_leases[handle.invocation_id] = lease
        handle._execution_lease = lease
        owner_task = asyncio.current_task()
        if owner_task is not None:
            self._ownership_tasks[handle.invocation_id] = owner_task
        lost: OwaError | None = None

        async def renew() -> None:
            nonlocal lease, lost
            try:
                while True:
                    await self._wait_for_ownership_renewal()
                    lease = store.renew(lease, seconds=self._execution_ownership_seconds)
                    self._execution_leases[handle.invocation_id] = lease
                    handle._execution_lease = lease
            except Exception as error:
                lost = (
                    error
                    if isinstance(error, OwaError)
                    else InvocationStateError("execution ownership renewal failed")
                )
                if owner_task is not None:
                    owner_task.cancel()

        heartbeat = asyncio.create_task(renew())
        try:
            yield
            store.verify(lease)
        except asyncio.CancelledError:
            if lost is not None:
                raise InvocationStateConflict(
                    "invocation execution ownership was lost; recovery is required"
                ) from None
            raise
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            self._execution_leases.pop(handle.invocation_id, None)
            self._ownership_tasks.pop(handle.invocation_id, None)
            handle._execution_lease = None
            try:
                store.release(lease)
            except OwaError:
                # Expiry, another owner or unavailable storage cannot justify a
                # fresh state write. A failed release expires naturally.
                pass

    async def initialize(self, services: RuntimeServices) -> None:
        self.services = services
        services.workflow_runner = self.run_child

    async def run_child(
        self,
        workflow: WorkflowPlan,
        input_data: Any,
        parent_context: dict[str, Any],
        task_reference: str,
    ) -> Any:
        parent_handle = parent_context.get("_invocation_handle")
        if not isinstance(parent_handle, ExecutionHandle):
            raise WorkflowExecutionError("nested workflow requires an invocation handle")
        child = self.services.invocations.create(
            engine=self.engine_name,
            session_id=None,
            user_id=parent_handle.user_id,
            workflow_name=workflow.name,
            workflow_version=workflow.version,
            workflow_fingerprint=workflow.fingerprint,
            parent_invocation_id=parent_handle.invocation_id,
            parent_task_reference=task_reference,
            owner_principal=parent_handle.owner_principal,
        )
        try:
            result = await self.invoke(workflow, child, input_data)
        except asyncio.CancelledError:
            await self.cancel(
                child,
                operation_id=f"{parent_handle.invocation_id}:{task_reference}",
            )
            raise
        if result.status == "completed":
            return result.output
        if result.status == "cancelled":
            raise InvocationCancelled(
                "nested workflow was cancelled",
                details={"invocation_id": child.invocation_id},
            )
        raise WorkflowExecutionError(
            "nested workflow faulted",
            details={"invocation_id": child.invocation_id, "error": result.error or {}},
        )

    async def compile(self, plan: WorkflowPlan) -> WorkflowPlan:
        return plan

    def _verify_execution_engine(self, handle: ExecutionHandle) -> None:
        # Preserve stored common-reference identities across recovery.
        reference_alias = self.engine_name == "base" and handle.engine in {"portable", "reference"}
        if handle.engine != self.engine_name and not reference_alias:
            raise InvocationStateError("invocation belongs to a different engine")

    async def invoke(
        self, workflow: WorkflowPlan, invocation: ExecutionHandle, input_data: Any
    ) -> InvocationResult:
        self.services.invocations.verify_fingerprint(invocation, workflow.fingerprint)
        self._verify_execution_engine(invocation)
        self.services.invocations.refresh(invocation)
        if invocation.status in TERMINAL_INVOCATION_STATES:
            return self._stored_result(invocation)
        active = self._active.get(invocation.invocation_id)
        if active is not None:
            return await asyncio.shield(active.result)
        try:
            async with self._own_execution(invocation):
                self.services.invocations.refresh(invocation)
                if invocation.status in TERMINAL_INVOCATION_STATES:
                    return self._stored_result(invocation)
                return await self._invoke_owned(workflow, invocation, input_data)
        except InvocationStateConflict as error:
            try:
                self.services.invocations.refresh(invocation, terminal_only=True)
            except InvocationStateConflict:
                raise error from None
            return self._stored_result(invocation)

    async def _invoke_owned(
        self, workflow: WorkflowPlan, invocation: ExecutionHandle, input_data: Any
    ) -> InvocationResult:
        raise NotImplementedError

    async def resume(
        self, handle: ExecutionHandle, resume_input: Any, plan: WorkflowPlan
    ) -> InvocationResult:
        self.services.invocations.verify_fingerprint(handle, plan.fingerprint)
        self._verify_execution_engine(handle)
        active_result = await self._resume_active_or_terminal(handle, resume_input)
        if active_result is not None:
            return active_result
        try:
            async with self._own_execution(handle):
                self.services.invocations.refresh(handle)
                if handle.status in TERMINAL_INVOCATION_STATES:
                    return self._stored_result(handle)
                await self._verify_resume_state(handle, plan)
                return await self._resume_owned(handle, resume_input, plan)
        except InvocationStateConflict as error:
            try:
                self.services.invocations.refresh(handle, terminal_only=True)
            except InvocationStateConflict:
                raise error from None
            return self._stored_result(handle)

    async def _resume_owned(
        self, handle: ExecutionHandle, resume_input: Any, plan: WorkflowPlan
    ) -> InvocationResult:
        if handle.status == "waiting":
            self._restart_resume_inputs[handle.invocation_id] = resume_input
            self.services.invocations.update(handle, status="running")
        if handle.status != "running":
            return self._stored_result(handle)
        return await self._invoke_owned(plan, handle, resume_input)

    async def cancel(
        self, handle: ExecutionHandle, *, operation_id: str | None = None
    ) -> InvocationResult:
        self.services.invocations.refresh(handle)
        if handle.status in TERMINAL_INVOCATION_STATES:
            return self._stored_result(handle)
        active = self._active.get(handle.invocation_id)
        error = InvocationCancelled(
            "invocation cancellation requested",
            details={"operation_id": operation_id} if operation_id else {},
        )
        owned_handle = active.handle if active is not None and active.handle is not None else handle
        if owned_handle is not handle:
            self.services.invocations.refresh(owned_handle)
        if owned_handle.status in TERMINAL_INVOCATION_STATES:
            self.services.invocations.refresh(handle)
            return self._stored_result(handle)
        # An authorized operator cancellation is a control write, not another
        # execution by the active worker. Keep the worker's private token on its
        # handle while committing through a freshly loaded control handle.
        control_handle = self.services.invocations.get(handle.invocation_id)
        if control_handle is None:
            raise InvocationStateError("invocation state is unavailable")
        if control_handle.status in TERMINAL_INVOCATION_STATES:
            self.services.invocations.refresh(owned_handle)
            self.services.invocations.refresh(handle)
            return self._stored_result(control_handle)
        try:
            self.services.invocations.update(
                control_handle, status="cancelled", error=error.as_dict()
            )
        except InvocationStateConflict:
            self.services.invocations.refresh(control_handle, terminal_only=True)
            self.services.invocations.refresh(owned_handle)
            self.services.invocations.refresh(handle)
            return self._stored_result(control_handle)
        self.services.invocations.refresh(owned_handle)
        if owned_handle is not handle:
            self.services.invocations.refresh(handle)
        if active is not None:
            active.control.token.cancel("cancelled")
        if active is None:
            self.services.lifecycle_events.emit(
                WorkflowEvent(
                    event_type="WorkflowCancelled",
                    invocation_id=handle.invocation_id,
                    session_id=handle.session_id,
                    workflow_name=handle.workflow_name,
                    workflow_version=handle.workflow_version,
                    engine=handle.engine,
                    operation_id=operation_id,
                    status="cancelled",
                    reason="cancelled",
                    error=error.as_dict(),
                )
            )
        return self._stored_result(handle)

    def _begin_active(self, handle: ExecutionHandle) -> ActiveInvocation:
        existing = self._active.get(handle.invocation_id)
        if existing is not None:
            return existing
        active = ActiveInvocation(
            control=LifecycleControl(),
            task=asyncio.current_task(),
            result=asyncio.get_running_loop().create_future(),
            handle=handle,
        )
        if handle.invocation_id in self._restart_resume_inputs:
            active.control.request_resume(self._restart_resume_inputs.pop(handle.invocation_id))
        self._active[handle.invocation_id] = active
        return active

    def _finish_active(self, handle: ExecutionHandle, result: InvocationResult) -> None:
        active = self._active.pop(handle.invocation_id, None)
        if active is not None and not active.result.done():
            active.result.set_result(result)

    def _abandon_active(self, handle: ExecutionHandle) -> None:
        active = self._active.pop(handle.invocation_id, None)
        if active is not None and not active.result.done():
            active.result.set_exception(
                InvocationStateConflict(
                    "invocation execution was interrupted; recovery is required"
                )
            )
            # Coalesced callers can still await the future. Consume the error
            # when there are no such callers so cleanup never leaves a warning.
            active.result.exception()

    async def _resume_active_or_terminal(
        self, handle: ExecutionHandle, resume_input: Any
    ) -> InvocationResult | None:
        self.services.invocations.refresh(handle)
        if handle.status in TERMINAL_INVOCATION_STATES:
            return self._stored_result(handle)
        active = self._active.get(handle.invocation_id)
        if active is None:
            return None
        if handle.status == "waiting":
            owned_handle = active.handle if active.handle is not None else handle
            if owned_handle is not handle:
                self.services.invocations.refresh(owned_handle)
            if owned_handle.status in TERMINAL_INVOCATION_STATES:
                self.services.invocations.refresh(handle)
                return self._stored_result(handle)
            self.services.invocations.update(owned_handle, status="running")
            if owned_handle is not handle:
                self.services.invocations.refresh(handle)
            active.control.request_resume(resume_input)
        return await asyncio.shield(active.result)

    @staticmethod
    def _stored_result(handle: ExecutionHandle) -> InvocationResult:
        return InvocationResult(
            handle.invocation_id,
            handle.session_id,
            handle.status,
            handle.output,
            handle.error,
        )

    def _metadata(
        self, workflow: WorkflowPlan, invocation: ExecutionHandle, active: ActiveInvocation
    ) -> dict[str, Any]:
        return {
            "invocation_id": invocation.invocation_id,
            "session_id": invocation.session_id,
            "engine": invocation.engine,
            "engine_execution_reference": invocation.engine_execution_reference,
            "workflow_name": workflow.name,
            "workflow_version": workflow.version,
            "parent_invocation_id": invocation.parent_invocation_id,
            "parent_task_reference": invocation.parent_task_reference,
            "_invocation_handle": invocation,
            "_lifecycle": active.control,
        }

    def _prepare_workflow_input(
        self, workflow: WorkflowPlan, invocation: ExecutionHandle, input_data: Any
    ) -> Any:
        self.services.invocations.verify_fingerprint(invocation, workflow.fingerprint)
        return self.services.invocations.bind_input(invocation, input_data)

    async def _verify_resume_state(self, handle: ExecutionHandle, plan: WorkflowPlan) -> Any:
        self._verify_execution_engine(handle)
        self.services.invocations.verify_fingerprint(handle, plan.fingerprint)
        return self.services.invocations.read_input(handle)

    def _record_result(
        self,
        handle: ExecutionHandle,
        *,
        status: str,
        output: Any = None,
        error: dict[str, Any] | None = None,
    ) -> InvocationResult:
        if status not in INVOCATION_STATES:
            raise ValueError(f"unsupported invocation status: {status}")
        if handle.status in TERMINAL_INVOCATION_STATES:
            return self._stored_result(handle)
        try:
            self.services.invocations.update(handle, status=status, output=output, error=error)
        except InvocationStateConflict:
            self.services.invocations.refresh(handle, terminal_only=True)
            return self._stored_result(handle)
        return InvocationResult(handle.invocation_id, handle.session_id, status, output, error)

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(engine=self.engine_name)

    async def shutdown(self) -> None:
        tasks = {
            active.task
            for active in self._active.values()
            if active.task is not None and active.task is not asyncio.current_task()
        } | {task for task in self._ownership_tasks.values() if task is not asyncio.current_task()}
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        sandbox = getattr(self.services, "sandbox", None)
        if sandbox is not None:
            await sandbox.shutdown()


class PortableWorkflowEngine(WorkflowEngine):
    """Reference executor used by both adapters and all deterministic tests."""

    async def initialize(self, services: RuntimeServices) -> None:
        await super().initialize(services)
        self.executor = SandboxWorkflowExecutor(
            services.catalog, services=services, event_sink=services.lifecycle_events
        )

    async def invoke(
        self, workflow: WorkflowPlan, invocation: ExecutionHandle, input_data: Any
    ) -> InvocationResult:
        return await super().invoke(workflow, invocation, input_data)

    async def resume(
        self, handle: ExecutionHandle, resume_input: Any, plan: WorkflowPlan
    ) -> InvocationResult:
        return await super().resume(handle, resume_input, plan)

    async def _invoke_owned(
        self, workflow: WorkflowPlan, invocation: ExecutionHandle, input_data: Any
    ) -> InvocationResult:
        active = self._begin_active(invocation)
        result: InvocationResult | None = None
        try:
            input_data = self._prepare_workflow_input(workflow, invocation, input_data)
            output = await self.executor.execute(
                workflow,
                input_data,
                metadata=self._metadata(workflow, invocation, active),
            )
            if active.control.token.cancelled or invocation.status == "cancelled":
                result = self._record_result(
                    invocation,
                    status="cancelled",
                    error=invocation.error,
                )
            else:
                result = self._record_result(invocation, status="completed", output=output)
            return result
        except InvocationCancelled as exc:
            result = self._record_result(
                invocation,
                status="cancelled",
                error=exc.as_dict(),
            )
            return result
        except Exception as exc:
            error = (
                exc.as_dict()
                if hasattr(exc, "as_dict")
                else {"code": "workflow_execution_error", "message": str(exc), "details": {}}
            )
            if invocation.status == "cancelled":
                result = self._stored_result(invocation)
            else:
                result = self._record_result(invocation, status="faulted", error=error)
            return result
        finally:
            if result is not None:
                self._finish_active(invocation, result)
            else:
                self._abandon_active(invocation)
