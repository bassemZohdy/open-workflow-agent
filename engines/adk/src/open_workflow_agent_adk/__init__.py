"""ADK engine adapter."""

from pathlib import Path
from typing import Any

from open_workflow_agent._native_sqlite_inspection import inspect_native_sqlite
from open_workflow_agent.child_suspension import ChildSuspension
from open_workflow_agent.engine import EngineCapabilities, InvocationResult, PortableWorkflowEngine
from open_workflow_agent.errors import (
    InvocationCancelled,
    InvocationStateError,
    OwaError,
    WorkflowExecutionError,
)
from open_workflow_agent.persistence import ExecutionHandle
from open_workflow_agent.timers import TimerSuspension
from open_workflow_agent.workflow import WorkflowPlan

from .agent import AdkAgentFactory
from .native import ADK_AVAILABLE, AdkRecoveryContext, NativeAdkRunner


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

    async def _native_initial_history_exists(self, handle: ExecutionHandle) -> bool:
        common = self.services.effect_journal.connection
        try:
            if common.is_postgresql:
                tables = {
                    row[0]
                    for row in common.execute(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema='owa_adk'"
                    ).fetchall()
                }
                if not tables:
                    return False
                if not {"sessions", "events"}.issubset(tables):
                    raise InvocationStateError("native initial storage schema is incomplete")
                return (
                    common.execute(
                        "SELECT 1 FROM owa_adk.events WHERE app_name=? AND user_id=? "
                        "AND session_id=? AND invocation_id=? LIMIT 1",
                        (
                            "open-workflow-agent",
                            handle.user_id or "owa-user",
                            handle.session_id,
                            handle.engine_execution_reference,
                        ),
                    ).fetchone()
                    is not None
                )
            path = Path(self.services.engine_database_path("adk", create_namespace=False))
            if not path.is_file():
                return False
            common_path = next(
                Path(row[2]) for row in common.execute("PRAGMA database_list") if row[1] == "main"
            )
            with inspect_native_sqlite(path, common_database=common_path) as db:
                tables = {
                    row[0]
                    for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                if not tables:
                    return False
                if not {"sessions", "events"}.issubset(tables):
                    raise InvocationStateError("native initial storage schema is incomplete")
                return (
                    db.execute(
                        "SELECT 1 FROM events WHERE app_name=? AND user_id=? "
                        "AND session_id=? AND invocation_id=? LIMIT 1",
                        (
                            "open-workflow-agent",
                            handle.user_id or "owa-user",
                            handle.session_id,
                            handle.engine_execution_reference,
                        ),
                    ).fetchone()
                    is not None
                )
        except InvocationStateError:
            raise
        except Exception:
            raise InvocationStateError("native initial storage could not be inspected") from None

    async def _preflight_native_continuation(self, handle: ExecutionHandle) -> None:
        if ADK_AVAILABLE and not self.native.available:
            raise InvocationStateError("native recovery storage is unavailable")
        if self.native.available and not await self._native_initial_history_exists(handle):
            raise InvocationStateError(
                "native recovery evidence is missing",
                details={"reason": "native_state_missing", "stage": "native"},
            )

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
        return await super().invoke(workflow, invocation, input_data)

    async def _invoke_owned(
        self, workflow: WorkflowPlan, invocation: ExecutionHandle, input_data: Any
    ) -> InvocationResult:
        if not self.native.available:
            return await super()._invoke_owned(workflow, invocation, input_data)
        active = self._begin_active(invocation)
        result: InvocationResult | None = None
        try:
            input_data = self._prepare_workflow_input(workflow, invocation, input_data)
            metadata = self._metadata(workflow, invocation, active)
            output = await self.native.invoke(
                lambda value: self.executor.execute(
                    workflow,
                    value,
                    metadata=metadata,
                ),
                input_data,
                session_id=invocation.session_id,
                user_id=invocation.user_id,
                invocation_id=invocation.engine_execution_reference,
                database_path=self._session_database_path(),
                _recovery_context=self._recovery_context(workflow, invocation, metadata),
                _execution_fence=self.services.execution_ownership.native_fence(
                    self._execution_leases[invocation.invocation_id]
                ),
            )
            if active.control.token.cancelled or invocation.status == "cancelled":
                result = self._stored_result(invocation)
            else:
                result = self._record_result(invocation, status="completed", output=output)
            return result
        except (TimerSuspension, ChildSuspension):
            if invocation.status == "running":
                self.services.invocations.update(invocation, status="waiting")
            result = self._stored_result(invocation)
            return result
        except InvocationCancelled as exc:
            self._raise_if_execution_interrupted(active)
            result = self._record_result(invocation, status="cancelled", error=exc.as_dict())
            return result
        except Exception as exc:
            error = (
                exc.as_dict()
                if isinstance(exc, OwaError)
                else WorkflowExecutionError("native execution failed").as_dict()
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
                self._abandon_active(invocation)

    async def resume(
        self, handle: ExecutionHandle, resume_input: Any, plan: WorkflowPlan
    ) -> InvocationResult:
        return await super().resume(handle, resume_input, plan)

    async def _resume_owned(
        self, handle: ExecutionHandle, resume_input: Any, plan: WorkflowPlan
    ) -> InvocationResult:
        if not self.native.available:
            return await super()._resume_owned(handle, resume_input, plan)
        if handle.status == "waiting":
            self.services.initial_deliveries.settle(handle)
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
            metadata = self._metadata(plan, handle, active)

            async def execute_workflow(value: Any) -> Any:
                nonlocal execution_completed, execution_output
                execution_output = await self.executor.execute(
                    plan,
                    value,
                    metadata=metadata,
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
                _recovery_context=self._recovery_context(plan, handle, metadata),
                _execution_fence=self.services.execution_ownership.native_fence(
                    self._execution_leases[handle.invocation_id]
                ),
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
        except (TimerSuspension, ChildSuspension):
            if handle.status == "running":
                self.services.invocations.update(handle, status="waiting")
            result = self._stored_result(handle)
            return result
        except InvocationCancelled as exc:
            self._raise_if_execution_interrupted(active)
            result = self._record_result(handle, status="cancelled", error=exc.as_dict())
            return result
        except Exception as exc:
            error = (
                exc.as_dict()
                if isinstance(exc, OwaError)
                else WorkflowExecutionError("native execution failed").as_dict()
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
                self._abandon_active(handle)

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
                _recovery_context=self._recovery_context(
                    plan, handle, {}, require_waiting=handle.status == "waiting"
                ),
            )
        return workflow_input

    def _recovery_context(
        self,
        plan: WorkflowPlan,
        handle: ExecutionHandle,
        metadata: dict[str, Any],
        *,
        require_waiting: bool = False,
    ) -> AdkRecoveryContext:
        return AdkRecoveryContext(
            uses_timing=plan.uses_timing,
            invocation_id=handle.invocation_id,
            workflow_fingerprint=plan.fingerprint,
            task_references=frozenset(
                task.execution_reference or task.reference
                for scope in plan.scopes
                for task in scope.tasks
            ),
            journal=self.services.effect_journal,
            metadata=metadata,
            require_waiting=require_waiting,
            child_waits=self.services.child_waits,
        )

    async def _scheduled_native_state_exists(
        self, handle: ExecutionHandle, plan: WorkflowPlan
    ) -> bool:
        if not self.native.available:
            if ADK_AVAILABLE:
                raise InvocationStateError("native scheduled execution storage is unavailable")
            return False
        return await self.native.scheduled_state_exists(
            self.services.invocations.read_input(handle),
            session_id=handle.session_id,
            user_id=handle.user_id,
            invocation_id=handle.engine_execution_reference,
            database_path=self._session_database_path(),
            _recovery_context=self._recovery_context(plan, handle, {}),
        )


__all__ = ["AdkWorkflowEngine"]
