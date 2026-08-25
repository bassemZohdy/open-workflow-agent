"""LangGraph engine adapter."""

from typing import Any

from open_workflow_agent.engine import EngineCapabilities, InvocationResult, PortableWorkflowEngine
from open_workflow_agent.persistence import ExecutionHandle
from open_workflow_agent.workflow import WorkflowPlan

from .native import LangGraphFunctionalAdapter


class LangGraphWorkflowEngine(PortableWorkflowEngine):
    """LangGraph-facing engine boundary.

    The common executor supplies the portable semantics. A native LangGraph
    Functional API compiler can be selected by deployments that need native
    graph persistence; deterministic contract tests remain framework-neutral.
    """

    engine_name = "langgraph"

    def __init__(self) -> None:
        self.native = LangGraphFunctionalAdapter()

    async def initialize(self, services: Any) -> None:
        await super().initialize(services)
        if services.database_root:
            self.native.database_path = str(
                services.database_root / "langgraph-checkpoints.sqlite3"
            )

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(engine=self.engine_name, resume=True, streaming=False)

    async def invoke(
        self, workflow: WorkflowPlan, invocation: ExecutionHandle, input_data: Any
    ) -> InvocationResult:
        if not self.native.checkpointer and not self.native.database_path:
            return await super().invoke(workflow, invocation, input_data)
        try:
            output = await self.native.invoke(
                lambda value: self.executor.execute(
                    workflow, value, metadata={"invocation_id": invocation.invocation_id}
                ),
                input_data,
                thread_id=invocation.session_id,
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


__all__ = ["LangGraphWorkflowEngine"]
