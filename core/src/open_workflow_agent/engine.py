"""Engine SPI and the deterministic portable execution implementation."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Literal

from ._version import __version__
from .child_suspension import ChildSuspension
from .child_waits import ChildWaitRegistration
from .effects import EffectIdentity
from .errors import (
    InvocationCancelled,
    InvocationStateConflict,
    InvocationStateError,
    OwaError,
    UnsupportedWorkflowFeature,
    WorkflowExecutionError,
)
from .lifecycle import (
    INVOCATION_STATES,
    TERMINAL_INVOCATION_STATES,
    ActiveInvocation,
    LifecycleControl,
)
from .observability import WorkflowEvent
from .ownership import ExecutionLease
from .persistence import ExecutionHandle
from .sandbox import SandboxWorkflowExecutor
from .services import RuntimeServices
from .timer_dispatcher import TimerDispatcher
from .timers import _CURRENT_TIMER_CLAIM, TimerClaim, TimerSuspension, TimerWake
from .timing import clock_started_at, root_clock_identity
from .workflow import (
    SUPPORTED_FUNCTION_CALLS,
    SUPPORTED_PROTOCOL_CALLS,
    SUPPORTED_TASKS,
    WorkflowPlan,
)
from .workflow_profile import WORKFLOW_DSL, workflow_profile

_PARENT_LIFECYCLE: ContextVar[LifecycleControl | None] = ContextVar(
    "owa_parent_lifecycle", default=None
)


@dataclass(frozen=True, slots=True)
class EngineCapabilities:
    engine: str
    portable_profile: str = "1"
    tasks: tuple[str, ...] = SUPPORTED_TASKS
    protocols: tuple[str, ...] = SUPPORTED_PROTOCOL_CALLS
    functions: tuple[str, ...] = SUPPORTED_FUNCTION_CALLS
    policies: tuple[str, ...] = ("retry", "timeout")
    resume: bool = True
    streaming: bool = False
    cancellation: bool = True
    waiting: bool = True
    native_task_nodes: bool = False
    native_scope_step_limit: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "runtime": "open-workflow-agent",
            "runtimeVersion": __version__,
            "engine": self.engine,
            "workflowDsl": WORKFLOW_DSL,
            "workflowProfile": workflow_profile(),
            "portableProfile": self.portable_profile,
            "tasks": list(self.tasks),
            "protocols": list(self.protocols),
            "functions": list(self.functions),
            "policies": list(self.policies),
            "features": {
                "nativeTaskExecution": {
                    "nodes": self.native_task_nodes,
                    "scopeStepLimit": self.native_scope_step_limit,
                },
                "resume": self.resume,
                "streaming": self.streaming,
                "cancellation": self.cancellation,
                "waiting": self.waiting,
                "events": {"emit": True, "listen": True, "durable": False},
                "cloudEvents": {
                    "lifecycle": True,
                    "specversion": "1.0",
                    "delivery": "bounded_snapshot",
                    "durable": False,
                },
                "scheduling": {
                    "after": True,
                    "every": True,
                    "cron": True,
                    "on": False,
                    "durable": True,
                    "owner": "fenced_workers",
                },
                "subWorkflows": {
                    "run": True,
                    "separateInvocation": True,
                    "localCatalog": True,
                    "externalCatalog": False,
                },
            },
        }


@dataclass(slots=True)
class InvocationResult:
    invocation_id: str
    session_id: str
    status: str
    output: Any = None
    error: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "invocation_id": self.invocation_id,
            "session_id": self.session_id,
            "status": self.status,
            "output": self.output,
        }
        if self.error:
            value["error"] = self.error
        return value


class WorkflowEngine:
    engine_name = "base"
    _execution_ownership_seconds: float = 30

    def __init__(self) -> None:
        self._active: dict[str, ActiveInvocation] = {}
        self._restart_resume_inputs: dict[str, Any] = {}
        self._execution_leases: dict[str, ExecutionLease] = {}
        self._ownership_tasks: dict[str, asyncio.Task[Any]] = {}
        self._timer_dispatcher: TimerDispatcher | None = None
        self._timer_claims: dict[str, TimerClaim] = {}
        self._timer_wakes: dict[str, dict[str, TimerWake]] = {}
        self._child_wakes: dict[str, dict[str, dict[str, Any]]] = {}
        self._ownership_released: dict[str, asyncio.Event] = {}

    async def _wait_for_ownership_renewal(self) -> None:
        await asyncio.sleep(self._execution_ownership_seconds / 3)

    @asynccontextmanager
    async def _own_execution(self, handle: ExecutionHandle) -> AsyncIterator[None]:
        store = self.services.execution_ownership
        lease = store.acquire(handle, seconds=self._execution_ownership_seconds)
        released = asyncio.Event()
        self._ownership_released[handle.invocation_id] = released
        self._execution_leases[handle.invocation_id] = lease
        handle._execution_lease = lease
        owner_task = asyncio.current_task()
        if owner_task is not None:
            self._ownership_tasks[handle.invocation_id] = owner_task
        lost: OwaError | None = None

        async def renew() -> None:
            nonlocal lease, lost
            try:
                while True:
                    await self._wait_for_ownership_renewal()
                    current = self.services.invocations.get(handle.invocation_id)
                    if current is not None and current.status == "cancelled":
                        # A different worker's authorized control write remains
                        # authoritative while this worker owns a suspended await.
                        self.services.invocations.refresh(handle, terminal_only=True)
                        active = self._active.get(handle.invocation_id)
                        if active is not None:
                            active.control.token.cancel("cancelled")
                        return
                    lease = store.renew(lease, seconds=self._execution_ownership_seconds)
                    self._execution_leases[handle.invocation_id] = lease
                    handle._execution_lease = lease
            except Exception as error:
                lost = (
                    error
                    if isinstance(error, OwaError)
                    else InvocationStateError("execution ownership renewal failed")
                )
                active = self._active.get(handle.invocation_id)
                if active is not None:
                    active.control.interrupt("ownership_lost")
                if owner_task is not None:
                    owner_task.cancel()

        heartbeat = asyncio.create_task(renew())
        try:
            yield
            current = self.services.invocations.get(handle.invocation_id)
            if current is not None and current.status == "cancelled":
                # Authoritative cancellation atomically revokes execution. A
                # read-only return of that first terminal outcome needs no new
                # execution authority; nonterminal ownership loss still fails.
                self.services.invocations.refresh(handle, terminal_only=True)
            else:
                store.verify(lease)
        except asyncio.CancelledError:
            if lost is not None:
                raise InvocationStateConflict(
                    "invocation execution ownership was lost; recovery is required"
                ) from None
            raise
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            self._execution_leases.pop(handle.invocation_id, None)
            self._ownership_tasks.pop(handle.invocation_id, None)
            handle._execution_lease = None
            try:
                store.release(lease)
            except OwaError:
                # Expiry, another owner or unavailable storage cannot justify a
                # fresh state write. A failed release expires naturally.
                pass
            finally:
                self._ownership_released.pop(handle.invocation_id, None)
                released.set()

    async def initialize(self, services: RuntimeServices) -> None:
        self.services = services
        services.workflow_runner = self.run_child

    async def run_child(
        self,
        workflow: WorkflowPlan,
        input_data: Any,
        parent_context: dict[str, Any],
        task_reference: str,
    ) -> Any:
        parent_handle = parent_context.get("_invocation_handle")
        if not isinstance(parent_handle, ExecutionHandle):
            raise WorkflowExecutionError("nested workflow requires an invocation handle")
        child = parent_context.get("_child_invocation_handle")
        if not isinstance(child, ExecutionHandle):
            raise WorkflowExecutionError("nested workflow requires an admitted child binding")
        observer = parent_context.get("_child_invocation_observer")
        if observer is not None:
            observer(child)
        parent_control = parent_context.get("_lifecycle")
        context_token = _PARENT_LIFECYCLE.set(
            parent_control if isinstance(parent_control, LifecycleControl) else None
        )
        try:
            # Task cancellation also transports host drain and ownership loss.
            # Business cancellation is an explicit persisted parent/scope control
            # operation; interruption alone cannot authorize a child state write.
            if child.status in TERMINAL_INVOCATION_STATES:
                result = self._stored_result(child)
            else:
                result = await self.invoke(workflow, child, input_data)
        finally:
            _PARENT_LIFECYCLE.reset(context_token)
        if result.status == "completed":
            return result.output
        if result.status == "waiting":
            identity = parent_context.get("_child_effect_identity")
            if not isinstance(identity, EffectIdentity):
                raise WorkflowExecutionError("child wait requires its parent action identity")
            raise ChildSuspension(
                identity,
                child.invocation_id,
                parent_context["_child_request_digest"],
                deadline_at=parent_context.get("_child_deadline_at"),
            )
        if result.status == "cancelled":
            raise InvocationCancelled(
                "nested workflow was cancelled",
                details={"invocation_id": child.invocation_id},
            )
        raise WorkflowExecutionError(
            "nested workflow faulted",
            details={"invocation_id": child.invocation_id, "error": result.error or {}},
        )

    async def compile(self, plan: WorkflowPlan) -> WorkflowPlan:
        self._verify_event_requirements(plan)
        return plan

    def _verify_event_requirements(self, plan: WorkflowPlan) -> None:
        services = getattr(self, "services", None)
        if services is None:
            # Plans may be compiled before deployment services are bound;
            # invocation admission repeats this check before any dispatch.
            return
        bus = services.event_bus
        if getattr(bus, "durable", False) and hasattr(bus, "register_workflow_listener"):
            return
        for scope in plan.scopes:
            for task in scope.tasks:
                listener = task.definition.get("listen")
                if not isinstance(listener, dict):
                    continue
                strategy = listener["to"]
                selected = strategy.get("one")
                if set(strategy) != {"one"} or (
                    isinstance(selected, dict) and selected.get("correlate")
                ):
                    raise UnsupportedWorkflowFeature(
                        "advanced event consumption requires the embedded durable event backend",
                        details={"reference": task.reference},
                    )

    def _verify_execution_engine(self, handle: ExecutionHandle) -> None:
        # Preserve stored common-reference identities across recovery.
        reference_alias = self.engine_name == "base" and handle.engine in {"portable", "reference"}
        if handle.engine != self.engine_name and not reference_alias:
            raise InvocationStateError("invocation belongs to a different engine")

    async def invoke(
        self, workflow: WorkflowPlan, invocation: ExecutionHandle, input_data: Any
    ) -> InvocationResult:
        self.services.invocations.verify_fingerprint(invocation, workflow.fingerprint)
        self._verify_execution_engine(invocation)
        self.services.invocations.refresh(invocation)
        if invocation.status in TERMINAL_INVOCATION_STATES:
            return self._stored_result(invocation)
        active = self._active.get(invocation.invocation_id)
        if active is not None:
            return await asyncio.shield(active.result)
        try:
            async with self._own_execution(invocation):
                self.services.invocations.refresh(invocation)
                if invocation.status in TERMINAL_INVOCATION_STATES:
                    return self._stored_result(invocation)
                if self.services.child_waits.for_invocation(
                    invocation.invocation_id, workflow.fingerprint
                ):
                    await self._verify_resume_state(invocation, workflow)
                    if not await self._prepare_child_wakes(invocation, workflow):
                        return self._stored_result(invocation)
                    return await self._resume_owned(invocation, None, workflow)
                return await self._invoke_owned(workflow, invocation, input_data)
        except InvocationStateConflict as error:
            try:
                self.services.invocations.refresh(invocation, terminal_only=True)
            except InvocationStateConflict:
                raise error from None
            return self._stored_result(invocation)

    async def _invoke_owned(
        self, workflow: WorkflowPlan, invocation: ExecutionHandle, input_data: Any
    ) -> InvocationResult:
        raise NotImplementedError

    async def _scheduled_native_state_exists(
        self, handle: ExecutionHandle, plan: WorkflowPlan
    ) -> bool:
        return False

    async def dispatch_scheduled(
        self, handle: ExecutionHandle, plan: WorkflowPlan, input_data: Any
    ) -> InvocationResult:
        """Reconcile a bound schedule handle without guessing SDK initialization."""
        self.services.invocations.verify_fingerprint(handle, plan.fingerprint)
        self._verify_execution_engine(handle)
        triggers = self.services.event_triggers(self.engine_name)
        if triggers is not None:
            triggers.verify_invocation(handle.invocation_id)
        self.services.invocations.refresh(handle)
        if handle.status in TERMINAL_INVOCATION_STATES:
            await self._cancel_synchronous_children(handle)
            return self._stored_result(handle)
        active = self._active.get(handle.invocation_id)
        if active is not None:
            return await asyncio.shield(active.result)
        async with self._own_execution(handle):
            self.services.invocations.refresh(handle)
            if handle.status in TERMINAL_INVOCATION_STATES:
                return self._stored_result(handle)
            if await self._scheduled_native_state_exists(handle, plan):
                await self._verify_resume_state(handle, plan)
                if not await self._prepare_child_wakes(handle, plan):
                    return self._stored_result(handle)
                self._prepare_timer_wake(handle, plan, None)
                return await self._resume_owned(handle, None, plan)
            return await self._invoke_owned(plan, handle, input_data)

    async def dispatch_child(
        self, handle: ExecutionHandle, load_plan: Callable[[], Awaitable[WorkflowPlan]]
    ) -> InvocationResult:
        """Acquire child authority before dependency resolution or native reentry."""
        self._verify_execution_engine(handle)
        async with self._own_execution(handle):
            self.services.invocations.refresh(handle)
            if handle.status in TERMINAL_INVOCATION_STATES:
                return self._stored_result(handle)
            plan = await load_plan()
            self.services.invocations.verify_fingerprint(handle, plan.fingerprint)
            if self.services.timers.resumable_for_invocation(
                handle.invocation_id, plan.fingerprint
            ) or self.services.child_waits.for_invocation(handle.invocation_id, plan.fingerprint):
                return self._stored_result(handle)
            if await self._scheduled_native_state_exists(handle, plan):
                await self._verify_resume_state(handle, plan)
                self._prepare_timer_wake(handle, plan, None)
                return await self._resume_owned(handle, None, plan)
            return await self._invoke_owned(
                plan, handle, self.services.invocations.read_input(handle)
            )

    async def dispatch_child_wait(
        self,
        registration: ChildWaitRegistration,
        load_plan: Callable[[], Awaitable[WorkflowPlan]],
    ) -> InvocationResult:
        handle = self.services.invocations.get(registration.identity.invocation_id)
        if handle is None:
            raise InvocationStateError("child wait parent is unavailable")
        self._verify_execution_engine(handle)
        async with self._own_execution(handle):
            self.services.invocations.refresh(handle)
            if handle.status in TERMINAL_INVOCATION_STATES:
                await self._cancel_synchronous_children(handle)
                return self._stored_result(handle)
            plan = await load_plan()
            self.services.invocations.verify_fingerprint(handle, plan.fingerprint)
            if await self._scheduled_native_state_exists(handle, plan):
                await self._verify_resume_state(handle, plan)
                if not await self._prepare_child_wakes(handle, plan):
                    return self._stored_result(handle)
                return await self._resume_owned(handle, None, plan)
            # A process can stop after common admission but before native waiting
            # persistence. Reentry still resolves the recorded internal child.
            if not await self._prepare_child_wakes(handle, plan):
                return self._stored_result(handle)
            await self._verify_resume_state(handle, plan)
            return await self._resume_owned(handle, None, plan)

    async def _prepare_child_wakes(self, handle: ExecutionHandle, plan: WorkflowPlan) -> bool:
        registrations = self.services.child_waits.for_invocation(
            handle.invocation_id, plan.fingerprint
        )
        executor = getattr(self, "executor", None)
        now = executor._absolute_clock() if executor is not None else time.time()
        ready = not registrations
        for registration in registrations:
            wake = self.services.child_waits.wake(registration, handle, now=now)
            if wake is not None:
                ready = True
                if wake["reason"] == "deadline":
                    child = self.services.invocations.get(registration.suspension.child_id)
                    if child is not None:
                        await self._cancel_child_for_control(handle, child, reason="deadline")
        self._child_wakes[handle.invocation_id] = self.services.child_waits.known_wakes(
            handle.invocation_id, plan.fingerprint
        )
        return ready

    async def _dispatch_timer(
        self, claim: TimerClaim, load_plan: Callable[[], Awaitable[WorkflowPlan]]
    ) -> InvocationResult | None:
        handle = self.services.invocations.get(claim.registration.identity.invocation_id)
        if handle is None:
            return None
        self._verify_execution_engine(handle)
        self.services.invocations.verify_fingerprint(
            handle, claim.registration.identity.workflow_fingerprint
        )
        async with self._own_execution(handle):
            self.services.invocations.refresh(handle)
            if handle.status in TERMINAL_INVOCATION_STATES:
                return self._stored_result(handle)
            # Resolve dependencies only after acquiring invocation ownership.
            # A competing manual/cross-process resume performs no duplicate I/O.
            now = (
                self._timer_dispatcher.clock()
                if self._timer_dispatcher is not None
                else time.time()
            )
            self.services.timers.verify_claim(claim, now=now)
            plan = await load_plan()
            self.services.invocations.verify_fingerprint(handle, plan.fingerprint)
            await self._verify_resume_state(handle, plan)
            await self._prepare_child_wakes(handle, plan)
            self._prepare_timer_wake(handle, plan, None)
            return await self._resume_owned(handle, None, plan)

    async def resume(
        self, handle: ExecutionHandle, resume_input: Any, plan: WorkflowPlan
    ) -> InvocationResult:
        self.services.invocations.verify_fingerprint(handle, plan.fingerprint)
        self._verify_execution_engine(handle)
        active_result = await self._resume_active_or_terminal(handle, resume_input)
        if active_result is not None:
            return active_result
        released = self._ownership_released.get(handle.invocation_id)
        if (
            released is not None
            and self._ownership_tasks.get(handle.invocation_id) is not asyncio.current_task()
        ):
            await released.wait()
        try:
            async with self._own_execution(handle):
                self.services.invocations.refresh(handle)
                if handle.status in TERMINAL_INVOCATION_STATES:
                    return self._stored_result(handle)
                await self._verify_resume_state(handle, plan)
                if not await self._prepare_child_wakes(handle, plan):
                    if not self.services.timers.resumable_for_invocation(
                        handle.invocation_id, plan.fingerprint
                    ):
                        return self._stored_result(handle)
                self._prepare_timer_wake(handle, plan, resume_input)
                return await self._resume_owned(handle, resume_input, plan)
        except InvocationStateConflict as error:
            try:
                self.services.invocations.refresh(handle, terminal_only=True)
            except InvocationStateConflict:
                raise error from None
            return self._stored_result(handle)

    async def _resume_owned(
        self, handle: ExecutionHandle, resume_input: Any, plan: WorkflowPlan
    ) -> InvocationResult:
        if handle.status == "waiting":
            self._restart_resume_inputs[handle.invocation_id] = resume_input
            self.services.invocations.update(handle, status="running")
        if handle.status != "running":
            return self._stored_result(handle)
        return await self._invoke_owned(plan, handle, resume_input)

    def _prepare_timer_wake(
        self, handle: ExecutionHandle, plan: WorkflowPlan, resume_input: Any
    ) -> None:
        store = self.services.timers
        executor = getattr(self, "executor", None)
        now = executor._absolute_clock() if executor is not None else time.time()
        store.verify_invocation(handle.invocation_id, plan.fingerprint)
        claim = _CURRENT_TIMER_CLAIM.get()
        if claim is not None and claim.registration.identity.invocation_id != handle.invocation_id:
            claim = None
        if claim is not None:
            store.verify_claim(claim, now=now)
            store.wake(
                claim.registration, "timer_due", now=now, execution_lease=handle._execution_lease
            )
        elif handle.status == "waiting":
            for registration in store.resumable_for_invocation(
                handle.invocation_id, plan.fingerprint
            ):
                store.wake(
                    registration,
                    "resume_requested",
                    resume_input,
                    now=now,
                    execution_lease=handle._execution_lease,
                )
        self._timer_wakes[handle.invocation_id] = store.known_wakes(
            handle.invocation_id, plan.fingerprint
        )

    async def cancel(
        self, handle: ExecutionHandle, *, operation_id: str | None = None
    ) -> InvocationResult:
        self.services.invocations.refresh(handle)
        if handle.status in TERMINAL_INVOCATION_STATES:
            await self._cancel_synchronous_children(handle)
            return self._stored_result(handle)
        active = self._active.get(handle.invocation_id)
        error = InvocationCancelled(
            "invocation cancellation requested",
            details={"operation_id": operation_id} if operation_id else {},
        )
        owned_handle = active.handle if active is not None and active.handle is not None else handle
        if owned_handle is not handle:
            self.services.invocations.refresh(owned_handle)
        if owned_handle.status in TERMINAL_INVOCATION_STATES:
            self.services.invocations.refresh(handle)
            return self._stored_result(handle)
        # An authorized operator cancellation is a control write, not another
        # execution by the active worker. Keep the worker's private token on its
        # handle while committing through a freshly loaded control handle.
        control_handle = self.services.invocations.get(handle.invocation_id)
        if control_handle is None:
            raise InvocationStateError("invocation state is unavailable")
        if control_handle.status in TERMINAL_INVOCATION_STATES:
            self.services.invocations.refresh(owned_handle)
            self.services.invocations.refresh(handle)
            return self._stored_result(control_handle)
        try:
            self.services.invocations.cancel_authoritatively(control_handle, error=error.as_dict())
        except InvocationStateConflict:
            self.services.invocations.refresh(control_handle, terminal_only=True)
            self.services.invocations.refresh(owned_handle)
            self.services.invocations.refresh(handle)
            return self._stored_result(control_handle)
        self.services.invocations.refresh(owned_handle)
        if owned_handle is not handle:
            self.services.invocations.refresh(handle)
        if active is not None:
            active.control.token.cancel("cancelled")
        if self.services.event_store is not None:
            self.services.event_store.close_invocation(
                handle.invocation_id, handle.workflow_fingerprint
            )
        self.services.timers.close_invocation(handle.invocation_id, handle.workflow_fingerprint)
        await self._cancel_synchronous_children(handle)
        if active is None:
            self.services.lifecycle_events.emit(
                WorkflowEvent(
                    event_type="WorkflowCancelled",
                    invocation_id=handle.invocation_id,
                    session_id=handle.session_id,
                    workflow_name=handle.workflow_name,
                    workflow_version=handle.workflow_version,
                    engine=handle.engine,
                    operation_id=operation_id,
                    status="cancelled",
                    reason="cancelled",
                    error=error.as_dict(),
                )
            )
        return self._stored_result(handle)

    async def _cancel_synchronous_children(self, handle: ExecutionHandle) -> None:
        rows = self.services.children.connection.execute(
            "SELECT binding.binding_key,binding.root_id,binding.parent_id,binding.child_id,"
            "binding.depth,binding.request_digest,binding.source_json,binding.digest,"
            "binding.parent_task_reference FROM child_workflow_bindings AS binding "
            "LEFT JOIN detached_child_jobs AS jobs ON jobs.binding_key=binding.binding_key "
            "WHERE binding.parent_id=? AND jobs.job_key IS NULL",
            (handle.invocation_id,),
        ).fetchall()
        errors: list[OwaError] = []
        for row in rows:
            try:
                binding = self.services.children._binding(row)
                child = self.services.children.verify_control(binding, handle)
                if (
                    child is None
                    or child.parent_invocation_id != handle.invocation_id
                    or child.owner_principal != handle.owner_principal
                    or child.user_id != handle.user_id
                    or child.engine != handle.engine
                    or child.parent_task_reference != binding.parent_task_reference
                ):
                    raise WorkflowExecutionError("child cancellation identity is inconsistent")
                if child.status not in TERMINAL_INVOCATION_STATES:
                    await self.cancel(
                        child, operation_id=f"{handle.invocation_id}:child-cancellation"
                    )
            except OwaError as error:
                errors.append(error)
        if errors:
            raise errors[0]

    async def _cancel_child_scopes(
        self,
        handle: ExecutionHandle,
        prefixes: tuple[str, ...],
        occurrence: tuple[tuple[str, int], ...],
        *,
        reason: Literal["fork_loser", "workflow_end", "deadline"] = "fork_loser",
    ) -> None:
        roots = tuple(prefix.rstrip("/") for prefix in prefixes)
        lease = self._execution_leases.get(handle.invocation_id)
        if lease is None or handle._execution_lease != lease:
            raise InvocationStateConflict(
                "child scope cancellation requires current parent authority"
            )
        self.services.execution_ownership.verify(lease)
        rows = self.services.children.connection.execute(
            "SELECT b.binding_key,b.root_id,b.parent_id,b.child_id,b.depth,b.request_digest,"
            "b.source_json,b.digest,b.parent_task_reference,i.payload "
            "FROM child_workflow_bindings b "
            "LEFT JOIN workflow_effect_invocations i ON i.identity=b.binding_key "
            "LEFT JOIN detached_child_jobs j ON j.binding_key=b.binding_key "
            "WHERE b.parent_id=? AND j.job_key IS NULL",
            (handle.invocation_id,),
        ).fetchall()
        for row in rows:
            binding = self.services.children._binding(row[:9])
            if row[9] is None:
                if any(
                    binding.parent_task_reference == root
                    or binding.parent_task_reference.startswith(root + "/")
                    for root in roots
                ):
                    raise WorkflowExecutionError("child scope action identity is absent")
                continue
            try:
                identity = EffectIdentity.from_value(json.loads(row[9]))
            except (TypeError, ValueError, KeyError):
                raise WorkflowExecutionError(
                    "child scope action identity is inconsistent"
                ) from None
            if (
                identity.operation != "run:workflow"
                or identity.key != binding.key
                or identity.invocation_id != handle.invocation_id
                or identity.workflow_fingerprint != handle.workflow_fingerprint
            ):
                raise WorkflowExecutionError("child scope action identity is inconsistent")
            if (
                not any(
                    identity.task_reference == root
                    or identity.task_reference.startswith(root + "/")
                    for root in roots
                )
                or identity.occurrence[: len(occurrence)] != occurrence
            ):
                continue
            child = self.services.children.verify_control(binding, handle)
            await self._cancel_child_for_control(handle, child, reason=reason)
            registration = self.services.child_waits.registration(identity)
            if registration is not None:
                executor = getattr(self, "executor", None)
                self.services.child_waits.wake(
                    registration,
                    handle,
                    now=executor._absolute_clock() if executor else time.time(),
                )
                self.services.child_waits.consume(registration, handle)

    async def _cancel_child_for_control(
        self,
        parent: ExecutionHandle,
        child: ExecutionHandle,
        *,
        reason: Literal["fork_loser", "workflow_end", "deadline"],
    ) -> None:
        """Persist an internal scope decision under live parent authority."""
        lease = self._execution_leases.get(parent.invocation_id)
        if lease is None or parent._execution_lease != lease:
            raise InvocationStateConflict("child control requires current parent authority")
        row = self.services.children.connection.execute(
            "SELECT binding_key,root_id,parent_id,child_id,depth,request_digest,source_json,"
            "digest,parent_task_reference FROM child_workflow_bindings WHERE child_id=?",
            (child.invocation_id,),
        ).fetchone()
        binding = self.services.children._binding(row)
        current = self.services.children.verify_control(binding, parent)
        operation_id = (
            f"{parent.invocation_id}:"
            + {
                "fork_loser": "fork-loser",
                "workflow_end": "workflow-end",
                "deadline": "child-deadline",
            }[reason]
        )
        error = InvocationCancelled(
            "child scope cancellation requested",
            details={"reason": reason, "operation_id": operation_id},
        )
        executor = getattr(self, "executor", None)
        controlled = self.services.invocations.cancel_under_parent_control(
            current,
            parent,
            execution_lease=lease,
            error=error.as_dict(),
            completed_at=executor._absolute_clock() if executor is not None else time.time(),
        )
        if controlled.status != "cancelled":
            # A different worker's first terminal result remains authoritative.
            return
        active = self._active.get(controlled.invocation_id)
        if active is not None:
            if active.handle is not None:
                self.services.invocations.refresh(active.handle, terminal_only=True)
            active.control.token.cancel(reason)
        self.services.invocations.refresh(child, terminal_only=True)
        if self.services.event_store is not None:
            self.services.event_store.close_invocation(
                controlled.invocation_id, controlled.workflow_fingerprint
            )
        self.services.timers.close_invocation(
            controlled.invocation_id, controlled.workflow_fingerprint
        )
        # The recorded child cancellation supplies durable authority to finish
        # its synchronous descendants even if this host subsequently drains.
        await self._cancel_synchronous_children(controlled)
        if active is None:
            self.services.lifecycle_events.emit(
                WorkflowEvent(
                    event_type="WorkflowCancelled",
                    invocation_id=controlled.invocation_id,
                    session_id=controlled.session_id,
                    workflow_name=controlled.workflow_name,
                    workflow_version=controlled.workflow_version,
                    engine=controlled.engine,
                    operation_id=operation_id,
                    status="cancelled",
                    reason=reason,
                    error=error.as_dict(),
                )
            )

    def _raise_if_execution_interrupted(self, active: ActiveInvocation) -> None:
        """Do not turn a callback's translated host interruption into control."""
        if active.control.interruption_reason not in {"host_drain", "ownership_lost"}:
            return
        handle = active.handle
        if handle is not None:
            current = self.services.invocations.get(handle.invocation_id)
            if current is not None and current.status == "cancelled":
                # An independently authorized cancellation still wins when
                # its worker is also draining or losing execution ownership.
                self.services.invocations.refresh(handle, terminal_only=True)
                return
        raise asyncio.CancelledError

    def _begin_active(self, handle: ExecutionHandle) -> ActiveInvocation:
        existing = self._active.get(handle.invocation_id)
        if existing is not None:
            return existing
        active = ActiveInvocation(
            control=LifecycleControl(parent_interruption=_PARENT_LIFECYCLE.get()),
            task=asyncio.current_task(),
            result=asyncio.get_running_loop().create_future(),
            handle=handle,
        )
        if handle.invocation_id in self._restart_resume_inputs:
            active.control.request_resume(self._restart_resume_inputs.pop(handle.invocation_id))
        self._active[handle.invocation_id] = active
        return active

    def _finish_active(self, handle: ExecutionHandle, result: InvocationResult) -> None:
        active = self._active.pop(handle.invocation_id, None)
        self._timer_wakes.pop(handle.invocation_id, None)
        self._child_wakes.pop(handle.invocation_id, None)
        if active is not None and not active.result.done():
            active.result.set_result(result)

    def _abandon_active(self, handle: ExecutionHandle) -> None:
        active = self._active.pop(handle.invocation_id, None)
        self._timer_wakes.pop(handle.invocation_id, None)
        self._child_wakes.pop(handle.invocation_id, None)
        if active is not None and not active.result.done():
            active.result.set_exception(
                InvocationStateConflict(
                    "invocation execution was interrupted; recovery is required"
                )
            )
            # Coalesced callers can still await the future. Consume the error
            # when there are no such callers so cleanup never leaves a warning.
            active.result.exception()

    async def _resume_active_or_terminal(
        self, handle: ExecutionHandle, resume_input: Any
    ) -> InvocationResult | None:
        self.services.invocations.refresh(handle)
        if handle.status in TERMINAL_INVOCATION_STATES:
            return self._stored_result(handle)
        active = self._active.get(handle.invocation_id)
        if active is None:
            return None
        if self.services.child_waits.for_invocation(
            handle.invocation_id, handle.workflow_fingerprint
        ) or self.services.timers.resumable_for_invocation(
            handle.invocation_id, handle.workflow_fingerprint
        ):
            await asyncio.shield(active.result)
            self.services.invocations.refresh(handle)
            return (
                self._stored_result(handle) if handle.status in TERMINAL_INVOCATION_STATES else None
            )
        if handle.status == "waiting":
            owned_handle = active.handle if active.handle is not None else handle
            if owned_handle is not handle:
                self.services.invocations.refresh(owned_handle)
            if owned_handle.status in TERMINAL_INVOCATION_STATES:
                self.services.invocations.refresh(handle)
                return self._stored_result(handle)
            self.services.invocations.update(owned_handle, status="running")
            if owned_handle is not handle:
                self.services.invocations.refresh(handle)
            active.control.request_resume(resume_input)
        return await asyncio.shield(active.result)

    @staticmethod
    def _stored_result(handle: ExecutionHandle) -> InvocationResult:
        return InvocationResult(
            handle.invocation_id,
            handle.session_id,
            handle.status,
            handle.output,
            handle.error,
        )

    def _metadata(
        self, workflow: WorkflowPlan, invocation: ExecutionHandle, active: ActiveInvocation
    ) -> dict[str, Any]:
        wakes = self._timer_wakes.get(invocation.invocation_id, {})
        return {
            "invocation_id": invocation.invocation_id,
            "session_id": invocation.session_id,
            "engine": invocation.engine,
            "engine_execution_reference": invocation.engine_execution_reference,
            "workflow_name": workflow.name,
            "workflow_version": workflow.version,
            "parent_invocation_id": invocation.parent_invocation_id,
            "parent_task_reference": invocation.parent_task_reference,
            "_invocation_handle": invocation,
            "_lifecycle": active.control,
            "_timer_wakes": {key: wake.as_value() for key, wake in wakes.items()},
            "_timer_wake": next(iter(wakes.values())).as_value() if wakes else None,
            "_child_wakes": self._child_wakes.get(invocation.invocation_id, {}),
            "_cancel_child_scopes": self._cancel_child_scopes,
        }

    def _prepare_workflow_input(
        self, workflow: WorkflowPlan, invocation: ExecutionHandle, input_data: Any
    ) -> Any:
        self.services.invocations.verify_fingerprint(invocation, workflow.fingerprint)
        self._verify_event_requirements(workflow)
        self.services.timers.verify_invocation(invocation.invocation_id, workflow.fingerprint)
        original = self.services.invocations.bind_input(invocation, input_data)
        if workflow.uses_durable_events and self.services.event_store is not None:
            self.services.event_store.bind_invocation(
                invocation.invocation_id,
                workflow.fingerprint,
                invocation.owner_principal or "anonymous",
                execution_lease=invocation._execution_lease,
            )
        return original

    async def _verify_resume_state(self, handle: ExecutionHandle, plan: WorkflowPlan) -> Any:
        self._verify_event_requirements(plan)
        self._verify_execution_engine(handle)
        self.services.invocations.verify_fingerprint(handle, plan.fingerprint)
        original_input = self.services.invocations.read_input(handle)
        try:
            self.services.effect_journal.verify_invocation(handle.invocation_id, plan.fingerprint)
            self.services.timers.verify_invocation(handle.invocation_id, plan.fingerprint)
            self.services.child_waits.verify_invocation(handle.invocation_id, plan.fingerprint)
            pending = [(handle.invocation_id, plan.fingerprint)]
            visited: set[str] = set()
            while pending:
                parent_id, fingerprint = pending.pop()
                if parent_id in visited:
                    continue
                visited.add(parent_id)
                for registration in self.services.child_waits.for_invocation(
                    parent_id, fingerprint
                ):
                    child = self.services.invocations.get(registration.suspension.child_id)
                    if child is None:
                        raise WorkflowExecutionError("awaited child invocation is absent")
                    self.services.effect_journal.verify_invocation(
                        child.invocation_id, child.workflow_fingerprint
                    )
                    self.services.timers.verify_invocation(
                        child.invocation_id, child.workflow_fingerprint
                    )
                    self.services.child_waits.verify_invocation(
                        child.invocation_id, child.workflow_fingerprint
                    )
                    pending.append((child.invocation_id, child.workflow_fingerprint))
            if self.services.event_store is not None:
                self.services.event_store.verify_invocation(
                    handle.invocation_id,
                    plan.fingerprint,
                    require_profile=plan.uses_durable_events,
                )
            if plan.uses_timing:
                clock = self.services.effect_journal.get(
                    root_clock_identity(handle.invocation_id, plan.fingerprint)
                )
                if clock is None or clock.status != "completed":
                    raise WorkflowExecutionError("workflow policy clock is missing")
                clock_started_at(clock.output)
        except WorkflowExecutionError:
            raise InvocationStateError(
                "workflow action recovery evidence is unavailable or inconsistent"
            ) from None
        return original_input

    def _record_result(
        self,
        handle: ExecutionHandle,
        *,
        status: str,
        output: Any = None,
        error: dict[str, Any] | None = None,
    ) -> InvocationResult:
        if status not in INVOCATION_STATES:
            raise ValueError(f"unsupported invocation status: {status}")
        if handle.status in TERMINAL_INVOCATION_STATES:
            return self._stored_result(handle)
        executor = getattr(self, "executor", None)
        try:
            self.services.invocations.update(
                handle,
                status=status,
                output=output,
                error=error,
                completed_at=(executor._absolute_clock() if executor is not None else time.time())
                if status in TERMINAL_INVOCATION_STATES
                else None,
            )
        except InvocationStateConflict:
            self.services.invocations.refresh(handle, terminal_only=True)
            return self._stored_result(handle)
        return InvocationResult(handle.invocation_id, handle.session_id, status, output, error)

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(engine=self.engine_name)

    async def shutdown(self) -> None:
        # Mark every local scope before propagating asyncio cancellation through
        # nested awaits. This private signal never persists a cancelled outcome.
        for active in self._active.values():
            active.control.interrupt("host_drain")
        if self._timer_dispatcher is not None:
            await self._timer_dispatcher.stop()
        tasks = {
            active.task
            for active in self._active.values()
            if active.task is not None and active.task is not asyncio.current_task()
        } | {task for task in self._ownership_tasks.values() if task is not asyncio.current_task()}
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        sandbox = getattr(self.services, "sandbox", None)
        if sandbox is not None:
            await sandbox.shutdown()


class PortableWorkflowEngine(WorkflowEngine):
    """Reference executor used by both adapters and all deterministic tests."""

    async def initialize(self, services: RuntimeServices) -> None:
        await super().initialize(services)
        self.executor = SandboxWorkflowExecutor(
            services.catalog, services=services, event_sink=services.lifecycle_events
        )
        self._timer_dispatcher = TimerDispatcher(
            services,
            self,
            clock=lambda: self.executor._absolute_clock(),
            poll_seconds=services.config.execution_timing.poll_interval_seconds,
            max_concurrency=services.config.execution_timing.max_concurrency,
        )
        await self._timer_dispatcher.start()

    async def invoke(
        self, workflow: WorkflowPlan, invocation: ExecutionHandle, input_data: Any
    ) -> InvocationResult:
        return await super().invoke(workflow, invocation, input_data)

    async def resume(
        self, handle: ExecutionHandle, resume_input: Any, plan: WorkflowPlan
    ) -> InvocationResult:
        return await super().resume(handle, resume_input, plan)

    async def _invoke_owned(
        self, workflow: WorkflowPlan, invocation: ExecutionHandle, input_data: Any
    ) -> InvocationResult:
        active = self._begin_active(invocation)
        result: InvocationResult | None = None
        try:
            input_data = self._prepare_workflow_input(workflow, invocation, input_data)
            output = await self.executor.execute(
                workflow,
                input_data,
                metadata=self._metadata(workflow, invocation, active),
            )
            if active.control.token.cancelled or invocation.status == "cancelled":
                result = self._record_result(
                    invocation,
                    status="cancelled",
                    error=invocation.error,
                )
            else:
                result = self._record_result(invocation, status="completed", output=output)
            return result
        except (TimerSuspension, ChildSuspension):
            result = self._stored_result(invocation)
            return result
        except InvocationCancelled as exc:
            self._raise_if_execution_interrupted(active)
            result = self._record_result(
                invocation,
                status="cancelled",
                error=exc.as_dict(),
            )
            return result
        except Exception as exc:
            error = (
                exc.as_dict()
                if hasattr(exc, "as_dict")
                else {"code": "workflow_execution_error", "message": str(exc), "details": {}}
            )
            if invocation.status == "cancelled":
                result = self._stored_result(invocation)
            else:
                result = self._record_result(invocation, status="faulted", error=error)
            return result
        finally:
            if result is not None:
                self._finish_active(invocation, result)
            else:
                self._abandon_active(invocation)
