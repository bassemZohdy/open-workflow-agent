"""Configured bounded OWA state controls over the native storage contract."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, Iterator
from contextlib import contextmanager
from typing import Any

from open_workflow_agent.config import NativeStateUpdateConfig
from open_workflow_agent.engine import PortableWorkflowEngine
from open_workflow_agent.errors import (
    ConfigurationError,
    InvocationNotFound,
    InvocationStateError,
    ModelError,
    OwaError,
    UnsupportedWorkflowFeature,
)
from open_workflow_agent.execution_authority import ExecutionAuthority
from open_workflow_agent.model_responses import bounded_model_json
from open_workflow_agent.native_state import NativeStateCapacity, NativeStateUnavailable
from open_workflow_agent.native_state_policy import CAPABILITY
from open_workflow_agent.persistence import ExecutionHandle
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.state_update_resolution import StateUpdateResolution, resolution_identity
from open_workflow_agent.state_updates import (
    StateUpdateBackend,
    StateUpdateProof,
    StateUpdateRequest,
    StateUpdates,
    state_update_error,
)
from open_workflow_agent.storage import is_postgresql_datasource
from open_workflow_agent.workflow import WorkflowPlan

from . import native
from .native_state_controls import inspect_native_control, state_control_storage_identity
from .native_state_mutations import prepare_state_update_storage
from .native_state_preflight import prepare_native_state_update
from .native_state_transition import AttestedStateControls


class LangGraphStateController:
    def __init__(self, services: RuntimeServices, engine: PortableWorkflowEngine) -> None:
        config = services.config.native_state_updates
        if config is None:
            raise ConfigurationError("native state update policy is absent")
        self.services, self.engine = services, engine
        self.config = NativeStateUpdateConfig.model_validate(config.model_dump(mode="json"))
        if services.config.native_state is None:
            raise ConfigurationError("native state updates require configured state views")
        self.config.verify_view_budget(services.config.native_state)
        self.path = services.engine_database_path("langgraph", create_namespace=False)
        self._slots = asyncio.Semaphore(config.bounds.max_concurrency)
        self._tasks: set[asyncio.Task[dict[str, Any]]] = set()
        self.bindings: dict[str, AttestedStateControls] = {}
        self._binding_users: dict[str, int] = {}
        self.coordinator = StateUpdates(
            services.effect_journal,
            services.invocations,
            services.execution_authorizations,
            backend_resolver=self._proof_backend,
        )
        self.opened = False

    async def open(self) -> None:
        self.services.engine_database_path("langgraph")
        postgres = is_postgresql_datasource(self.path)
        saver_type = getattr(native, "AsyncPostgresSaver" if postgres else "AsyncSqliteSaver", None)
        if saver_type is None or self.path == ":memory:":
            raise ConfigurationError("native state updates require durable native storage")
        async with saver_type.from_conn_string(self.path) as saver:
            await native._prepare_saver(saver, self.path)
            await prepare_state_update_storage(saver, postgresql=postgres)
        self.opened = True

    def verify(self, configuration: NativeStateUpdateConfig | None) -> None:
        if (
            not self.opened
            or configuration != self.config
            or self.services.config.native_state_updates != self.config
            or self.services.effect_journal._state_updates is not self.coordinator
            or self.services.native_state_controller is not self
            or self.path != self.services.engine_database_path("langgraph", create_namespace=False)
            or not self.services.config.execution_authorization.enabled
            or not self.services.config.server.api_security_profile
            or self.services.config.native_state is None
        ):
            raise ConfigurationError("native state update installation or policy changed")

    def bind(self, plan: WorkflowPlan) -> None:
        self.verify(self.services.config.native_state_updates)
        if plan.native_state_updates is None or not any(
            item.required and item.capability_id == CAPABILITY for item in plan.native_extensions
        ):
            raise UnsupportedWorkflowFeature("workflow does not require native state updates")
        if (
            plan.native_state_updates.authorization_policy_ref
            != self.services.config.execution_authorization.id
        ):
            raise UnsupportedWorkflowFeature(
                "native state update authorization policy is unavailable"
            )

    @contextmanager
    def _binding(self, plan: WorkflowPlan) -> Iterator[AttestedStateControls]:
        self.bind(plan)
        if plan.fingerprint not in self.bindings:
            if len(self.bindings) >= self.config.max_workflow_bindings:
                raise NativeStateCapacity("native state workflow binding capacity is exhausted")
            bridge = AttestedStateControls(self.services, plan, bounds=self.config.bounds)
            self.coordinator.register(bridge.backend())
            self.bindings[plan.fingerprint] = bridge
            self._binding_users[plan.fingerprint] = 0
        self._binding_users[plan.fingerprint] += 1
        try:
            yield self.bindings[plan.fingerprint]
        finally:
            self._binding_users[plan.fingerprint] -= 1
            if self._binding_users[plan.fingerprint] == 0:
                bridge = self.bindings.pop(plan.fingerprint)
                self._binding_users.pop(plan.fingerprint)
                self.coordinator._backends.pop(bridge.storage_identity, None)
                bridge.proposals.clear()

    def _proof_backend(self, request: StateUpdateRequest) -> StateUpdateBackend:
        self.verify(self.services.config.native_state_updates)
        expected = state_control_storage_identity(self.path, request.workflow_fingerprint)
        if request.storage_identity != expected:
            raise state_update_error("native_binding_changed")

        async def unavailable(
            request: StateUpdateRequest, handle: ExecutionHandle
        ) -> StateUpdateProof:
            raise state_update_error("native_mutation_binding_missing")

        def inspect(request: StateUpdateRequest) -> StateUpdateProof | None:
            self.verify(self.services.config.native_state_updates)
            return inspect_native_control(self.path, request, self.config.bounds)

        # Proof-only fallback permits restart/tree audit without accumulating
        # executable bindings or resolving plans inside a common transaction.
        return StateUpdateBackend(expected, unavailable, inspect)

    def _authorize(self, handle: ExecutionHandle, actor: ExecutionAuthority, action: str) -> None:
        saved = self.services.invocations.get(handle.invocation_id)
        if saved is None or saved.owner_principal != actor.principal.identity:
            raise InvocationNotFound("invocation not found")
        resource = "invocation:" + handle.invocation_id
        self.services.execution_authorizations.authorize_request(actor, action, resource)
        original = self.services.execution_authorizations.verify_invocation(handle)
        self.services.execution_authorizations.authorize_request(original, action, resource)

    async def _run(
        self,
        handle: ExecutionHandle,
        actor: ExecutionAuthority,
        action: str,
        operation: Callable[[], Coroutine[Any, Any, dict[str, Any]]],
    ) -> dict[str, Any]:
        self.verify(self.services.config.native_state_updates)
        self._authorize(handle, actor, action)
        if self._slots.locked():
            raise NativeStateCapacity("native state control capacity is exhausted")
        async with self._slots:
            task = asyncio.create_task(operation())
            self._tasks.add(task)
            try:
                try:
                    async with asyncio.timeout(self.config.bounds.timeout_seconds):
                        result = await asyncio.shield(task)
                except BaseException:
                    task.cancel()
                    while not task.done():
                        try:
                            await asyncio.shield(task)
                        except asyncio.CancelledError:
                            continue
                        except Exception:
                            break
                    if not task.cancelled():
                        task.exception()
                    raise
                self.verify(self.services.config.native_state_updates)
                self._authorize(handle, actor, action)
                try:
                    bounded_model_json(result, limit=self.config.bounds.max_response_bytes)
                except ModelError:
                    raise NativeStateUnavailable(
                        "state control response exceeds its configured bound"
                    ) from None
                return result
            except TimeoutError:
                raise NativeStateUnavailable(
                    "native state control timed out; inspect its retained outcome"
                ) from None
            except OwaError:
                raise
            except Exception:
                raise NativeStateUnavailable(
                    "native state control evidence is unavailable"
                ) from None
            finally:
                self._tasks.discard(task)

    async def _plan(self, handle: ExecutionHandle) -> WorkflowPlan:
        plan = await self.services.recovery_definitions.resolve_inspection(handle)
        self.bind(plan)
        return plan

    def _waiting(self, handle: ExecutionHandle) -> None:
        self.services.invocations.refresh(handle)
        if handle.status != "waiting":
            raise InvocationStateError("native state controls require a waiting invocation")

    @staticmethod
    def _proof_result(request: StateUpdateRequest, proof: StateUpdateProof) -> dict[str, Any]:
        return {
            "profile": CAPABILITY,
            "operation_id": request.identity.key,
            "request_digest": request.request_digest,
            "state": "completed",
            "base_reference": proof.base_reference,
            "result_reference": proof.result_reference,
        }

    async def update(
        self,
        handle: ExecutionHandle,
        authority: ExecutionAuthority,
        *,
        request_id: str,
        base_reference: str,
        task_reference: str,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        async def operation() -> dict[str, Any]:
            plan = await self._plan(handle)
            self.services.invocations.refresh(handle)
            assert plan.native_state_updates is not None
            encoded = plan.native_state_updates.validate(
                task_reference, values, self.services.config.execution_authorization.id
            )
            previous = next(
                (
                    item
                    for item, _ in self.coordinator.requests(handle)
                    if item.request_id == request_id
                ),
                None,
            )
            if previous is not None and (
                previous.base_reference != base_reference
                or previous.task_reference != task_reference
                or previous.values_json != encoded
                or previous.policy_digest != plan.native_state_updates.digest
            ):
                raise state_update_error("request_changed")
            if handle.status in {"completed", "faulted", "cancelled"}:
                if previous is None:
                    raise InvocationStateError("terminal invocations cannot admit state controls")
                receipt = self.services.effect_journal.get(previous.identity)
                if receipt is None or receipt.status != "completed":
                    raise InvocationStateError("terminal invocations cannot admit state controls")
                proof = self.coordinator._proof(previous)
                assert proof is not None
                return self._proof_result(previous, proof)
            with self._binding(plan) as bridge:
                async with self.engine._own_execution(handle):
                    self._waiting(handle)
                    request = previous
                    phase = next(
                        (
                            phase
                            for item, phase in self.coordinator.requests(handle)
                            if request is not None and item.identity == request.identity
                        ),
                        None,
                    )
                    try:
                        if request is None or phase == "prepared":
                            preflight = await prepare_native_state_update(
                                self.services,
                                handle,
                                authority,
                                request_id=request_id,
                                base_reference=base_reference,
                                task_reference=task_reference,
                                values=values,
                                bounds=self.config.bounds,
                            )
                            candidate = bridge.stage(preflight).request
                            if request is not None and request != candidate:
                                raise state_update_error("request_changed")
                            request = candidate
                        assert request is not None
                        proof = await self.coordinator.update(handle, request, authority)
                        return self._proof_result(request, proof)
                    except OwaError as error:
                        if request is not None:
                            error.details.update(
                                operation_id=request.identity.key,
                                request_digest=request.request_digest,
                            )
                        raise
                    finally:
                        if request is not None:
                            bridge.proposals.pop(request.identity.key, None)

        return await self._run(handle, authority, "state.update", operation)

    async def inspect(
        self, handle: ExecutionHandle, authority: ExecutionAuthority
    ) -> dict[str, Any]:
        async def operation() -> dict[str, Any]:
            await self._plan(handle)
            return {
                "profile": CAPABILITY,
                "invocation_id": handle.invocation_id,
                "verification": "common_metadata_only",
                "updates": [
                    {
                        "operation_id": item.identity.key,
                        "request_digest": item.request_digest,
                        "task_reference": item.task_reference,
                        "state": phase,
                    }
                    for item, phase in self.coordinator.requests(handle)
                ],
            }

        return await self._run(handle, authority, "state.inspect", operation)

    async def resolve(
        self,
        handle: ExecutionHandle,
        authority: ExecutionAuthority,
        *,
        operation_id: str,
        request_digest: str,
        decision: StateUpdateResolution,
    ) -> dict[str, Any]:
        async def operation() -> dict[str, Any]:
            await self._plan(handle)
            target = next(
                (
                    item
                    for item, _ in self.coordinator.requests(handle)
                    if item.identity.key == operation_id
                ),
                None,
            )
            if target is None:
                raise InvocationNotFound("state control not found")
            if target.request_digest != request_digest:
                raise state_update_error("request_changed")
            self.services.invocations.refresh(handle)
            if handle.status in {"completed", "faulted", "cancelled"}:
                audit = self.services.effect_journal.get(resolution_identity(target.identity))
                if audit is None or audit.output["resolution"] != decision.descriptor:
                    raise InvocationStateError(
                        "terminal invocations cannot admit state resolutions"
                    )
            else:
                async with self.engine._own_execution(handle):
                    self._waiting(handle)
                    audit = await self.coordinator.resolve(handle, target, decision, authority)
            return {
                "profile": CAPABILITY,
                "operation_id": operation_id,
                "decision": decision.decision,
                "resolution_digest": audit.request_digest,
            }

        return await self._run(handle, authority, "state.resolve", operation)

    async def close(self) -> None:
        self.opened = False
        for task in tuple(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
        if self.services.effect_journal._state_updates is self.coordinator:
            self.services.effect_journal._state_updates = None
        if self.services.native_state_controller is self:
            self.services.native_state_controller = None
        self.bindings.clear()
        self.coordinator._backends.clear()
