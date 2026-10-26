"""Authorized resolution commits only an audit and reconciled common outcome."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest
from open_workflow_agent.effects import EffectJournal, _receipt_payload
from open_workflow_agent.errors import OwaError, WorkflowExecutionError
from open_workflow_agent.execution_authorization import ExecutionAuthorizationStore
from open_workflow_agent.persistence import InvocationStore
from open_workflow_agent.recovery_definitions import RecoveryDefinitionArchive
from open_workflow_agent.security import Principal
from open_workflow_agent.state_update_resolution import (
    StateUpdateResolution,
    resolution_identity,
    resolution_receipt,
)
from open_workflow_agent.state_updates import TABLE, StateUpdateProof, StateUpdates
from test_state_updates import authority_tree as _authority_tree
from test_state_updates import controls as _controls
from test_state_updates import phase

authority_tree = _authority_tree
controls = _controls


def choice(committed=False, **changes):
    return StateUpdateResolution(
        **(
            {
                "operation_id": "resolve-1",
                "principal": "alice",
                "decision": "complete" if committed else "abandon_unknown",
                "acknowledge_unknown": not committed,
            }
            | changes
        )
    )


async def uncertain(controls, committed=False):
    coordinator, handle, request, evidence, actor, *_ = controls
    evidence.fail = "after" if committed else "before"
    with pytest.raises(OwaError):
        await coordinator.update(handle, request, actor)
    assert phase(coordinator, request) == "dispatched"
    return coordinator, handle, request, evidence, actor


@pytest.mark.parametrize(
    "changes",
    [
        {"operation_id": ""},
        {"operation_id": "../x"},
        {"operation_id": "x" * 129},
        {"principal": ""},
        {"principal": "x\n"},
        {"principal": "雪" * 86},
        {"decision": "retry"},
        {"decision": []},
        {"acknowledge_unknown": 1},
        {"acknowledge_unknown": False},
        {"decision": "complete", "acknowledge_unknown": True},
    ],
)
def test_resolution_requires_a_finite_explicit_choice(changes):
    with pytest.raises(ValueError):
        choice(**changes)


def test_descriptor_is_versioned_exact_and_copied():
    decision = choice()
    assert StateUpdateResolution.from_descriptor(decision.descriptor) == decision
    raw = decision.descriptor
    raw["principal"] = "bob"
    assert decision.principal == "alice"
    for changes in ({"profile": "v2"}, {"extra": True}):
        with pytest.raises(ValueError):
            StateUpdateResolution.from_descriptor(decision.descriptor | changes)


@pytest.mark.asyncio
@pytest.mark.parametrize("committed", [False, True])
async def test_resolution_survives_reopen_without_any_native_redispatch(controls, committed):
    coordinator, handle, request, evidence, actor = await uncertain(controls, committed)
    audit = await coordinator.resolve(handle, request, choice(committed), actor)
    assert phase(coordinator, request) == ("resolved_complete" if committed else "abandoned")
    assert audit.output["resolution"] == choice(committed).descriptor
    assert await coordinator.resolve(handle, request, choice(committed), actor) == audit
    invocations = InvocationStore(coordinator._test_database)
    journal = EffectJournal(coordinator._test_database, invocations=invocations)
    archive = RecoveryDefinitionArchive(journal, invocations)
    auth = ExecutionAuthorizationStore(
        journal,
        invocations,
        archive,
        config_provider=coordinator.authorizations.config_provider,
        security_provider=coordinator.authorizations.security_provider,
    )
    restored = StateUpdates(journal, invocations, auth)
    restored.register(evidence.backend())
    try:
        saved = invocations.get(handle.invocation_id)
        saved._execution_lease = handle._execution_lease
        assert await restored.resolve(saved, request, choice(committed), actor) == audit
        journal.verify_invocation(handle.invocation_id, handle.workflow_fingerprint)
        if committed:
            assert await restored.update(saved, request, actor) == evidence.inspect(request)
        else:
            with pytest.raises(OwaError) as failure:
                await restored.update(saved, request, actor)
            assert failure.value.details["outcome"] == "unknown"
        assert evidence.calls == [request.identity.key]
    finally:
        journal.close()
        invocations.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("committed", [False, True])
async def test_decision_must_match_native_evidence_and_is_immutable(controls, committed):
    coordinator, handle, request, evidence, actor = await uncertain(controls, committed)
    with pytest.raises(OwaError):
        await coordinator.resolve(handle, request, choice(not committed), actor)
    assert phase(coordinator, request) == "dispatched"
    assert coordinator.journal.get(resolution_identity(request.identity)) is None
    await coordinator.resolve(handle, request, choice(committed), actor)
    assert evidence.calls == [request.identity.key]


@pytest.mark.asyncio
@pytest.mark.parametrize("committed", [False, True])
async def test_committed_choice_cannot_change_its_id_or_outcome(controls, committed):
    coordinator, handle, request, evidence, actor = await uncertain(controls, committed)
    await coordinator.resolve(handle, request, choice(committed), actor)
    for changed in (choice(not committed), choice(committed, operation_id="other")):
        with pytest.raises(OwaError):
            await coordinator.resolve(handle, request, changed, actor)
    assert evidence.calls == [request.identity.key]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["principal", "request", "lease", "cancelled", "policy"])
async def test_resolution_requires_current_original_owner_request_and_permission(controls, change):
    coordinator, handle, request, evidence, actor = await uncertain(controls)
    decision = choice()
    if change == "principal":
        decision = replace(decision, principal="bob")
    elif change == "request":
        request = replace(request, values_json='{"changed":true}')
    elif change == "lease":
        coordinator._test_ownership.release(handle._execution_lease)
    elif change == "cancelled":
        coordinator.invocations.update(handle, status="cancelled")
    else:
        coordinator.authorizations.config_provider().policy.rules[0].actions.remove("state.resolve")
    with pytest.raises(OwaError):
        await coordinator.resolve(handle, request, decision, actor)
    assert coordinator.journal.get(resolution_identity(request.identity)) is None
    assert evidence.calls == [controls[2].identity.key]


@pytest.mark.asyncio
@pytest.mark.parametrize("controls", ["without-resolution-permission"], indirect=True)
async def test_new_policy_permission_does_not_replace_retained_original_permission(controls):
    coordinator, handle, request, _evidence, actor = await uncertain(controls)
    coordinator.authorizations.config_provider().policy.rules[0].actions.append("state.resolve")
    with pytest.raises(OwaError):
        await coordinator.resolve(handle, request, choice(), actor)
    assert coordinator.journal.get(resolution_identity(request.identity)) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("committed", [False, True])
async def test_resolution_outcome_and_audit_roll_back_together(controls, monkeypatch, committed):
    coordinator, handle, request, _evidence, actor = await uncertain(controls, committed)
    original = coordinator._finish

    def interrupted(*args):
        original(*args)
        raise RuntimeError("interrupted before common commit")

    with monkeypatch.context() as patch:
        patch.setattr(coordinator, "_finish", interrupted)
        with pytest.raises(OwaError):
            await coordinator.resolve(handle, request, choice(committed), actor)
    assert phase(coordinator, request) == "dispatched"
    assert coordinator.journal.get(request.identity).status == "pending"
    assert coordinator.journal.get(resolution_identity(request.identity)) is None
    await coordinator.resolve(handle, request, choice(committed), actor)


@pytest.mark.asyncio
async def test_concurrent_duplicate_resolution_is_one_choice(controls):
    coordinator, handle, request, evidence, actor = await uncertain(controls)
    results = await asyncio.gather(
        *(coordinator.resolve(handle, request, choice(), actor) for _ in range(3))
    )
    assert results == [results[0]] * 3
    coordinator.journal.verify_invocation(handle.invocation_id, handle.workflow_fingerprint)
    assert evidence.calls == [request.identity.key]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage", ["audit", "index", "phase", "payload", "permission", "authorization"]
)
async def test_damaged_resolution_blocks_common_recovery_and_retry(controls, damage):
    coordinator, handle, request, evidence, actor = await uncertain(controls)
    await coordinator.resolve(handle, request, choice(), actor)
    selected = resolution_identity(request.identity)
    if damage in {"audit", "index"}:
        table = "workflow_effects" if damage == "audit" else "workflow_effect_invocations"
        coordinator.connection.execute(f"DELETE FROM {table} WHERE identity=?", (selected.key,))
    elif damage == "phase":
        coordinator.connection.execute(
            f"UPDATE {TABLE} SET phase='completed' WHERE identity=?", (request.identity.key,)
        )
    elif damage == "permission":
        coordinator.connection.execute(
            "DELETE FROM execution_authorization_decisions WHERE business_key=?", (selected.key,)
        )
    elif damage == "authorization":
        forged = resolution_receipt(request, choice(), "0" * 64)
        coordinator.connection.execute(
            "UPDATE workflow_effects SET payload=? WHERE identity=?",
            (json.dumps(_receipt_payload(forged)), selected.key),
        )
    else:
        coordinator.connection.execute(
            "UPDATE workflow_effects SET payload='{}' WHERE identity=?", (selected.key,)
        )
    coordinator.connection.commit()
    with pytest.raises(WorkflowExecutionError):
        coordinator.journal.verify_invocation(handle.invocation_id, handle.workflow_fingerprint)
    with pytest.raises(OwaError):
        await coordinator.update(handle, request, actor)
    assert evidence.calls == [request.identity.key]


@pytest.mark.asyncio
@pytest.mark.parametrize("different", [False, True])
async def test_resolution_rejects_absent_or_substituted_caller(controls, different):
    coordinator, handle, request, _evidence, actor = await uncertain(controls)
    caller = replace(actor, principal=Principal("bob")) if different else None
    with pytest.raises(OwaError):
        await coordinator.resolve(handle, request, choice(), caller)
    assert coordinator.journal.get(resolution_identity(request.identity)) is None


@pytest.mark.asyncio
async def test_prepared_control_cannot_be_resolved_and_remains_dispatchable(controls, monkeypatch):
    coordinator, handle, request, evidence, actor, *_ = controls
    original = type(coordinator.connection).execute

    def interrupted(connection, statement, parameters=()):
        if connection is coordinator.connection and "SET phase='dispatched'" in statement:
            raise RuntimeError("stopped before dispatch")
        return original(connection, statement, parameters)

    with monkeypatch.context() as patch:
        patch.setattr(type(coordinator.connection), "execute", interrupted)
        with pytest.raises(OwaError):
            await coordinator.update(handle, request, actor)
    assert phase(coordinator, request) == "prepared"
    with pytest.raises(OwaError):
        await coordinator.resolve(handle, request, choice(), actor)
    assert coordinator.journal.get(resolution_identity(request.identity)) is None
    await coordinator.update(handle, request, actor)
    assert evidence.calls == [request.identity.key]


@pytest.mark.asyncio
async def test_abandonment_keeps_unknown_outcome_even_if_matching_proof_arrives_later(controls):
    coordinator, handle, request, evidence, actor = await uncertain(controls)
    audit = await coordinator.resolve(handle, request, choice(), actor)
    evidence.markers[request.identity.key] = StateUpdateProof(
        request.request_digest, request.base_reference, "f" * 64
    )
    coordinator.journal.verify_invocation(handle.invocation_id, handle.workflow_fingerprint)
    assert await coordinator.resolve(handle, request, choice(), actor) == audit
    with pytest.raises(OwaError) as failure:
        await coordinator.update(handle, request, actor)
    assert failure.value.details["outcome"] == "unknown"
    with pytest.raises(OwaError):
        coordinator.attested_ids(handle.invocation_id, handle.workflow_fingerprint)
    assert evidence.calls == [request.identity.key]


@pytest.mark.asyncio
async def test_unadmitted_resolution_creates_no_control_or_decision_audit(controls):
    coordinator, handle, request, evidence, actor, *_ = controls
    with pytest.raises(OwaError):
        await coordinator.resolve(handle, request, choice(), actor)
    assert coordinator.journal.get(request.identity) is None
    assert coordinator.journal.get(resolution_identity(request.identity)) is None
    assert evidence.calls == []
