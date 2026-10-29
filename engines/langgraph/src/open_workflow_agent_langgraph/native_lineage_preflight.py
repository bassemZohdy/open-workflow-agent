"""Read-only selected-checkpoint basis for a future new-identity native branch."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, replace
from typing import Any

from langgraph.graph import END
from open_workflow_agent.config import NativeStateConfig
from open_workflow_agent.errors import OwaError
from open_workflow_agent.execution_authority import ExecutionAuthority
from open_workflow_agent.native_lineage_policy import LineageKind
from open_workflow_agent.persistence import ExecutionHandle, _verify_identity
from open_workflow_agent.security import _valid_bounded_reference
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.state_lineage import StateLineageRequest, lineage_error

from .graph import LangGraphScopeExecutor, _json_identity, _node_name
from .native_state import state_reference
from .native_state_preflight import _BoundedSaver, _InspectionDriver
from .native_state_storage import checkpoint_index, readonly_saver
from .native_state_transition import projection


@dataclass(frozen=True, slots=True)
class NativeLineageSnapshot:
    reference: str
    occurrence: int
    checkpoint_id: str
    checkpoint_reference: str
    parent_reference: str | None
    metadata_json: str
    native_json: str
    phase: str


def basis_digest(input_json: str, snapshots: tuple[NativeLineageSnapshot, ...]) -> str:
    value = [
        "owa/native-lineage-basis/v1",
        "do_not_inherit_pending_writes",
        input_json,
        [
            [
                item.reference,
                item.occurrence,
                item.checkpoint_id,
                item.checkpoint_reference,
                item.parent_reference,
                item.metadata_json,
                item.native_json,
                item.phase,
            ]
            for item in snapshots
        ],
    ]
    return hashlib.sha256(_json_identity(value).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class NativeLineagePreflight:
    request: StateLineageRequest
    input_json: str
    snapshots: tuple[NativeLineageSnapshot, ...]
    task_node: str | None

    def verify_basis(self, input_json: str, snapshots: tuple[NativeLineageSnapshot, ...]) -> None:
        if basis_digest(input_json, snapshots) != self.request.source_basis_digest:
            raise lineage_error("source_basis_changed")


def _owner(
    services: RuntimeServices,
    handle: ExecutionHandle,
    authority: ExecutionAuthority | None,
    kind: LineageKind,
) -> None:
    saved = services.invocations.get(handle.invocation_id)
    if (
        not services.config.execution_authorization.enabled
        or saved is None
        or saved._persisted_payload is None
        or handle.engine != "langgraph"
        or handle.owner_principal is None
        or authority is None
        or authority.principal.identity != handle.owner_principal
    ):
        raise lineage_error("original_owner_required")
    _verify_identity(handle, json.loads(saved._persisted_payload))
    action, resource = "state." + kind, "invocation:" + handle.invocation_id
    services.execution_authorizations.authorize_request(authority, action, resource)
    original = services.execution_authorizations.verify_invocation(handle)
    if original is None:
        raise lineage_error("original_authority_required")
    services.execution_authorizations.authorize_request(original, action, resource)


async def prepare_native_lineage(
    services: RuntimeServices,
    source: ExecutionHandle,
    authority: ExecutionAuthority | None,
    *,
    kind: LineageKind,
    target_invocation_id: str,
    request_id: str,
    source_reference: str,
    acknowledge_reexecution: bool = False,
    task_reference: str | None = None,
    values: Any = None,
    bounds: NativeStateConfig | None = None,
    _reserved_request: StateLineageRequest | None = None,
) -> NativeLineagePreflight:
    try:
        if type(kind) is not str or kind not in {"replay", "branch"}:
            raise lineage_error("kind_invalid")
        _owner(services, source, authority, kind)
        if _reserved_request is not None:
            from .native_lineage_reservation import NativeReplayReservations

            if authority is None:
                raise lineage_error("original_owner_required")
            recovered, target = NativeReplayReservations(services).recover(
                source, authority, request_id
            )
            if recovered != _reserved_request or target.invocation_id != target_invocation_id:
                raise lineage_error("reservation_request_invalid")
        if (
            not _valid_bounded_reference(target_invocation_id, maximum_bytes=256)
            or target_invocation_id == source.invocation_id
            or (
                _reserved_request is None
                and services.invocations.get(target_invocation_id) is not None
            )
        ):
            raise lineage_error("new_business_identity_required")
        plan = await services.recovery_definitions.resolve_inspection(source)
        policy = plan.native_state_replay if kind == "replay" else plan.native_state_branch
        if (
            policy is None
            or policy.authorization_policy_ref != services.config.execution_authorization.id
        ):
            raise lineage_error("declared_policy_required")
        path = services.engine_database_path("langgraph", create_namespace=False)
        encoded = None
        if kind == "branch":
            if plan.native_state_updates is None or task_reference is None:
                raise lineage_error("update_policy_required")
            encoded = plan.native_state_updates.validate(
                task_reference, values, policy.authorization_policy_ref
            )
        elif task_reference is not None or values is not None:
            raise lineage_error("replay_cannot_update")
        request = StateLineageRequest(
            source.invocation_id,
            target_invocation_id,
            plan.fingerprint,
            request_id,
            source_reference,
            "0" * 64,
            policy.digest,
            hashlib.sha256(
                _json_identity(["owa/langgraph-state-lineage/v1", path, plan.fingerprint]).encode()
            ).hexdigest(),
            kind,
            policy.effect_policy,
            acknowledge_reexecution,
            task_reference,
            encoded,
        )
        limits = NativeStateConfig.model_validate((bounds or NativeStateConfig()).model_dump())
        async with (
            asyncio.timeout(limits.timeout_seconds),
            readonly_saver(path, limits) as (saver, postgres),
        ):
            probe = LangGraphScopeExecutor(
                plan, saver, source.invocation_id, effect_journal=services.effect_journal
            )
            root = probe.checkpoint_config("/do", 1)
            thread = root["configurable"]["thread_id"]
            index, _ = await checkpoint_index(saver, thread, limits, postgres)
            key = next(
                (
                    key
                    for key, _ in index
                    if state_reference(source.invocation_id, plan.fingerprint, key)
                    == source_reference
                ),
                None,
            )
            if key is None:
                raise lineage_error("source_reference_unavailable")
            guarded = _BoundedSaver(saver, limits, postgres, selected={thread: key})
            driver = _InspectionDriver(
                plan, guarded, source.invocation_id, effect_journal=services.effect_journal
            )
            original_input = services.invocations.read_input(source)
            input_json = _json_identity(original_input)
            driver._expected_input_json = input_json
            snapshots = []
            pending = [("/do", 1)]
            visited = set()
            root_visits = {}
            while pending:
                reference, occurrence = pending.pop(0)
                if (reference, occurrence) in visited:
                    continue
                visited.add((reference, occurrence))
                config = driver.checkpoint_config(reference, occurrence)
                item = await guarded.aget_tuple(config)
                if item is None:
                    raise lineage_error("source_scope_missing")
                native = item.checkpoint["channel_values"]
                phase = "checkpoint"
                if reference == "/do" and set(native) == {"__start__"}:
                    driver.validate_historical_initial_projection(item, original_input)
                    native, phase = native["__start__"], "initial"
                decoded, _ = driver._validate_native(native, reference, occurrence)
                if reference == "/do":
                    root_visits = decoded["task_visits"]
                elif (
                    driver._state_digest(native) != driver._required_scopes[(reference, occurrence)]
                    or native["next_node"] != END
                ):
                    raise lineage_error("source_scope_changed")
                item_key = item.checkpoint["id"]
                parent = guarded.parents[config["configurable"]["thread_id"]]
                snapshots.append(
                    NativeLineageSnapshot(
                        reference,
                        occurrence,
                        item_key,
                        state_reference(source.invocation_id, plan.fingerprint, item_key),
                        state_reference(source.invocation_id, plan.fingerprint, parent)
                        if parent
                        else None,
                        _json_identity(
                            {
                                "created_at": item.checkpoint["ts"],
                                "step": item.metadata["step"],
                                "source": item.metadata["source"],
                                "has_pending_writes": bool(item.pending_writes),
                            }
                        ),
                        _json_identity(projection(native)),
                        phase,
                    )
                )
                pending.extend(sorted(set(driver._required_scopes) - visited))
            # Recheck physical/archive/common evidence after every native await.
            async with services.recovery_definitions.inspection_profile(source) as fresh_plan:
                if fresh_plan.fingerprint != plan.fingerprint:
                    raise lineage_error("source_definition_changed")
                fresh = _InspectionDriver(
                    plan, guarded, source.invocation_id, effect_journal=services.effect_journal
                )
                fresh._expected_input_json = input_json
                observed = {}
                for item in snapshots:
                    packet = json.loads(item.native_json)
                    fresh._validate_native(packet, item.reference, item.occurrence)
                    observed[(item.reference, item.occurrence)] = (
                        fresh._state_digest(packet),
                        packet["next_node"],
                    )
                for coordinate, digest in fresh._required_scopes.items():
                    if observed.get(coordinate) != (digest, END):
                        raise lineage_error("source_scope_changed")
            node = None
            if kind == "branch":
                task = next((task for task in plan.tasks if task.reference == task_reference), None)
                if (
                    task is None
                    or root_visits.get(task.execution_reference or task.reference, 0) < 1
                ):
                    raise lineage_error("attribution_unvisited")
                node = _node_name(task)
            ordered = tuple(sorted(snapshots, key=lambda item: (item.reference, item.occurrence)))
            if (
                len(input_json.encode())
                + sum(
                    len(item.native_json.encode()) + len(item.metadata_json.encode())
                    for item in ordered
                )
                > limits.max_native_bytes * 4
            ):
                raise lineage_error("source_packet_budget")
            if services.engine_database_path("langgraph", create_namespace=False) != path:
                raise lineage_error("storage_binding_changed")
            _owner(services, source, authority, kind)
            if (
                _reserved_request is None
                and services.invocations.get(target_invocation_id) is not None
            ):
                raise lineage_error("new_business_identity_required")
            completed = replace(request, source_basis_digest=basis_digest(input_json, ordered))
            if _reserved_request is not None:
                recovered, target = NativeReplayReservations(services).recover(
                    source, authority, request_id
                )
                if recovered != completed or target.invocation_id != target_invocation_id:
                    raise lineage_error("reservation_request_invalid")
            return NativeLineagePreflight(
                completed,
                input_json,
                ordered,
                node,
            )
    except OwaError:
        raise
    except TimeoutError:
        raise lineage_error("preflight_timeout") from None
    except Exception:
        raise lineage_error("source_unavailable") from None
