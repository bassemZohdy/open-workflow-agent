"""Prepare narrowly bounded root-only native lineage projections."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from open_workflow_agent.effects import ReceiptDependency
from open_workflow_agent.execution_authorization import AUTHORITY_OPERATION
from open_workflow_agent.lineage_registry import RemappedReceipt
from open_workflow_agent.native_lineage_policy import LineageKind
from open_workflow_agent.persistence import ExecutionHandle
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.state_lineage import lineage_error
from open_workflow_agent.workflow import WorkflowPlan

from .graph import LangGraphScopeExecutor, _json_identity, _node_name
from .native_lineage_fork import NativeForkPacket, NativeForkProposal
from .native_lineage_inspection import selected_business_receipts
from .native_lineage_preflight import NativeLineagePreflight


def _validate_root_basis(
    services: RuntimeServices,
    plan: WorkflowPlan,
    preflight: NativeLineagePreflight,
    kind: LineageKind,
) -> tuple[dict[str, Any], tuple[ReceiptDependency, ...], int]:
    """Check the supported source profile without creating a target invocation.

    Native channels, timing, state controls and native routing remain excluded.
    """
    try:
        request = preflight.request
        preflight.verify_basis(preflight.input_json, preflight.snapshots)
        policy = plan.native_state_replay if kind == "replay" else plan.native_state_branch
        if (
            request.kind != kind
            or request.workflow_fingerprint != plan.fingerprint
            or policy is None
            or policy.digest != request.policy_digest
            or policy.effect_policy != request.effect_policy
            or plan.uses_timing
            or plan.native_channels is not None
            or any(
                item.capability_id.startswith(("langgraph.command@", "langgraph.send@"))
                for item in plan.native_extensions
            )
            or not 1 <= len(preflight.snapshots) <= 64
            or (
                preflight.snapshots[0].reference,
                preflight.snapshots[0].occurrence,
                preflight.snapshots[0].phase,
            )
            != ("/do", 1, "checkpoint")
        ):
            raise lineage_error("replay_profile_unsupported")
        native = json.loads(preflight.snapshots[0].native_json)
        bindings = json.loads(native["receipt_bindings"])
        if bindings.get("version") != 2:
            raise lineage_error("replay_profile_unsupported")
        dependencies = selected_business_receipts(preflight, services.effect_journal)
        business = {item.identity.key for item in dependencies}
        for source in preflight.snapshots:
            source_bindings = json.loads(json.loads(source.native_json)["receipt_bindings"])
            for value in source_bindings["receipts"]:
                dependency = ReceiptDependency.from_value(value)
                operation = dependency.identity.operation
                if (
                    dependency.identity.key not in business
                    and operation != AUTHORITY_OPERATION
                    and not operation.startswith(
                        (
                            "policy:execution-authorization:v1:",
                            "policy:execution-authorization-intent:v1:",
                            "policy:execution-authorization-seal:v1",
                        )
                    )
                ):
                    raise lineage_error("replay_dependency_unsupported")
        snapshot = json.loads(native["snapshot"])
        visits = snapshot["task_visits"]
        tasks = plan.scopes[0].tasks
        root_refs = {item.execution_reference or item.reference for item in tasks}
        root_visits = {key: value for key, value in visits.items() if key in root_refs}
        completed = len(root_visits)
        if not 0 < completed <= len(tasks) or root_visits != {
            (item.execution_reference or item.reference): 1 for item in tasks[:completed]
        }:
            raise lineage_error("replay_profile_unsupported")
        coordinates = {(item.reference, item.occurrence) for item in preflight.snapshots}
        for item in preflight.snapshots[1:]:
            scope = next(scope for scope in plan.scopes if scope.reference == item.reference)
            packet = json.loads(item.native_json)
            child_bindings = json.loads(packet["receipt_bindings"])
            child_visits = json.loads(packet["snapshot"])["task_visits"]
            if (
                item.phase != "checkpoint"
                or packet["next_node"] != "__end__"
                or child_bindings.get("version") != 2
                or not scope.tasks
                or any(
                    child_visits.get(task.execution_reference or task.reference) != 1
                    for task in scope.tasks
                )
                or any(
                    (entry["reference"], entry["occurrence"]) not in coordinates
                    for entry in child_bindings["scopes"]
                )
            ):
                raise lineage_error("replay_profile_unsupported")
        if kind == "branch":
            task = tasks[completed - 1]
            updates = plan.native_state_updates
            if (
                updates is None
                or request.task_reference != task.reference
                or preflight.task_node != _node_name(task)
                or updates.validate(task.reference, request.values, policy.authorization_policy_ref)
                != request.values_json
            ):
                raise lineage_error("branch_attribution_invalid")
        return native, dependencies, completed
    except Exception:
        raise lineage_error("replay_staging_unavailable") from None


def validate_root_replay_basis(
    services: RuntimeServices,
    plan: WorkflowPlan,
    preflight: NativeLineagePreflight,
) -> tuple[dict[str, Any], tuple[ReceiptDependency, ...], int]:
    return _validate_root_basis(services, plan, preflight, "replay")


def validate_root_branch_basis(
    services: RuntimeServices,
    plan: WorkflowPlan,
    preflight: NativeLineagePreflight,
) -> tuple[dict[str, Any], tuple[ReceiptDependency, ...], int]:
    """Validate the supported attributed source before reserving a branch."""
    return _validate_root_basis(services, plan, preflight, "branch")


def _stage_root_lineage(
    services: RuntimeServices,
    plan: WorkflowPlan,
    preflight: NativeLineagePreflight,
    target: ExecutionHandle,
    kind: LineageKind,
) -> tuple[NativeForkProposal, tuple[ReceiptDependency, ...]]:
    """Stage a root checkpoint; native proof and common binding must follow."""
    try:
        request = preflight.request
        if (
            request.target_invocation_id != target.invocation_id
            or target.workflow_fingerprint != plan.fingerprint
            or target._execution_lease is None
        ):
            raise lineage_error("replay_profile_unsupported")
        _, dependencies, _ = _validate_root_basis(services, plan, preflight, kind)
        authority = services.execution_authorizations.authority_receipt(target)
        if authority is None:
            raise lineage_error("original_authority_required")
        source_packets: dict[tuple[str, int], dict[str, Any]] = {
            (item.reference, item.occurrence): json.loads(item.native_json)
            for item in preflight.snapshots
        }
        business = {item.identity.key: item for item in dependencies}
        staged: dict[tuple[str, int], dict[str, Any]] = {}
        visiting: set[tuple[str, int]] = set()

        def stage(coordinate: tuple[str, int]) -> dict[str, Any]:
            if coordinate in staged:
                return staged[coordinate]
            if coordinate in visiting or coordinate not in source_packets:
                raise lineage_error("replay_scope_invalid")
            visiting.add(coordinate)
            packet = source_packets[coordinate]
            original = json.loads(packet["receipt_bindings"])
            target_receipts = [ReceiptDependency.capture(authority.identity, authority.receipt)]
            for value in original["receipts"]:
                dependency = ReceiptDependency.from_value(value)
                if dependency.identity.key in business:
                    target_receipts.append(
                        ReceiptDependency(
                            RemappedReceipt.capture(request, dependency).target,
                            dependency.receipt_digest,
                        )
                    )
            scopes = []
            for child in original["scopes"]:
                child_key = (child["reference"], child["occurrence"])
                scopes.append(
                    {
                        **child,
                        "state_digest": LangGraphScopeExecutor._state_digest(stage(child_key)),
                    }
                )
            snapshot = json.loads(packet["snapshot"])
            snapshot["variables"]["workflow"]["id"] = target.invocation_id
            if kind == "branch" and coordinate == ("/do", 1):
                snapshot["data"] = request.values
            packet["snapshot"] = _json_identity(snapshot)
            bindings = {
                "version": 4,
                "state_digest": "",
                "receipts": [asdict(item) for item in target_receipts],
                "scopes": scopes,
                "lineage": request.request_digest,
            }
            packet["receipt_bindings"] = _json_identity(bindings)
            bindings["state_digest"] = LangGraphScopeExecutor._state_digest(packet)
            packet["receipt_bindings"] = _json_identity(bindings)
            visiting.remove(coordinate)
            staged[coordinate] = packet
            return packet

        packets = tuple(
            NativeForkPacket(
                item.reference,
                item.occurrence,
                _json_identity(stage((item.reference, item.occurrence))),
            )
            for item in preflight.snapshots
        )
        return NativeForkProposal(preflight, packets), dependencies
    except Exception:
        raise lineage_error("replay_staging_unavailable") from None


def stage_root_replay(
    services: RuntimeServices,
    plan: WorkflowPlan,
    preflight: NativeLineagePreflight,
    target: ExecutionHandle,
) -> tuple[NativeForkProposal, tuple[ReceiptDependency, ...]]:
    return _stage_root_lineage(services, plan, preflight, target, "replay")


def stage_root_branch(
    services: RuntimeServices,
    plan: WorkflowPlan,
    preflight: NativeLineagePreflight,
    target: ExecutionHandle,
) -> tuple[NativeForkProposal, tuple[ReceiptDependency, ...]]:
    """Stage a private attributed branch; this does not admit or run it."""
    return _stage_root_lineage(services, plan, preflight, target, "branch")
