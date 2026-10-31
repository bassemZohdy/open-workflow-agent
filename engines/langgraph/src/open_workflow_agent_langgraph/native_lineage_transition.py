"""Prepare a narrowly bounded root-only native replay projection."""

from __future__ import annotations

import json
from dataclasses import asdict

from open_workflow_agent.effects import ReceiptDependency
from open_workflow_agent.execution_authorization import AUTHORITY_OPERATION
from open_workflow_agent.lineage_registry import RemappedReceipt
from open_workflow_agent.persistence import ExecutionHandle
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.state_lineage import lineage_error
from open_workflow_agent.workflow import WorkflowPlan

from .graph import LangGraphScopeExecutor, _json_identity
from .native_lineage_fork import NativeForkPacket, NativeForkProposal
from .native_lineage_inspection import selected_business_receipts
from .native_lineage_preflight import NativeLineagePreflight


def stage_root_replay(
    services: RuntimeServices,
    plan: WorkflowPlan,
    preflight: NativeLineagePreflight,
    target: ExecutionHandle,
) -> tuple[NativeForkProposal, tuple[ReceiptDependency, ...]]:
    """Stage a root checkpoint; native proof and common binding must follow.

    This first profile excludes native channels, timing, completed nested scopes,
    state controls and native routing extensions until their remapping is proven.
    """
    try:
        request = preflight.request
        preflight.verify_basis(preflight.input_json, preflight.snapshots)
        if (
            request.kind != "replay"
            or request.target_invocation_id != target.invocation_id
            or request.workflow_fingerprint != plan.fingerprint
            or target.workflow_fingerprint != plan.fingerprint
            or target._execution_lease is None
            or plan.native_state_replay is None
            or plan.native_state_replay.digest != request.policy_digest
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
        authority = services.execution_authorizations.authority_receipt(target)
        if authority is None:
            raise lineage_error("original_authority_required")
        target_receipts = [ReceiptDependency.capture(authority.identity, authority.receipt)]
        target_receipts.extend(
            ReceiptDependency(RemappedReceipt.capture(request, item).target, item.receipt_digest)
            for item in dependencies
        )
        snapshot = json.loads(native["snapshot"])
        if not snapshot["task_visits"]:
            raise lineage_error("replay_profile_unsupported")
        snapshot["variables"]["workflow"]["id"] = target.invocation_id
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
