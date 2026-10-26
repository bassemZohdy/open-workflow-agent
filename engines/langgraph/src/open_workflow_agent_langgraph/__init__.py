"""LangGraph engine adapter."""

import asyncio
import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from open_workflow_agent._native_sqlite_inspection import inspect_native_sqlite
from open_workflow_agent.child_suspension import ChildSuspension
from open_workflow_agent.engine import EngineCapabilities, InvocationResult, PortableWorkflowEngine
from open_workflow_agent.errors import (
    ConfigurationError,
    InvocationCancelled,
    InvocationStateError,
    OwaError,
    UnsupportedWorkflowFeature,
    WorkflowExecutionError,
)
from open_workflow_agent.native_extensions import (
    LANGGRAPH_CHANNELS,
    LANGGRAPH_COMMAND,
    LANGGRAPH_SEND,
    LANGGRAPH_STREAMS,
    MAX_NATIVE_SEND_BRANCHES,
)
from open_workflow_agent.native_memory_contract import CAPABILITY as MEMORY_CAPABILITY
from open_workflow_agent.native_memory_contract import (
    SEMANTIC_CAPABILITY as MEMORY_SEMANTIC_CAPABILITY,
)
from open_workflow_agent.native_memory_contract import references as memory_references
from open_workflow_agent.persistence import ExecutionHandle
from open_workflow_agent.storage import is_postgresql_datasource
from open_workflow_agent.timers import TimerSuspension
from open_workflow_agent.workflow import (
    SUPPORTED_FUNCTION_CALLS,
    WorkflowPlan,
    _NativeMemoryRecoveryRequired,
)

from . import native as native_module
from .agent import LangGraphAgentFactory
from .native import LANGGRAPH_AVAILABLE, NATIVE_SCOPE_STEP_LIMIT, LangGraphFunctionalAdapter

if TYPE_CHECKING:
    from .native_memory_catalog import NativeMemoryCatalog


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
        self.native_memory: NativeMemoryCatalog | None = None

    async def initialize(self, services: Any) -> None:
        if services.config.observability.native_streams is not None and (
            not self.native.task_nodes_available
            or not services.config.server.api_security_profile
            or not services.config.execution_authorization.enabled
        ):
            raise ConfigurationError(
                "native streams require native LangGraph, authenticated API ingress "
                "and original execution authorization"
            )
        database_path = services.engine_database_path("langgraph", create_namespace=False)
        proactor_loop = getattr(asyncio, "ProactorEventLoop", None)
        if (
            native_module.LANGGRAPH_AVAILABLE
            and getattr(native_module, "AsyncPostgresSaver", None) is not None
            and is_postgresql_datasource(database_path)
            and proactor_loop is not None
            and isinstance(asyncio.get_running_loop(), proactor_loop)
        ):
            raise ConfigurationError(
                "LangGraph PostgreSQL persistence requires a selector event loop on Windows",
                details={"reason": "unsupported_event_loop"},
            )
        if services.config.memory.native and (
            not self.native.task_nodes_available
            or not services.config.execution_authorization.enabled
        ):
            raise ConfigurationError(
                "native memory bindings require native LangGraph "
                "and original execution authorization"
            )
        self.native.database_path = database_path
        if services.config.memory.native:
            from .native_memory_catalog import NativeMemoryCatalog

            installed = NativeMemoryCatalog(services)
            await installed.open()
            self.native_memory = installed
        try:
            await super().initialize(services)
            factory = LangGraphAgentFactory()
            self.agent = factory.create(
                services.config.agent,
                services.config.model,
                factory.bind_tools(services.agent_tool_bindings()),
            )
        except BaseException:
            try:
                await super().shutdown()
            finally:
                if self.native_memory is not None:
                    await self.native_memory.close()
                    self.native_memory = None
            raise

    async def shutdown(self) -> None:
        try:
            await super().shutdown()
        finally:
            if self.native_memory is not None:
                await self.native_memory.close()
                self.native_memory = None

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

    def _verify_workflow_capabilities(self, plan: WorkflowPlan) -> None:
        super()._verify_workflow_capabilities(plan)
        if not any(
            item.required and item.capability_id == LANGGRAPH_SEND
            for item in plan.native_extensions
        ):
            return
        for scope in plan.scopes:
            for task in scope.tasks:
                fork = task.definition.get("fork")
                if fork is None:
                    continue
                if (
                    not isinstance(fork, dict)
                    or fork.get("compete", False)
                    or not 1 <= len(fork.get("branches", [])) <= MAX_NATIVE_SEND_BRANCHES
                ):
                    raise UnsupportedWorkflowFeature(
                        "native Send requires non-competing forks of at most 64 branches"
                    )

    def capabilities(self) -> EngineCapabilities:
        extensions: tuple[str, ...] = (
            (LANGGRAPH_CHANNELS, LANGGRAPH_COMMAND, LANGGRAPH_SEND)
            if self.native.task_nodes_available
            else ()
        )
        if self.native_memory is not None:
            self.native_memory.verify()
            extensions += (MEMORY_CAPABILITY,)
            if any(binding.semantic is not None for binding in self.native_memory.bindings):
                extensions += (MEMORY_SEMANTIC_CAPABILITY,)
        services = getattr(self, "services", None)
        streams = False
        if services is not None and services.config.observability.native_streams is not None:
            if (
                services.native_streams is None
                or not services.config.server.api_security_profile
                or not services.config.execution_authorization.enabled
            ):
                raise ConfigurationError("native stream installation requires original authority")
            services.native_streams.verify(services.config.observability.native_streams)
            streams = self.native.task_nodes_available
            if streams:
                extensions += (LANGGRAPH_STREAMS,)
        functions = SUPPORTED_FUNCTION_CALLS + (
            tuple(sorted(memory_references(self.native_memory.bindings)))
            if self.native_memory is not None
            else ()
        )
        return EngineCapabilities(
            engine=self.engine_name,
            functions=functions,
            resume=True,
            streaming=streams,
            native_task_nodes=self.native.task_nodes_available,
            native_scope_step_limit=NATIVE_SCOPE_STEP_LIMIT
            if self.native.task_nodes_available
            else None,
            native_extensions=extensions,
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
                _stream_observer=(
                    self.services.native_streams.begin(invocation, workflow.native_extensions)
                    if self.services.native_streams is not None
                    else None
                ),
                _execution_fence=self.services.execution_ownership.native_fence(
                    self._execution_leases[invocation.invocation_id]
                ),
            )
            if active.control.token.cancelled or invocation.status == "cancelled":
                result = self._stored_result(invocation)
            else:
                result = self._record_result(invocation, status="completed", output=output)
            return result
        except (TimerSuspension, ChildSuspension, _NativeMemoryRecoveryRequired):
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
            if self.services.native_streams is not None:
                self.services.native_streams.end(
                    invocation, result.status if result is not None else "interrupted"
                )
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
                _stream_observer=(
                    self.services.native_streams.begin(handle, plan.native_extensions)
                    if self.services.native_streams is not None
                    else None
                ),
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
        except (TimerSuspension, ChildSuspension, _NativeMemoryRecoveryRequired):
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
            if self.services.native_streams is not None:
                self.services.native_streams.end(
                    handle, result.status if result is not None else "interrupted"
                )
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
