"""Engine SPI and the deterministic portable execution implementation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from .errors import InvocationCancelled
from .lifecycle import (
    INVOCATION_STATES,
    TERMINAL_INVOCATION_STATES,
    ActiveInvocation,
    LifecycleControl,
)
from .observability import WorkflowEvent
from .persistence import ExecutionHandle
from .services import RuntimeServices
from .workflow import (
    SUPPORTED_FUNCTION_CALLS,
    SUPPORTED_PROTOCOL_CALLS,
    SUPPORTED_TASKS,
    WorkflowExecutor,
    WorkflowPlan,
)


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

    def as_dict(self) -> dict[str, Any]:
        return {
            "runtime": "open-workflow-agent",
            "runtimeVersion": "0.1.0",
            "engine": self.engine,
            "workflowDsl": "1.0.3",
            "portableProfile": self.portable_profile,
            "tasks": list(self.tasks),
            "protocols": list(self.protocols),
            "functions": list(self.functions),
            "policies": list(self.policies),
            "features": {
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

    def __init__(self) -> None:
        self._active: dict[str, ActiveInvocation] = {}
        self._restart_resume_inputs: dict[str, Any] = {}

    async def initialize(self, services: RuntimeServices) -> None:
        self.services = services

    async def compile(self, plan: WorkflowPlan) -> WorkflowPlan:
        return plan

    async def invoke(
        self, workflow: WorkflowPlan, invocation: ExecutionHandle, input_data: Any
    ) -> InvocationResult:
        raise NotImplementedError

    async def resume(
        self, handle: ExecutionHandle, resume_input: Any, plan: WorkflowPlan
    ) -> InvocationResult:
        self.services.invocations.verify_fingerprint(handle, plan.fingerprint)
        active_result = await self._resume_active_or_terminal(handle, resume_input)
        if active_result is not None:
            return active_result
        if handle.status == "waiting":
            self._restart_resume_inputs[handle.invocation_id] = resume_input
            self.services.invocations.update(handle, status="running")
        if handle.status != "running":
            return self._stored_result(handle)
        return await self.invoke(plan, handle, resume_input)

    async def cancel(
        self, handle: ExecutionHandle, *, operation_id: str | None = None
    ) -> InvocationResult:
        if handle.status in TERMINAL_INVOCATION_STATES:
            return self._stored_result(handle)
        active = self._active.get(handle.invocation_id)
        error = InvocationCancelled(
            "invocation cancellation requested",
            details={"operation_id": operation_id} if operation_id else {},
        )
        if active is not None:
            active.control.token.cancel("cancelled")
        self.services.invocations.update(handle, status="cancelled", error=error.as_dict())
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
        )
        restart_input = self._restart_resume_inputs.pop(handle.invocation_id, None)
        if restart_input is not None:
            active.control.request_resume(restart_input)
        self._active[handle.invocation_id] = active
        return active

    def _finish_active(self, handle: ExecutionHandle, result: InvocationResult) -> None:
        active = self._active.pop(handle.invocation_id, None)
        if active is not None and not active.result.done():
            active.result.set_result(result)

    async def _resume_active_or_terminal(
        self, handle: ExecutionHandle, resume_input: Any
    ) -> InvocationResult | None:
        if handle.status in TERMINAL_INVOCATION_STATES:
            return self._stored_result(handle)
        active = self._active.get(handle.invocation_id)
        if active is None:
            return None
        if handle.status == "waiting":
            self.services.invocations.update(handle, status="running")
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
            "_invocation_handle": invocation,
            "_lifecycle": active.control,
        }

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
        self.services.invocations.update(handle, status=status, output=output, error=error)
        return InvocationResult(handle.invocation_id, handle.session_id, status, output, error)

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(engine=self.engine_name)

    async def shutdown(self) -> None:
        return None


class PortableWorkflowEngine(WorkflowEngine):
    """Reference executor used by both adapters and all deterministic tests."""

    async def initialize(self, services: RuntimeServices) -> None:
        await super().initialize(services)
        self.executor = WorkflowExecutor(
            services.catalog, services=services, event_sink=services.lifecycle_events
        )

    async def invoke(
        self, workflow: WorkflowPlan, invocation: ExecutionHandle, input_data: Any
    ) -> InvocationResult:
        active = self._begin_active(invocation)
        result: InvocationResult | None = None
        try:
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
                self._active.pop(invocation.invocation_id, None)

    async def resume(
        self, handle: ExecutionHandle, resume_input: Any, plan: WorkflowPlan
    ) -> InvocationResult:
        return await super().resume(handle, resume_input, plan)
