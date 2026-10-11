"""ADK engine adapter."""

from typing import Any

from open_workflow_agent.engine import EngineCapabilities, InvocationResult, PortableWorkflowEngine
from open_workflow_agent.errors import InvocationCancelled, InvocationStateError
from open_workflow_agent.persistence import ExecutionHandle
from open_workflow_agent.workflow import WorkflowPlan

from .agent import AdkAgentFactory
from .native import ADK_AVAILABLE, NativeAdkRunner


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
        super().__init__()
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
        active = self._begin_active(invocation)
        result: InvocationResult | None = None
        try:
            input_data = self._prepare_workflow_input(workflow, invocation, input_data)
            output = await self.native.invoke(
                lambda value: self.executor.execute(
                    workflow,
                    value,
                    metadata=self._metadata(workflow, invocation, active),
                ),
                input_data,
                session_id=invocation.session_id,
                user_id=invocation.user_id,
                invocation_id=invocation.engine_execution_reference,
                database_path=self._session_database_path(),
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
        await self._verify_resume_state(handle, plan)
        if not self.native.available:
            return await PortableWorkflowEngine.resume(self, handle, resume_input, plan)
        if handle.status == "waiting":
            self._restart_resume_inputs[handle.invocation_id] = resume_input
            self.services.invocations.update(handle, status="running")
        if handle.status != "running":
            return self._stored_result(handle)
        active = self._begin_active(handle)
        result: InvocationResult | None = None
        try:
            workflow_input = self._prepare_workflow_input(plan, handle, resume_input)
            execution_completed = False
            execution_output: Any = None

            async def execute_workflow(value: Any) -> Any:
                nonlocal execution_completed, execution_output
                execution_output = await self.executor.execute(
                    plan,
                    value,
                    metadata=self._metadata(plan, handle, active),
                )
                execution_completed = True
                return execution_output

            output = await self.native.resume(
                execute_workflow,
                workflow_input,
                session_id=handle.session_id,
                user_id=handle.user_id,
                invocation_id=handle.engine_execution_reference,
                database_path=self._session_database_path(),
            )
            if execution_completed:
                output = execution_output
            elif output is None:
                # ADK 2.x can complete a resumed dynamic node without
                # calling the portable runner after restart. Recover through
                # the same plan only when no callback completion was observed;
                # a completed callback may validly return JSON null.
                output = await execute_workflow(workflow_input)
            result = (
                self._stored_result(handle)
                if active.control.token.cancelled or handle.status == "cancelled"
                else self._record_result(handle, status="completed", output=output)
            )
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
            result = (
                self._stored_result(handle)
                if handle.status == "cancelled"
                else self._record_result(handle, status="faulted", error=error)
            )
            return result
        finally:
            if result is not None:
                self._finish_active(handle, result)
            else:
                self._active.pop(handle.invocation_id, None)

    async def _verify_resume_state(self, handle: ExecutionHandle, plan: WorkflowPlan) -> Any:
        workflow_input = await super()._verify_resume_state(handle, plan)
        if ADK_AVAILABLE and not self.native.available:
            raise InvocationStateError(
                "native execution state is unavailable; recovery is required"
            )
        if self.native.available:
            await self.native.admit_resume(
                workflow_input,
                session_id=handle.session_id,
                user_id=handle.user_id,
                invocation_id=handle.engine_execution_reference,
                database_path=self._session_database_path(),
            )
        return workflow_input


__all__ = ["AdkWorkflowEngine"]
