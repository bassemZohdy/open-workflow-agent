"""Prepare narrowly bounded root-only native lineage projections."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from open_workflow_agent.config import NativeStateConfig
from open_workflow_agent.effects import ReceiptDependency
from open_workflow_agent.execution_authorization import AUTHORITY_OPERATION
from open_workflow_agent.lineage_registry import RemappedReceipt, lineage_identity
from open_workflow_agent.native_lineage_policy import LineageKind
from open_workflow_agent.persistence import ExecutionHandle
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.state_lineage import lineage_error
from open_workflow_agent.workflow import ScopePlan, WorkflowPlan

from .graph import LangGraphScopeExecutor, _json_identity, _node_name
from .native_lineage_fork import NativeForkPacket, NativeForkProposal
from .native_lineage_inspection import (
    lineage_registry_for_target,
    selected_business_receipts,
    selected_control_receipts,
)
from .native_lineage_preflight import NativeLineagePreflight


def _forward_nested_scope(definition: dict[str, Any]) -> bool:
    """Allow child routes that stay forward inside their own scope."""
    body = definition.get("do")
    if not isinstance(body, list) or not body:
        return False
    names = {
        next(iter(entry)): index
        for index, entry in enumerate(body)
        if isinstance(entry, dict) and len(entry) == 1
    }
    if len(names) != len(body):
        return False
    for index, entry in enumerate(body):
        if not isinstance(entry, dict) or len(entry) != 1:
            return False
        child = next(iter(entry.values()))
        if not isinstance(child, dict):
            return False
        transitions = [child.get("then")]
        if "switch" in child:
            cases = child["switch"]
            if not isinstance(cases, list):
                return False
            for entry in cases:
                if not isinstance(entry, dict) or len(entry) != 1:
                    return False
                case = next(iter(entry.values()))
                if not isinstance(case, dict):
                    return False
                transitions.append(case.get("then"))
        for transition in transitions:
            if transition in (None, "continue"):
                continue
            if not isinstance(transition, str) or transition not in names:
                return False
            if names[transition] <= index:
                return False
        if "do" in child:
            if not _forward_nested_scope(child):
                return False
        elif "fork" in child:
            if not _forward_fanout(child):
                return False
        elif not any(operation in child for operation in ("set", "call", "switch")):
            return False
    return True


def _forward_fanout(definition: dict[str, Any]) -> bool:
    """Only completed non-competing fan-outs with non-routing branches are safe."""
    fork = definition.get("fork")
    if not isinstance(fork, dict) or fork.get("compete", False):
        return False
    branches = fork.get("branches")
    if not isinstance(branches, list) or not 1 <= len(branches) <= 64:
        return False
    for entry in branches:
        if not isinstance(entry, dict) or len(entry) != 1:
            return False
        branch = next(iter(entry.values()))
        if not isinstance(branch, dict) or branch.get("then") is not None or "switch" in branch:
            return False
    return _forward_nested_scope({"do": branches})


def _verify_child_route(
    scope: ScopePlan, packet: dict[str, Any], previous: dict[str, Any] | None, occurrence: int
) -> None:
    """Verify one completed occurrence from cumulative retained visit counters."""
    if occurrence < 1 or (occurrence > 1 and previous is None):
        raise lineage_error("replay_profile_unsupported")
    current_visits = json.loads(packet["snapshot"])["task_visits"]
    previous_visits = json.loads(previous["snapshot"])["task_visits"] if previous else {}
    visits: dict[str, int] = {}
    for task in scope.tasks:
        reference = task.execution_reference or task.reference
        current = current_visits.get(reference, 0)
        prior = previous_visits.get(reference, 0)
        if (
            type(current) is not int
            or type(prior) is not int
            or not 0 <= prior <= occurrence - 1
            or current - prior not in {0, 1}
        ):
            raise lineage_error("replay_profile_unsupported")
        if current > prior:
            visits[reference] = 1
    derived = dict(packet)
    derived["snapshot"] = json.dumps({"task_visits": visits})
    _scope_lineage_position(scope, derived, allow_complex_prefix=False)


def root_lineage_position(plan: WorkflowPlan, native: dict[str, Any]) -> int:
    """Identify the last root task in a prefix or a verifiable forward route."""
    return _scope_lineage_position(plan.scopes[0], native, allow_complex_prefix=True)


def _scope_lineage_position(
    scope: ScopePlan, native: dict[str, Any], *, allow_complex_prefix: bool
) -> int:
    """Verify the ordered route using only task visits and declared transitions."""
    tasks = scope.tasks
    visits = json.loads(native["snapshot"])["task_visits"]
    references = [task.execution_reference or task.reference for task in tasks]
    root_references = set(references)
    root_visits = {key: value for key, value in visits.items() if key in root_references}
    completed = len(root_visits)
    names = {task.name: index for index, task in enumerate(tasks)}
    if (
        allow_complex_prefix
        and 0 < completed <= len(tasks)
        and root_visits == {ref: 1 for ref in references[:completed]}
        and any(task.kind not in {"set", "call", "switch"} for task in tasks[:completed])
    ):
        for index, task in enumerate(tasks[:completed]):
            definition = task.definition
            transitions = [definition.get("then")]
            if task.kind == "switch":
                for case in definition["switch"]:
                    if not isinstance(case, dict) or len(case) != 1:
                        raise lineage_error("replay_profile_unsupported")
                    branch = next(iter(case.values()))
                    if not isinstance(branch, dict):
                        raise lineage_error("replay_profile_unsupported")
                    transitions.append(branch.get("then"))
            for transition in transitions:
                if transition in (None, "continue", "end", "exit"):
                    continue
                if (
                    not isinstance(transition, str)
                    or transition not in names
                    or names[transition] <= index
                ):
                    raise lineage_error("replay_profile_unsupported")
        next_node = native["next_node"]
        allowed = {"__end__", *(_node_name(task) for task in tasks[completed:])}
        if next_node not in allowed:
            raise lineage_error("replay_profile_unsupported")
        return completed
    if not root_visits or any(value != 1 for value in root_visits.values()):
        raise lineage_error("replay_profile_unsupported")
    cursor, reached = 0, set()
    while cursor < len(tasks):
        task = tasks[cursor]
        ref = references[cursor]
        if (
            ref not in root_visits
            or ref in reached
            or (
                task.kind not in {"set", "call", "switch"}
                and not (task.kind in {"do", "for"} and _forward_nested_scope(task.definition))
                and not (task.kind == "fork" and _forward_fanout(task.definition))
            )
        ):
            raise lineage_error("replay_profile_unsupported")
        reached.add(ref)
        transitions = [task.definition.get("then")]
        if task.kind == "switch":
            cases = task.definition.get("switch")
            if not isinstance(cases, list):
                raise lineage_error("replay_profile_unsupported")
            transitions = []
            has_default = False
            for entry in cases:
                if not isinstance(entry, dict) or len(entry) != 1:
                    raise lineage_error("replay_profile_unsupported")
                case = next(iter(entry.values()))
                if not isinstance(case, dict):
                    raise lineage_error("replay_profile_unsupported")
                has_default |= "when" not in case
                transitions.append(
                    case.get("then")
                    if case.get("then") is not None
                    else task.definition.get("then")
                )
            if not has_default:
                transitions.append(task.definition.get("then"))
        following = set()
        for transition in transitions:
            if transition is None or transition == "continue":
                destination = cursor + 1 if cursor + 1 < len(tasks) else None
            elif transition in {"end", "exit"}:
                destination = None
            elif isinstance(transition, str) and transition in names:
                destination = names[transition]
                if destination <= cursor:
                    raise lineage_error("replay_profile_unsupported")
            else:
                raise lineage_error("replay_profile_unsupported")
            following.add(destination)
        if reached == set(root_visits):
            expected = {
                "__end__" if item is None else _node_name(tasks[item]) for item in following
            }
            if native["next_node"] not in expected:
                raise lineage_error("replay_profile_unsupported")
            return cursor + 1
        remaining = [
            index for index in range(cursor + 1, len(tasks)) if references[index] in root_visits
        ]
        if not remaining or remaining[0] not in following:
            raise lineage_error("replay_profile_unsupported")
        cursor = remaining[0]
    raise lineage_error("replay_profile_unsupported")


def _validate_root_basis(
    services: RuntimeServices,
    plan: WorkflowPlan,
    preflight: NativeLineagePreflight,
    kind: LineageKind,
) -> tuple[dict[str, Any], tuple[ReceiptDependency, ...], int]:
    """Check the supported source profile without creating a target invocation.

    Timing policies remain excluded.
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
        if bindings.get("version") not in {2, 3, 4, 5}:
            raise lineage_error("replay_profile_unsupported")
        source_handle = services.invocations.get(request.source_invocation_id)
        if source_handle is None:
            raise lineage_error("source_unavailable")
        configured = (
            services.config.native_state_replay
            if kind == "replay"
            else services.config.native_state_branch
        )
        lineage = lineage_registry_for_target(
            services, source_handle, configured.bounds if configured else NativeStateConfig()
        )
        inherited = None
        parent = ()
        if lineage is not None:
            lineage.verify_source_for_new_target(source_handle, request.target_invocation_id)
            _, mappings, _ = lineage.read(source_handle)
            inherited = {item.target.key: item for item in mappings}
            marker = lineage_identity(source_handle)
            receipt = services.effect_journal.get(marker)
            if receipt is None:
                raise lineage_error("source_lineage_binding_missing")
            parent = (ReceiptDependency.capture(marker, receipt),)
        dependencies = (
            selected_business_receipts(preflight, services.effect_journal, inherited)
            + selected_control_receipts(preflight, services.effect_journal)
            + parent
        )
        if len(dependencies) > 64:
            raise lineage_error("replay_dependency_unsupported")
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
        tasks = plan.scopes[0].tasks
        completed = root_lineage_position(plan, native)
        coordinates = {(item.reference, item.occurrence) for item in preflight.snapshots}
        packets = {
            (item.reference, item.occurrence): json.loads(item.native_json)
            for item in preflight.snapshots
        }
        for item in preflight.snapshots[1:]:
            scope = next(scope for scope in plan.scopes if scope.reference == item.reference)
            packet = packets[(item.reference, item.occurrence)]
            child_bindings = json.loads(packet["receipt_bindings"])
            child_visits = json.loads(packet["snapshot"])["task_visits"]
            all_visited = all(
                child_visits.get(task.execution_reference or task.reference) == item.occurrence
                for task in scope.tasks
            )
            if (
                item.phase != "checkpoint"
                or packet["next_node"] != "__end__"
                or child_bindings.get("version") not in {2, 4}
                or not scope.tasks
                or any(
                    (entry["reference"], entry["occurrence"]) not in coordinates
                    for entry in child_bindings["scopes"]
                )
            ):
                raise lineage_error("replay_profile_unsupported")
            if not all_visited:
                _verify_child_route(
                    scope,
                    packet,
                    packets.get((item.reference, item.occurrence - 1)),
                    item.occurrence,
                )
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
