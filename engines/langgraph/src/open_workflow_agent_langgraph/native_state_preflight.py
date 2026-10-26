"""Read-only OWA checkpoint/receipt preflight; it neither updates nor resumes state."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from langgraph.graph import END
from open_workflow_agent.config import NativeStateConfig
from open_workflow_agent.errors import OwaError
from open_workflow_agent.execution_authority import ExecutionAuthority
from open_workflow_agent.ownership import execution_write_guard
from open_workflow_agent.persistence import ExecutionHandle, _verify_identity
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.state_updates import StateUpdateRequest, state_update_error

from .graph import LangGraphScopeExecutor, _json_identity, _node_name
from .native_state import state_reference
from .native_state_controls import state_control_storage_identity
from .native_state_storage import checkpoint_index, guard_checkpoint, readonly_saver


@dataclass(frozen=True, slots=True)
class NativeStatePreflight:
    request: StateUpdateRequest
    checkpoint_id: str
    base_native_json: str
    task_node: str

    @property
    def base_digest(self) -> str:
        return hashlib.sha256(self.base_native_json.encode()).hexdigest()

    def verify_base(self, native: Any) -> None:
        if _json_identity(native) != self.base_native_json:
            raise state_update_error("native_base_changed")


class _BoundedSaver:
    def __init__(self, saver: Any, bounds: NativeStateConfig, postgres: bool) -> None:
        self.saver, self.bounds, self.postgres = saver, bounds, postgres
        self.remaining = bounds.max_native_bytes
        self.cache: dict[str, Any] = {}
        self.coordinates: dict[str, tuple[str, int]] = {}

    async def aget_tuple(self, config: dict[str, Any]) -> Any:
        thread = config["configurable"]["thread_id"]
        if thread in self.cache:
            return self.cache[thread]
        if len(self.cache) >= self.bounds.max_history:
            raise state_update_error("native_scope_limit")
        index, _ = await checkpoint_index(self.saver, thread, self.bounds, self.postgres)
        checkpoint = None
        if index:
            key = index[0][0]
            self.remaining -= await guard_checkpoint(
                self.saver, thread, key, self.remaining, self.postgres
            )
            checkpoint = await self.saver.aget_tuple(
                {
                    "configurable": {
                        "thread_id": thread,
                        "checkpoint_ns": "",
                        "checkpoint_id": key,
                    }
                }
            )
            if checkpoint is None:
                raise state_update_error("native_scope_missing")
            if (
                checkpoint.config["configurable"]
                != {
                    "thread_id": thread,
                    "checkpoint_ns": "",
                    "checkpoint_id": key,
                }
                or checkpoint.checkpoint.get("id") != key
                or type(checkpoint.checkpoint.get("v")) is not int
                or checkpoint.checkpoint["v"] != 4
                or type(checkpoint.metadata.get("step")) is not int
                or checkpoint.metadata.get("source") not in {"input", "loop", "update", "fork"}
            ):
                raise state_update_error("native_checkpoint_changed")
            timestamp = checkpoint.checkpoint.get("ts")
            if (
                not isinstance(timestamp, str)
                or len(timestamp) > 128
                or datetime.fromisoformat(timestamp).tzinfo is None
            ):
                raise state_update_error("native_timestamp_invalid")
        self.cache[thread] = checkpoint
        return checkpoint


class _InspectionDriver(LangGraphScopeExecutor):
    def checkpoint_config(self, reference: str, occurrence: int) -> dict[str, Any]:
        config = super().checkpoint_config(reference, occurrence)
        self.saver.coordinates[config["configurable"]["thread_id"]] = (reference, occurrence)
        return config


def _owner(
    services: RuntimeServices, handle: ExecutionHandle, authority: ExecutionAuthority | None
) -> None:
    if not services.config.execution_authorization.enabled:
        raise state_update_error("authorization_required")
    saved = services.invocations.get(handle.invocation_id)
    if (
        saved is None
        or saved._persisted_payload is None
        or saved.status != "waiting"
        or handle.status != "waiting"
        or handle.engine != "langgraph"
        or handle.owner_principal is None
        or authority is None
        or authority.principal.identity != handle.owner_principal
        or handle._execution_lease is None
    ):
        raise state_update_error("original_waiting_owner_required")
    _verify_identity(handle, json.loads(saved._persisted_payload))
    statement, parameters = execution_write_guard(
        services.effect_journal.connection, handle._execution_lease
    )
    if services.effect_journal.connection.execute(statement, parameters).fetchone() is None:
        raise state_update_error("ownership_lost")
    resource = "invocation:" + handle.invocation_id
    services.execution_authorizations.authorize_request(authority, "state.update", resource)
    original = services.execution_authorizations.verify_invocation(handle)
    if original is None:
        raise state_update_error("original_authority_required")
    services.execution_authorizations.authorize_request(original, "state.update", resource)


async def prepare_native_state_update(
    services: RuntimeServices,
    handle: ExecutionHandle,
    authority: ExecutionAuthority | None,
    *,
    request_id: str,
    base_reference: str,
    task_reference: str,
    values: Any,
    bounds: NativeStateConfig | None = None,
) -> NativeStatePreflight:
    try:
        return await _prepare_native_state_update(
            services,
            handle,
            authority,
            request_id=request_id,
            base_reference=base_reference,
            task_reference=task_reference,
            values=values,
            bounds=bounds,
        )
    except OwaError:
        raise
    except TimeoutError:
        raise state_update_error("preflight_timeout") from None
    except Exception:
        raise state_update_error("native_state_unavailable") from None


async def _prepare_native_state_update(
    services: RuntimeServices,
    handle: ExecutionHandle,
    authority: ExecutionAuthority | None,
    *,
    request_id: str,
    base_reference: str,
    task_reference: str,
    values: Any,
    bounds: NativeStateConfig | None = None,
) -> NativeStatePreflight:
    """Validate policy and all retained OWA scopes without creating a control intent.

    The resulting adapter-private base must be compared again inside the owned
    native write transaction. No public mutation capability installs this yet.
    """
    _owner(services, handle, authority)
    plan = await services.recovery_definitions.resolve_inspection(handle)
    policy = plan.native_state_updates
    if policy is None:
        raise state_update_error("native_update_policy_missing")
    encoded = policy.validate(task_reference, values, services.config.execution_authorization.id)
    path = services.engine_database_path("langgraph", create_namespace=False)
    request = StateUpdateRequest(
        handle.invocation_id,
        plan.fingerprint,
        request_id,
        base_reference,
        task_reference,
        encoded,
        policy.digest,
        state_control_storage_identity(path, plan.fingerprint),
    )
    limits = NativeStateConfig.model_validate((bounds or NativeStateConfig()).model_dump())
    async with (
        asyncio.timeout(limits.timeout_seconds),
        readonly_saver(path, limits) as (saver, postgres),
    ):
        guarded = _BoundedSaver(saver, limits, postgres)
        driver = _InspectionDriver(
            plan, guarded, handle.invocation_id, effect_journal=services.effect_journal
        )
        original = services.invocations.read_input(handle)
        driver._expected_input_json = _json_identity(original)
        config = driver.checkpoint_config("/do", 1)
        checkpoint = await guarded.aget_tuple(config)
        if checkpoint is None:
            raise state_update_error("native_base_missing")
        key = checkpoint.config["configurable"]["checkpoint_id"]
        if state_reference(handle.invocation_id, plan.fingerprint, key) != base_reference:
            raise state_update_error("native_base_stale")
        native = checkpoint.checkpoint["channel_values"]
        if "__start__" in native:
            raise state_update_error("native_base_unexecuted")
        driver._validate_native(native, "/do", 1)
        await driver._validate_saved_scopes()
        # Recheck common evidence with fresh physical definition pins after all
        # native awaits. Cached native packets all came from one read snapshot.
        async with services.recovery_definitions.inspection_profile(handle) as verified:
            if verified.fingerprint != plan.fingerprint:
                raise state_update_error("native_definition_changed")
            fresh = LangGraphScopeExecutor(
                plan, saver, handle.invocation_id, effect_journal=services.effect_journal
            )
            fresh._expected_input_json = _json_identity(original)
            observed = {}
            for thread, item in guarded.cache.items():
                if item is None:
                    continue
                ref, occurrence = guarded.coordinates[thread]
                channels = item.checkpoint["channel_values"]
                fresh._validate_native(channels, ref, occurrence)
                observed[(ref, occurrence)] = (fresh._state_digest(channels), channels["next_node"])
            for coordinate, digest in fresh._required_scopes.items():
                if observed.get(coordinate) != (digest, END):
                    raise state_update_error("native_scope_changed")
            decoded, _ = fresh._validate_native(native, "/do", 1)
        task = next((item for item in plan.tasks if item.reference == task_reference), None)
        if (
            task is None
            or decoded["task_visits"].get(task.execution_reference or task.reference, 0) < 1
            or native["next_node"] == END
        ):
            raise state_update_error("native_attribution_unavailable")
        if services.engine_database_path("langgraph", create_namespace=False) != path:
            raise state_update_error("native_binding_changed")
        _owner(services, handle, authority)
        return NativeStatePreflight(request, key, _json_identity(native), _node_name(task))
