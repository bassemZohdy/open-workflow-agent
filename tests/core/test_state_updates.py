"""Original-owner control audit and conservative recovery on real common stores."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from uuid import uuid4

import pytest
from open_workflow_agent import state_updates as module
from open_workflow_agent.effects import EffectJournal, _receipt_payload
from open_workflow_agent.errors import InvocationStateError, OwaError, WorkflowExecutionError
from open_workflow_agent.execution_authority import authority_scope
from open_workflow_agent.execution_authorization import ExecutionAuthorizationStore
from open_workflow_agent.persistence import InvocationStore
from open_workflow_agent.recovery_definitions import RecoveryDefinitionArchive
from open_workflow_agent.state_updates import (
    TABLE,
    StateUpdateBackend,
    StateUpdateProof,
    StateUpdateRequest,
    StateUpdates,
)
from test_execution_authorization import authority_tree as _authority_tree

authority_tree = _authority_tree


class Evidence:
    def __init__(self):
        self.markers = {}
        self.calls = []
        self.fail = None

    def inspect(self, request):
        return self.markers.get(request.identity.key)

    async def apply(self, request, handle):
        self.calls.append(request.identity.key)
        if self.fail == "before":
            raise RuntimeError("private driver failure before native commit")
        proof = StateUpdateProof(request.request_digest, request.base_reference, "f" * 64)
        self.markers[request.identity.key] = proof
        if self.fail == "after":
            raise RuntimeError("native commit without common acknowledgement")
        return proof

    def backend(self):
        return StateUpdateBackend("d" * 64, self.apply, self.inspect)


@pytest.fixture
def controls(authority_tree):
    services, archive, auth, plan, handle, authority, _ = authority_tree
    rule = services.config.execution_authorization.policy.rules[0]
    rule.actions.append("state.update")
    rule.resources.append("*")
    auth.reserve_invocation(handle, authority, input_data={"initial": True})
    with authority_scope(authority):
        archive.admit(plan, handle, {"initial": True}, execution_lease=handle._execution_lease)
    coordinator = StateUpdates(services.effect_journal, services.invocations, auth)
    coordinator._test_database = services.execution_ownership._database
    evidence = Evidence()
    coordinator.register(evidence.backend())
    request = StateUpdateRequest.create(
        invocation_id=handle.invocation_id,
        workflow_fingerprint=plan.fingerprint,
        request_id=uuid4().hex * 2,
        base_reference="a" * 64,
        task_reference="/do/0/action",
        values={"value": 2},
        policy_digest="c" * 64,
        storage_identity="d" * 64,
    )
    return coordinator, handle, request, evidence, authority, archive


def phase(coordinator, request):
    return coordinator.connection.execute(
        f"SELECT phase FROM {TABLE} WHERE identity=?", (request.identity.key,)
    ).fetchone()[0]


@pytest.mark.parametrize("values", [{"x": float("nan")}, {"x": b"bytes"}, {"x": "x" * 65536}])
def test_values_are_finite_and_bounded(values):
    with pytest.raises(OwaError):
        StateUpdateRequest.create(
            invocation_id="id",
            workflow_fingerprint="fp",
            request_id="1" * 64,
            base_reference="2" * 64,
            task_reference="/do/0/set",
            values=values,
            policy_digest="3" * 64,
            storage_identity="4" * 64,
        )


def test_immutable_request_and_control_identity_do_not_alias_attribution_changes(controls):
    request = controls[2]
    assert StateUpdateRequest.from_descriptor(request.descriptor) == request
    changed = replace(request, task_reference="/do/1/other")
    assert changed.identity == request.identity and changed.request_digest != request.request_digest
    copied = request.values
    copied["value"] = 99
    assert request.values == {"value": 2}
    with pytest.raises(OwaError):
        StateUpdateRequest.from_descriptor(request.descriptor | {"native_checkpoint": "private"})


@pytest.mark.asyncio
async def test_owned_completion_is_idempotent_and_globally_verified(controls):
    coordinator, handle, request, evidence, authority, _ = controls
    proof = await coordinator.update(handle, request, authority)
    assert await coordinator.update(handle, request, authority) == proof
    assert evidence.calls == [request.identity.key]
    assert phase(coordinator, request) == "completed"
    coordinator.journal.verify_invocation(handle.invocation_id, handle.workflow_fingerprint)
    with pytest.raises(OwaError):
        await coordinator.update(handle, replace(request, values_json='{"value":3}'), authority)
    assert evidence.calls == [request.identity.key]


@pytest.mark.asyncio
async def test_committed_native_proof_recovers_common_completion_without_redispatch(controls):
    coordinator, handle, request, evidence, authority, _ = controls
    evidence.fail = "after"
    with pytest.raises(InvocationStateError):
        await coordinator.update(handle, request, authority)
    assert phase(coordinator, request) == "dispatched"
    evidence.fail = None
    proof = await coordinator.update(handle, request, authority)
    assert proof == evidence.inspect(request)
    assert evidence.calls == [request.identity.key]
    assert phase(coordinator, request) == "completed"


@pytest.mark.asyncio
async def test_unknown_dispatched_outcome_blocks_retry_and_global_recovery(controls):
    coordinator, handle, request, evidence, authority, _ = controls
    evidence.fail = "before"
    with pytest.raises(InvocationStateError):
        await coordinator.update(handle, request, authority)
    with pytest.raises(InvocationStateError) as failure:
        await coordinator.update(handle, request, authority)
    assert failure.value.details["reason"] == "outcome_unknown"
    with pytest.raises(WorkflowExecutionError):
        coordinator.journal.verify_invocation(handle.invocation_id, handle.workflow_fingerprint)
    assert evidence.calls == [request.identity.key]


@pytest.mark.asyncio
async def test_reopen_uses_durable_intent_and_current_backend_proof(controls):
    coordinator, handle, request, evidence, authority, archive = controls
    evidence.fail = "after"
    with pytest.raises(InvocationStateError):
        await coordinator.update(handle, request, authority)
    database = coordinator._test_database
    invocations = InvocationStore(database)
    journal = EffectJournal(database, invocations=invocations)
    restored_archive = RecoveryDefinitionArchive(
        journal, invocations, profile_provider=archive.profile_provider
    )
    auth = ExecutionAuthorizationStore(
        journal,
        invocations,
        restored_archive,
        config_provider=coordinator.authorizations.config_provider,
        security_provider=coordinator.authorizations.security_provider,
    )
    restored = StateUpdates(journal, invocations, auth)
    restored.register(evidence.backend())
    try:
        saved = invocations.get(handle.invocation_id)
        saved._execution_lease = handle._execution_lease
        await restored.update(saved, request, authority)
        assert evidence.calls == [request.identity.key]
        journal.verify_invocation(handle.invocation_id, handle.workflow_fingerprint)
    finally:
        journal.close()
        invocations.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["proof", "intent", "index", "phase", "authorization"])
async def test_corrupt_or_missing_common_and_native_evidence_fails_closed(controls, damage):
    coordinator, handle, request, evidence, authority, _ = controls
    await coordinator.update(handle, request, authority)
    connection = coordinator.connection
    if damage == "proof":
        evidence.markers.clear()
    elif damage == "index":
        connection.execute(
            "DELETE FROM workflow_effect_invocations WHERE identity=?", (request.identity.key,)
        )
    elif damage == "authorization":
        connection.execute(
            f"UPDATE {TABLE} SET authorization_digest=? WHERE identity=?",
            ("0" * 64, request.identity.key),
        )
    elif damage == "phase":
        connection.execute(
            f"UPDATE {TABLE} SET phase='prepared' WHERE identity=?", (request.identity.key,)
        )
    else:
        connection.execute(f"DELETE FROM {TABLE} WHERE identity=?", (request.identity.key,))
    connection.commit()
    with pytest.raises(OwaError):
        await coordinator.update(handle, request, authority)
    with pytest.raises(WorkflowExecutionError):
        coordinator.journal.verify_invocation(handle.invocation_id, handle.workflow_fingerprint)
    assert evidence.calls == [request.identity.key]


@pytest.mark.asyncio
async def test_current_owner_and_current_policy_cannot_be_substituted(controls):
    coordinator, handle, request, evidence, authority, _ = controls
    with pytest.raises(OwaError):
        await coordinator.update(handle, request, None)
    coordinator.authorizations.config_provider().policy.rules[0].actions.remove("state.update")
    with pytest.raises(OwaError):
        await coordinator.update(handle, request, authority)
    assert evidence.calls == []


@pytest.mark.asyncio
async def test_self_consistent_request_rewrite_cannot_reuse_an_old_permission(controls):
    coordinator, handle, request, evidence, authority, _ = controls
    evidence.fail = "before"
    with pytest.raises(InvocationStateError):
        await coordinator.update(handle, request, authority)
    receipt = coordinator.journal.get(request.identity)
    changed = replace(request, values_json='{"value":99}')
    from open_workflow_agent.model_responses import bounded_model_json

    coordinator.connection.execute(
        f"UPDATE {TABLE} SET request_json=?,request_digest=? WHERE identity=?",
        (
            bounded_model_json(changed.descriptor, limit=131_072),
            changed.request_digest,
            request.identity.key,
        ),
    )
    coordinator.connection.execute(
        "UPDATE workflow_effects SET payload=? WHERE identity=?",
        (
            json.dumps(_receipt_payload(replace(receipt, request_digest=changed.request_digest))),
            request.identity.key,
        ),
    )
    coordinator.connection.commit()
    with pytest.raises(OwaError):
        coordinator.journal.get(request.identity)
    assert evidence.calls == [request.identity.key]


@pytest.mark.asyncio
async def test_cancelled_native_commit_is_reconciled_without_redispatch(controls):
    coordinator, handle, request, evidence, authority, _ = controls
    entered, finish = asyncio.Event(), asyncio.Event()

    async def committed(request, handle):
        proof = await evidence.apply(request, handle)
        entered.set()
        await finish.wait()
        return proof

    coordinator._backends[request.storage_identity] = StateUpdateBackend(
        request.storage_identity, committed, evidence.inspect
    )
    task = asyncio.create_task(coordinator.update(handle, request, authority))
    await asyncio.wait_for(entered.wait(), 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await coordinator.update(handle, request, authority)
    assert phase(coordinator, request) == "completed"
    assert evidence.calls == [request.identity.key]


@pytest.mark.asyncio
async def test_prepared_interruption_can_dispatch_once_after_retry(controls, monkeypatch):
    coordinator, handle, request, evidence, authority, _ = controls
    connection_type = type(coordinator.connection)
    original = connection_type.execute

    def interrupted(connection, statement, parameters=()):
        if connection is coordinator.connection and "SET phase='dispatched'" in statement:
            raise RuntimeError("interrupted before durable dispatch phase")
        return original(connection, statement, parameters)

    monkeypatch.setattr(connection_type, "execute", interrupted)
    with pytest.raises(OwaError):
        await coordinator.update(handle, request, authority)
    assert phase(coordinator, request) == "prepared" and evidence.calls == []
    coordinator.journal.verify_invocation(handle.invocation_id, handle.workflow_fingerprint)
    monkeypatch.setattr(connection_type, "execute", original)
    await coordinator.update(handle, request, authority)
    assert evidence.calls == [request.identity.key]


@pytest.mark.asyncio
async def test_common_limit_is_checked_before_a_second_native_dispatch(controls, monkeypatch):
    coordinator, handle, request, evidence, authority, _ = controls
    monkeypatch.setattr(module, "MAX_UPDATES", 1)
    await coordinator.update(handle, request, authority)
    with pytest.raises(InvocationStateError) as failure:
        await coordinator.update(handle, replace(request, request_id=uuid4().hex * 2), authority)
    assert failure.value.details["reason"] == "update_limit"
    assert evidence.calls == [request.identity.key]


@pytest.mark.asyncio
async def test_disabled_authorization_cannot_install_an_unprotected_control(controls):
    coordinator, handle, request, evidence, authority, _ = controls
    coordinator.authorizations.config_provider().enabled = False
    with pytest.raises(OwaError):
        await coordinator.update(handle, request, authority)
    assert evidence.calls == []
