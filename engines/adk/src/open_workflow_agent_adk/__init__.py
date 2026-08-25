"""ADK engine adapter."""

from typing import Any

from open_workflow_agent.engine import EngineCapabilities, InvocationResult, PortableWorkflowEngine
from open_workflow_agent.persistence import ExecutionHandle
from open_workflow_agent.workflow import WorkflowPlan

from .agent import AdkAgentFactory
from .native import NativeAdkRunner


class AdkWorkflowEngine(PortableWorkflowEngine):
    """ADK-facing engine boundary.

    The adapter preserves the ADK boundary and capability contract. When the
    optional ADK package is installed, native compilation can be supplied
    here; the reference implementation uses the common portable executor so
    tests remain deterministic and do not require a provider.
    """

    engine_name = "adk"

    def _session_database_path(self) -> str:
        return self.services.engine_database_path("adk")

    def __init__(self) -> None:
        self.native = NativeAdkRunner()

    async def initialize(self, services: Any) -> None:
        await super().initialize(services)
        factory = AdkAgentFactory()
        self.agent = factory.create(
            services.config.agent,
            services.config.model,
            factory.bind_tools(services.agent_tool_bindings()),
        )

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(engine=self.engine_name, resume=True, streaming=False)

    async def invoke(
        self, workflow: WorkflowPlan, invocation: ExecutionHandle, input_data: Any
    ) -> InvocationResult:
        if not self.native.available:
            return await super().invoke(workflow, invocation, input_data)
        try:
            output = await self.native.invoke(
                lambda value: self.executor.execute(
                    workflow,
                    value,
                    metadata={
                        "invocation_id": invocation.invocation_id,
                        "session_id": invocation.session_id,
                        "engine": invocation.engine,
                        "engine_execution_reference": invocation.engine_execution_reference,
                        "workflow_name": workflow.name,
                        "workflow_version": workflow.version,
                    },
                ),
                input_data,
                session_id=invocation.session_id,
                user_id=invocation.user_id,
                invocation_id=invocation.engine_execution_reference,
                database_path=self._session_database_path(),
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
                invocation.invocation_id,
                invocation.session_id,
                "faulted",
                error=error,
            )

    async def resume(
        self, handle: ExecutionHandle, resume_input: Any, plan: WorkflowPlan
    ) -> InvocationResult:
        self.services.invocations.verify_fingerprint(handle, plan.fingerprint)
        if not self.native.available or handle.status not in {"running", "waiting", "suspended"}:
            return await super().resume(handle, resume_input, plan)
        try:

            async def execute_workflow(value: Any) -> Any:
                return await self.executor.execute(
                    plan,
                    value,
                    metadata={
                        "invocation_id": handle.invocation_id,
                        "session_id": handle.session_id,
                        "engine": handle.engine,
                        "engine_execution_reference": handle.engine_execution_reference,
                        "workflow_name": plan.name,
                        "workflow_version": plan.version,
                    },
                )

            output = await self.native.resume(
                execute_workflow,
                resume_input,
                session_id=handle.session_id,
                user_id=handle.user_id,
                invocation_id=handle.engine_execution_reference,
                database_path=self._session_database_path(),
            )
            if output is None:
                # ADK 2.x can complete a resumed dynamic node without
                # surfacing its returned value after a process restart. Keep
                # the native resume attempt, then recover the public result
                # through the same portable plan contract.
                output = await execute_workflow(resume_input)
            self.services.invocations.update(handle, status="completed")
            return InvocationResult(handle.invocation_id, handle.session_id, "completed", output)
        except Exception as exc:
            self.services.invocations.update(handle, status="faulted")
            error = (
                exc.as_dict()
                if hasattr(exc, "as_dict")
                else {"code": "workflow_execution_error", "message": str(exc), "details": {}}
            )
            return InvocationResult(handle.invocation_id, handle.session_id, "faulted", error=error)


__all__ = ["AdkWorkflowEngine"]
