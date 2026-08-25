"""LangGraph engine adapter."""

from pathlib import Path
from typing import Any

from open_workflow_agent.engine import EngineCapabilities, InvocationResult, PortableWorkflowEngine
from open_workflow_agent.persistence import ExecutionHandle
from open_workflow_agent.workflow import WorkflowPlan

from .agent import LangGraphAgentFactory
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
        factory = LangGraphAgentFactory()
        self.agent = factory.create(
            services.config.agent,
            services.config.model,
            factory.bind_tools(services.agent_tool_bindings()),
        )
        database = (
            services.database_root / "langgraph-checkpoints.sqlite3"
            if services.database_root
            else Path(services.config.persistence.database).with_name(
                "langgraph-checkpoints.sqlite3"
            )
        )
        self.native.database_path = str(database)

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

    async def resume(
        self, handle: ExecutionHandle, resume_input: Any, plan: WorkflowPlan
    ) -> InvocationResult:
        self.services.invocations.verify_fingerprint(handle, plan.fingerprint)
        if not self.native.checkpointer and not self.native.database_path:
            return await super().resume(handle, resume_input, plan)
        if handle.status not in {"running", "waiting", "suspended"}:
            return await super().resume(handle, resume_input, plan)
        try:
            output = await self.native.resume(
                lambda value: self.executor.execute(
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
                ),
                resume_input,
                thread_id=handle.session_id,
            )
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


__all__ = ["LangGraphWorkflowEngine"]
