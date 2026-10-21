"""LangGraph engine adapter."""

import hashlib
import json
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
from open_workflow_agent.native_extensions import LANGGRAPH_CHANNELS
from open_workflow_agent.persistence import ExecutionHandle
from open_workflow_agent.timers import TimerSuspension
from open_workflow_agent.workflow import WorkflowPlan

from . import native as native_module
from .agent import LangGraphAgentFactory
from .native import LANGGRAPH_AVAILABLE, NATIVE_SCOPE_STEP_LIMIT, LangGraphFunctionalAdapter


class LangGraphWorkflowEngine(PortableWorkflowEngine):
    """LangGraph-facing engine boundary.

    The common executor supplies task semantics. Native StateGraph nodes and
    conditional edges execute every derived scope; checkpoints stay private
    to the adapter. Minimal test environments use the common interpreter.
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
        self.native.database_path = services.engine_database_path(
            "langgraph", create_namespace=False
        )

    async def _native_initial_history_exists(self, handle: ExecutionHandle) -> bool:
        key = hashlib.sha256(
            json.dumps(
                [
                    "owa/native-scopes/v1",
                    handle.invocation_id,
                    handle.workflow_fingerprint,
                    "/do",
                    1,
                ]
            ).encode()
        ).hexdigest()
        thread_ids = ("owa-scope-v1:" + key, handle.invocation_id)
        common = self.services.effect_journal.connection
        try:
            if not self.native.database_path:
                saver = self.native.checkpointer
                memory_saver_type = getattr(native_module, "InMemorySaver", None)
                if memory_saver_type is None or not isinstance(saver, memory_saver_type):
                    return False
                # The official get_tuple reader inserts absent defaultdict
                # entries. Presence admission must leave even missing evidence
                # untouched and leave serialization validation to native replay.
                return any(
                    saver.storage.get(thread_id, {}).get("", {}) for thread_id in thread_ids
                ) or any(
                    value and identity[0] in thread_ids and identity[1] == ""
                    for identity, value in saver.writes.items()
                )
            if common.is_postgresql:
                tables = {
                    row[0]
                    for row in common.execute(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema='owa_langgraph'"
                    ).fetchall()
                }
                if not tables:
                    return False
                required = {"checkpoints", "checkpoint_writes"}
                if not required.issubset(tables):
                    raise InvocationStateError("native initial storage schema is incomplete")
                return any(
                    common.execute(
                        f"SELECT 1 FROM owa_langgraph.{table} WHERE thread_id IN (?,?) LIMIT 1",
                        thread_ids,
                    ).fetchone()
                    is not None
                    for table in sorted(required)
                )
            path = Path(self.native.database_path or "")
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
                if not {"checkpoints", "writes"}.issubset(tables):
                    raise InvocationStateError("native initial storage schema is incomplete")
                return any(
                    db.execute(
                        f"SELECT 1 FROM {table} WHERE thread_id IN (?,?) LIMIT 1", thread_ids
                    ).fetchone()
                    is not None
                    for table in ("checkpoints", "writes")
                )
        except InvocationStateError:
            raise
        except Exception:
            raise InvocationStateError("native initial storage could not be inspected") from None

    async def _preflight_native_continuation(self, handle: ExecutionHandle) -> None:
        if LANGGRAPH_AVAILABLE and not self.native.task_nodes_available:
            raise InvocationStateError("native recovery storage is unavailable")
        if self.native.task_nodes_available and not await self._native_initial_history_exists(
            handle
        ):
            raise InvocationStateError(
                "native recovery evidence is missing",
                details={"reason": "native_state_missing", "stage": "native"},
            )

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            engine=self.engine_name,
            resume=True,
            streaming=False,
            native_task_nodes=self.native.task_nodes_available,
            native_scope_step_limit=NATIVE_SCOPE_STEP_LIMIT
            if self.native.task_nodes_available
            else None,
            native_extensions=(LANGGRAPH_CHANNELS,) if self.native.task_nodes_available else (),
        )

    async def invoke(
        self, workflow: WorkflowPlan, invocation: ExecutionHandle, input_data: Any
    ) -> InvocationResult:
        return await super().invoke(workflow, invocation, input_data)

    async def _invoke_owned(
        self, workflow: WorkflowPlan, invocation: ExecutionHandle, input_data: Any
    ) -> InvocationResult:
        if not self.native.task_nodes_available:
            return await super()._invoke_owned(workflow, invocation, input_data)
        active = self._begin_active(invocation)
        result: InvocationResult | None = None
        try:
            input_data = await self._prepare_workflow_input_owned(workflow, invocation, input_data)
            self.services.engine_database_path("langgraph")
            await self.native.prepare_storage()
            output = await self.native.execute_plan(
                self.executor,
                workflow,
                input_data,
                metadata=self._metadata(workflow, invocation, active),
                invocation_id=invocation.invocation_id,
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
        workflow_input = self.services.invocations.read_input(handle)
        if not self.native.task_nodes_available:
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
            self.services.engine_database_path("langgraph")
            await self.native.prepare_storage()
            output = await self.native.execute_plan(
                self.executor,
                plan,
                workflow_input,
                metadata=self._metadata(plan, handle, active),
                invocation_id=handle.invocation_id,
                resume=True,
                _execution_fence=self.services.execution_ownership.native_fence(
                    self._execution_leases[handle.invocation_id]
                ),
            )
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
        if LANGGRAPH_AVAILABLE and not self.native.task_nodes_available:
            raise InvocationStateError(
                "native execution state is unavailable; recovery is required"
            )
        if self.native.task_nodes_available:
            await self.native.admit_resume(
                plan,
                handle.invocation_id,
                workflow_input,
                effect_journal=self.services.effect_journal,
                child_waits=self.services.child_waits,
            )
        return workflow_input

    async def _scheduled_native_state_exists(
        self, handle: ExecutionHandle, plan: WorkflowPlan
    ) -> bool:
        if not self.native.task_nodes_available:
            if LANGGRAPH_AVAILABLE:
                raise InvocationStateError("native scheduled execution storage is unavailable")
            return False
        return await self.native.scheduled_state_exists(
            plan,
            handle.invocation_id,
            self.services.invocations.read_input(handle),
            self.services.effect_journal,
            self.services.child_waits,
        )


__all__ = ["LangGraphWorkflowEngine"]
