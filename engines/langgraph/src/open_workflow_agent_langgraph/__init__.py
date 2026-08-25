"""LangGraph engine adapter."""

from typing import Any

from open_workflow_agent.engine import EngineCapabilities, InvocationResult, PortableWorkflowEngine
from open_workflow_agent.errors import InvocationCancelled
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
        super().__init__()
        self.native = LangGraphFunctionalAdapter()

    async def initialize(self, services: Any) -> None:
        await super().initialize(services)
        factory = LangGraphAgentFactory()
        self.agent = factory.create(
            services.config.agent,
            services.config.model,
            factory.bind_tools(services.agent_tool_bindings()),
        )
        self.native.database_path = services.engine_database_path("langgraph")

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(engine=self.engine_name, resume=True, streaming=False)

    async def invoke(
        self, workflow: WorkflowPlan, invocation: ExecutionHandle, input_data: Any
    ) -> InvocationResult:
        if not self.native.checkpointer and not self.native.database_path:
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
                thread_id=invocation.session_id,
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

    async def resume(
        self, handle: ExecutionHandle, resume_input: Any, plan: WorkflowPlan
    ) -> InvocationResult:
        self.services.invocations.verify_fingerprint(handle, plan.fingerprint)
        active_result = await self._resume_active_or_terminal(handle, resume_input)
        if active_result is not None:
            return active_result
        if not self.native.checkpointer and not self.native.database_path:
            return await PortableWorkflowEngine.resume(self, handle, resume_input, plan)
        if handle.status == "waiting":
            self._restart_resume_inputs[handle.invocation_id] = resume_input
            self.services.invocations.update(handle, status="running")
        if handle.status != "running":
            return self._stored_result(handle)
        active = self._begin_active(handle)
        result: InvocationResult | None = None
        try:
            output = await self.native.resume(
                lambda value: self.executor.execute(
                    plan,
                    value,
                    metadata=self._metadata(plan, handle, active),
                ),
                resume_input,
                thread_id=handle.session_id,
            )
            result = self._record_result(handle, status="completed", output=output)
            return result
        except InvocationCancelled as exc:
            result = self._record_result(handle, status="cancelled", error=exc.as_dict())
            return result
        except Exception as exc:
            error = (
                exc.as_dict()
                if hasattr(exc, "as_dict")
                else {"code": "workflow_execution_error", "message": str(exc), "details": {}}
            )
            result = self._record_result(handle, status="faulted", error=error)
            return result
        finally:
            if result is not None:
                self._finish_active(handle, result)
            else:
                self._active.pop(handle.invocation_id, None)


__all__ = ["LangGraphWorkflowEngine"]
