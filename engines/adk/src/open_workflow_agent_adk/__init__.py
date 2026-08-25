"""ADK engine adapter."""

from typing import Any

from open_workflow_agent.engine import EngineCapabilities, InvocationResult, PortableWorkflowEngine
from open_workflow_agent.persistence import ExecutionHandle
from open_workflow_agent.workflow import WorkflowPlan

from .native import NativeAdkRunner


class AdkWorkflowEngine(PortableWorkflowEngine):
    """ADK-facing engine boundary.

    The adapter preserves the ADK boundary and capability contract. When the
    optional ADK package is installed, native compilation can be supplied
    here; the reference implementation uses the common portable executor so
    tests remain deterministic and do not require a provider.
    """

    engine_name = "adk"

    def __init__(self) -> None:
        self.native = NativeAdkRunner()

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
                    workflow, value, metadata={"invocation_id": invocation.invocation_id}
                ),
                input_data,
                session_id=invocation.session_id,
                user_id=invocation.user_id,
                database_path=(
                    str(self.services.database_root / "adk-sessions.sqlite3")
                    if self.services.database_root
                    else self.services.config.persistence.database
                ),
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


__all__ = ["AdkWorkflowEngine"]
