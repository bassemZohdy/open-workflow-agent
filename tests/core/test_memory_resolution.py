"""Explicit resolution records a decision without blindly resending a write."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import replace

import pytest
from open_workflow_agent import memory_writes as module
from open_workflow_agent.effects import EffectJournal, ReceiptDependency
from open_workflow_agent.errors import InvocationStateError, MemoryError, WorkflowExecutionError
from open_workflow_agent.memory_mutations import MemoryMutationMarker
from open_workflow_agent.memory_resolution import MemoryWriteResolution, resolution_identity
from open_workflow_agent.memory_writes import TABLE, MemoryWrites
from open_workflow_agent.persistence import InvocationStore
from test_memory_writes import phase
from test_memory_writes import writes as _writes_fixture

writes = _writes_fixture


def choice(decision="abandon_unknown", **kwargs):
    return MemoryWriteResolution(
        "resolution-1", "alice", decision, decision == "abandon_unknown", **kwargs
    )


async def uncertain(writes, *, committed=False):
    coordinator, handle, mutation, evidence, *_ = writes
    evidence.fail = "after" if committed else "before"
    with pytest.raises(InvocationStateError):
        await coordinator.mutate(handle, mutation)
    return coordinator, handle, mutation, evidence


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
def test_resolution_requires_finite_explicit_acknowledgement(changes):
    value = dict(
        operation_id="resolution-1",
        principal="alice",
        decision="abandon_unknown",
        acknowledge_unknown=True,
    )
    with pytest.raises(ValueError):
        MemoryWriteResolution(**(value | changes))


def test_descriptor_is_exact_versioned_and_copied():
    decision = choice()
    assert MemoryWriteResolution.from_descriptor(decision.descriptor) == decision
    raw = decision.descriptor
    raw["principal"] = "bob"
    assert decision.principal == "alice"
    for changes in ({"profile": "v2"}, {"extra": True}):
        with pytest.raises(ValueError):
            MemoryWriteResolution.from_descriptor(decision.descriptor | changes)


@pytest.mark.asyncio
@pytest.mark.parametrize("committed", [False, True])
async def test_resolution_persists_across_reopen_and_never_dispatches(writes, committed):
    coordinator, handle, mutation, evidence = await uncertain(writes, committed=committed)
    decision = choice("complete" if committed else "abandon_unknown")
    audit = await coordinator.resolve(handle, mutation, decision)
    assert audit.status == "completed"
    assert audit.output["resolution"] == decision.descriptor
    assert phase(coordinator, mutation) == ("resolved_complete" if committed else "abandoned")
    assert await coordinator.resolve(handle, mutation, decision) == audit
    invocations = InvocationStore(writes[4])
    journal = EffectJournal(writes[4], invocations=invocations)
    restored = MemoryWrites(journal, invocations)
    restored.register(evidence.backend())
    try:
        saved = invocations.get(handle.invocation_id)
        saved._execution_lease = handle._execution_lease
        assert await restored.resolve(saved, mutation, decision) == audit
        journal.verify_invocation(handle.invocation_id, handle.workflow_fingerprint)
        observed = []

        def observer(identity, receipt):
            observed.append(ReceiptDependency.capture(identity, receipt))

        if committed:
            await restored.mutate(saved, mutation, observer=observer)
        else:
            with pytest.raises(MemoryError) as error:
                await restored.mutate(saved, mutation, observer=observer)
            assert error.value.details["outcome"] == "unknown"
        assert len(observed) == 1
        assert evidence.calls == [mutation.identity.key]
    finally:
        journal.close()
        invocations.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("committed", [False, True])
async def test_decision_must_match_evidence_and_cannot_change(writes, committed):
    coordinator, handle, mutation, evidence = await uncertain(writes, committed=committed)
    wrong = choice("abandon_unknown" if committed else "complete")
    with pytest.raises(InvocationStateError):
        await coordinator.resolve(handle, mutation, wrong)
    assert phase(coordinator, mutation) == "dispatched"
    assert coordinator.journal.get(resolution_identity(mutation.identity)) is None
    right = choice("complete" if committed else "abandon_unknown")
    await coordinator.resolve(handle, mutation, right)
    with pytest.raises(InvocationStateError):
        await coordinator.resolve(handle, mutation, replace(right, operation_id="different"))
    with pytest.raises(InvocationStateError):
        await coordinator.resolve(handle, mutation, wrong)
    assert evidence.calls == [mutation.identity.key]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["principal", "request", "lease", "cancelled"])
async def test_resolution_requires_original_request_principal_and_live_running_owner(
    writes, change
):
    coordinator, handle, mutation, _evidence = await uncertain(writes)
    decision = choice()
    if change == "principal":
        decision = replace(decision, principal="bob")
    elif change == "request":
        mutation = replace(mutation, value_json='{"changed":true}')
    elif change == "lease":
        writes[5].release(handle._execution_lease)
    else:
        # Persist through the store's existing terminal transition.
        coordinator.invocations.update(handle, status="cancelled")
    with pytest.raises(InvocationStateError):
        await coordinator.resolve(handle, mutation, decision)
    assert coordinator.journal.get(resolution_identity(mutation.identity)) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("committed", [False, True])
async def test_resolution_outcome_and_audit_roll_back_together(writes, monkeypatch, committed):
    coordinator, handle, mutation, _evidence = await uncertain(writes, committed=committed)
    original = coordinator._finish

    def failed(*args):
        original(*args)
        raise RuntimeError("failure before transaction commit")

    with monkeypatch.context() as patch:
        patch.setattr(coordinator, "_finish", failed)
        with pytest.raises(RuntimeError):
            await coordinator.resolve(
                handle, mutation, choice("complete" if committed else "abandon_unknown")
            )
    assert phase(coordinator, mutation) == "dispatched"
    assert coordinator.journal.get(mutation.identity).status == "pending"
    assert coordinator.journal.get(resolution_identity(mutation.identity)) is None
    await coordinator.resolve(
        handle, mutation, choice("complete" if committed else "abandon_unknown")
    )


@pytest.mark.asyncio
async def test_concurrent_resolution_is_one_immutable_choice(writes):
    coordinator, handle, mutation, evidence = await uncertain(writes)
    results = await asyncio.gather(
        *(coordinator.resolve(handle, mutation, choice()) for _ in range(3))
    )
    assert results == [results[0]] * 3
    coordinator.journal.verify_invocation(handle.invocation_id, handle.workflow_fingerprint)
    assert evidence.calls == [mutation.identity.key]


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["audit", "index", "phase", "payload"])
async def test_damaged_resolution_fails_recovery_and_cached_write(writes, damage):
    coordinator, handle, mutation, evidence = await uncertain(writes)
    await coordinator.resolve(handle, mutation, choice())
    identity = resolution_identity(mutation.identity)
    if damage in {"audit", "index"}:
        table = "workflow_effects" if damage == "audit" else "workflow_effect_invocations"
        coordinator.connection.execute(f"DELETE FROM {table} WHERE identity=?", (identity.key,))
    elif damage == "phase":
        coordinator.connection.execute(
            f"UPDATE {TABLE} SET phase='completed' WHERE identity=?", (mutation.identity.key,)
        )
    else:
        coordinator.connection.execute(
            "UPDATE workflow_effects SET payload='{}' WHERE identity=?", (identity.key,)
        )
    coordinator.connection.commit()
    with pytest.raises(WorkflowExecutionError):
        coordinator.journal.verify_invocation(handle.invocation_id, handle.workflow_fingerprint)
    with pytest.raises((InvocationStateError, WorkflowExecutionError)):
        await coordinator.mutate(handle, mutation)
    assert evidence.calls == [mutation.identity.key]


@pytest.mark.asyncio
async def test_abandonment_never_claims_that_late_effect_was_rolled_back(writes):
    coordinator, handle, mutation, evidence = await uncertain(writes)
    await coordinator.resolve(handle, mutation, choice())
    # A late matching proof cannot turn an acknowledged unknown into a promise
    # of nonexecution, or silently change the immutable operator decision.
    evidence.markers[mutation.identity.key] = MemoryMutationMarker.for_request(mutation)
    coordinator.journal.verify_invocation(handle.invocation_id, handle.workflow_fingerprint)
    with pytest.raises(MemoryError) as error:
        await coordinator.mutate(handle, mutation)
    assert error.value.details["outcome"] == "unknown"
    assert evidence.calls == [mutation.identity.key]


@pytest.mark.asyncio
async def test_nonadmitted_resolution_does_not_create_an_intent(writes):
    coordinator, handle, mutation, evidence, *_ = writes
    with pytest.raises(InvocationStateError):
        await coordinator.resolve(handle, mutation, choice())
    assert coordinator.journal.get(mutation.identity) is None
    assert coordinator.journal.get(resolution_identity(mutation.identity)) is None
    assert evidence.calls == []


@pytest.mark.asyncio
async def test_prepared_write_is_not_resolved_and_remains_normally_dispatchable(
    writes, monkeypatch
):
    coordinator, handle, mutation, evidence, *_ = writes
    original = module.owned_write_transaction
    transactions = []

    @contextmanager
    def stop_before_dispatch(*args, **kwargs):
        transactions.append(True)
        if len(transactions) == 2:
            raise RuntimeError("stop before dispatch")
        with original(*args, **kwargs):
            yield

    with monkeypatch.context() as patch:
        patch.setattr(module, "owned_write_transaction", stop_before_dispatch)
        with pytest.raises(RuntimeError):
            await coordinator.mutate(handle, mutation)
    with pytest.raises(InvocationStateError):
        await coordinator.resolve(handle, mutation, choice())
    assert phase(coordinator, mutation) == "prepared"
    assert evidence.calls == []
    await coordinator.mutate(handle, mutation)
    assert evidence.calls == [mutation.identity.key]


@pytest.mark.asyncio
async def test_automatic_completion_cannot_be_rewritten_as_a_resolution(writes):
    coordinator, handle, mutation, evidence, *_ = writes
    await coordinator.mutate(handle, mutation)
    receipt = coordinator.journal.get(mutation.identity)
    with pytest.raises(InvocationStateError):
        await coordinator.resolve(handle, mutation, choice("complete"))
    assert coordinator.journal.get(resolution_identity(mutation.identity)) is None
    assert coordinator.journal.get(mutation.identity) == receipt
    assert evidence.calls == [mutation.identity.key]


@pytest.mark.asyncio
async def test_lease_expiry_at_final_guard_rolls_back_resolution_and_outcome(writes, monkeypatch):
    coordinator, handle, mutation, _evidence = await uncertain(writes)
    original = coordinator._finish

    def expire(*args):
        coordinator.connection.execute(
            "UPDATE invocation_ownership SET expires_at=1 WHERE invocation_id=?",
            (handle.invocation_id,),
        )
        original(*args)

    with monkeypatch.context() as patch:
        patch.setattr(coordinator, "_finish", expire)
        with pytest.raises(InvocationStateError):
            await coordinator.resolve(handle, mutation, choice())
    assert phase(coordinator, mutation) == "dispatched"
    assert coordinator.journal.get(mutation.identity).status == "pending"
    assert coordinator.journal.get(resolution_identity(mutation.identity)) is None
    await coordinator.resolve(handle, mutation, choice())
