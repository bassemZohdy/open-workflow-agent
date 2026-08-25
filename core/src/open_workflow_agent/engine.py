"""Engine SPI and the deterministic portable execution implementation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import UnsupportedWorkflowFeature
from .persistence import ExecutionHandle
from .services import RuntimeServices
from .workflow import WorkflowExecutor, WorkflowPlan


@dataclass(frozen=True, slots=True)
class EngineCapabilities:
    engine: str
    portable_profile: str = "1"
    tasks: tuple[str, ...] = ("do", "call", "set", "switch", "for", "fork")
    functions: tuple[str, ...] = ("agent:1.0.0@default", "llm:1.0.0@default")
    resume: bool = True
    streaming: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "runtime": "open-workflow-agent",
            "runtimeVersion": "0.1.0",
            "engine": self.engine,
            "workflowDsl": "1.0.3",
            "portableProfile": self.portable_profile,
            "tasks": list(self.tasks),
            "functions": list(self.functions),
            "features": {"resume": self.resume, "streaming": self.streaming},
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
        raise UnsupportedWorkflowFeature("resume is not implemented by this engine")

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(engine=self.engine_name)

    async def shutdown(self) -> None:
        return None


class PortableWorkflowEngine(WorkflowEngine):
    """Reference executor used by both adapters and all deterministic tests."""

    async def initialize(self, services: RuntimeServices) -> None:
        await super().initialize(services)
        self.executor = WorkflowExecutor(
            services.catalog, services=services, event_sink=services.events
        )

    async def invoke(
        self, workflow: WorkflowPlan, invocation: ExecutionHandle, input_data: Any
    ) -> InvocationResult:
        try:
            output = await self.executor.execute(
                workflow,
                input_data,
                metadata={
                    "invocation_id": invocation.invocation_id,
                    "session_id": invocation.session_id,
                    "engine": invocation.engine,
                    "engine_execution_reference": invocation.engine_execution_reference,
                    "workflow_name": workflow.name,
                    "workflow_version": workflow.version,
                },
            )
            invocation.status = "completed"
            self.services.invocations.update(invocation, status="completed")
            return InvocationResult(
                invocation.invocation_id, invocation.session_id, "completed", output
            )
        except Exception as exc:
            invocation.status = "faulted"
            self.services.invocations.update(invocation, status="faulted")
            error = (
                exc.as_dict()
                if hasattr(exc, "as_dict")
                else {"code": "workflow_execution_error", "message": str(exc), "details": {}}
            )
            return InvocationResult(
                invocation.invocation_id, invocation.session_id, "faulted", error=error
            )

    async def resume(
        self, handle: ExecutionHandle, resume_input: Any, plan: WorkflowPlan
    ) -> InvocationResult:
        self.services.invocations.verify_fingerprint(handle, plan.fingerprint)
        return await self.invoke(plan, handle, resume_input)
