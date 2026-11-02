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

    This first profile excludes native channels, timing, completed nested scopes,
    state controls and native routing extensions until their remapping is proven.
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
            or len(preflight.snapshots) != 1
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
        if bindings.get("version") != 2 or bindings.get("scopes"):
            raise lineage_error("replay_profile_unsupported")
        source_receipts = [ReceiptDependency.from_value(value) for value in bindings["receipts"]]
        dependencies = selected_business_receipts(preflight, services.effect_journal)
        business = {item.identity.key for item in dependencies}
        for item in source_receipts:
            operation = item.identity.operation
            if (
                item.identity.key not in business
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
        completed = len(visits)
        if not 0 < completed <= len(tasks) or visits != {
            (item.execution_reference or item.reference): 1 for item in tasks[:completed]
        }:
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
        native, dependencies, _ = _validate_root_basis(services, plan, preflight, kind)
        authority = services.execution_authorizations.authority_receipt(target)
        if authority is None:
            raise lineage_error("original_authority_required")
        target_receipts = [ReceiptDependency.capture(authority.identity, authority.receipt)]
        target_receipts.extend(
            ReceiptDependency(RemappedReceipt.capture(request, item).target, item.receipt_digest)
            for item in dependencies
        )
        snapshot = json.loads(native["snapshot"])
        snapshot["variables"]["workflow"]["id"] = target.invocation_id
        if kind == "branch":
            snapshot["data"] = request.values
        native["snapshot"] = _json_identity(snapshot)
        bindings = {
            "version": 4,
            "state_digest": "",
            "receipts": [asdict(item) for item in target_receipts],
            "scopes": [],
            "lineage": request.request_digest,
        }
        native["receipt_bindings"] = _json_identity(bindings)
        bindings["state_digest"] = LangGraphScopeExecutor._state_digest(native)
        native["receipt_bindings"] = _json_identity(bindings)
        return NativeForkProposal(
            preflight, (NativeForkPacket("/do", 1, _json_identity(native)),)
        ), dependencies
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
