"""Microsoft Agent Framework engine adapter."""

from typing import Any

from open_workflow_agent.engine import EngineCapabilities, InvocationResult, PortableWorkflowEngine
from open_workflow_agent.errors import InvocationCancelled
from open_workflow_agent.persistence import ExecutionHandle
from open_workflow_agent.workflow import WorkflowPlan

from .native import AgentFrameworkNativeAdapter


class AgentFrameworkWorkflowEngine(PortableWorkflowEngine):
    """Agent Framework-facing engine boundary with common portable semantics."""

    engine_name = "agent-framework"

    def __init__(self) -> None:
        super().__init__()
        self.native = AgentFrameworkNativeAdapter()

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(engine=self.engine_name, resume=True, streaming=False)

    async def invoke(
        self, workflow: WorkflowPlan, invocation: ExecutionHandle, input_data: Any
    ) -> InvocationResult:
        if not self.native.available:
            return await super().invoke(workflow, invocation, input_data)

        active = self._begin_active(invocation)
        result: InvocationResult | None = None
        try:
            output = await self.native.invoke(
                lambda value: self.executor.execute(
                    workflow,
                    value,
                    metadata=self._metadata(workflow, invocation, active),
                ),
                input_data,
            )
            if active.control.token.cancelled or invocation.status == "cancelled":
                result = self._stored_result(invocation)
            else:
                result = self._record_result(invocation, status="completed", output=output)
            return result
        except InvocationCancelled as exc:
            result = self._record_result(invocation, status="cancelled", error=exc.as_dict())
            return result
        except Exception as exc:
            error = (
                exc.as_dict()
                if hasattr(exc, "as_dict")
                else {"code": "workflow_execution_error", "message": str(exc), "details": {}}
            )
            result = (
                self._stored_result(invocation)
                if invocation.status == "cancelled"
                else self._record_result(invocation, status="faulted", error=error)
            )
            return result
        finally:
            if result is not None:
                self._finish_active(invocation, result)
            else:
                self._active.pop(invocation.invocation_id, None)


__all__ = ["AgentFrameworkWorkflowEngine"]
