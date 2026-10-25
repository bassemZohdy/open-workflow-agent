"""Common write recovery never blindly redispatches an uncertain mutation."""

from __future__ import annotations

import asyncio
import hashlib
import os
from contextlib import contextmanager
from dataclasses import replace

import pytest
from open_workflow_agent import memory_writes as module
from open_workflow_agent.effects import EffectIdentity, EffectJournal
from open_workflow_agent.errors import (
    InvocationStateConflict,
    InvocationStateError,
    WorkflowExecutionError,
)
from open_workflow_agent.memory_mutations import MAX_MUTATIONS, MemoryMutation, MemoryMutationMarker
from open_workflow_agent.memory_reads import MemoryReadBinding
from open_workflow_agent.memory_writes import TABLE, MemoryMutationBackend, MemoryWrites
from open_workflow_agent.ownership import ExecutionOwnershipStore
from open_workflow_agent.persistence import InvocationStore


class NativeEvidence:
    def __init__(self, binding):
        self.binding = binding
        self.markers = {}
        self.calls = []
        self.fail = None

    async def apply(self, request, handle):
        self.calls.append(request.identity.key)
        await asyncio.sleep(0)
        if self.fail == "before":
            raise RuntimeError("secret provider failure")
        marker = MemoryMutationMarker.for_request(request)
        self.markers[request.identity.key] = marker
        if self.fail == "after":
            raise RuntimeError("secret lost acknowledgement")
        return marker

    def backend(self, storage="a" * 64):
        return MemoryMutationBackend(
            self.binding,
            storage,
            self.apply,
            lambda request: self.markers.get(request.identity.key),
        )


@pytest.fixture(params=["sqlite", "postgresql"])
def writes(request, tmp_path):
    path = str(tmp_path / "common.sqlite3")
    if request.param == "postgresql":
        path = os.getenv("OWA_TEST_POSTGRES_URL")
        if not path:
            pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    invocations = InvocationStore(path)
    ownership = ExecutionOwnershipStore(path)
    journal = EffectJournal(path, invocations=invocations)
    coordinator = MemoryWrites(journal, invocations)
    handle = invocations.create(
        engine="test",
        session_id=None,
        user_id=None,
        owner_principal="alice",
        workflow_name="memory",
        workflow_version="1.0.0",
        workflow_fingerprint="definition",
    )
    handle._execution_lease = ownership.acquire(handle, seconds=300)
    binding = MemoryReadBinding("preferences", "1.0.0", request.param)
    evidence = NativeEvidence(binding)
    coordinator.register(evidence.backend())
    parent = EffectIdentity(
        handle.invocation_id,
        handle.workflow_fingerprint,
        "/do/write",
        (("/do/write", 1),),
        "call:agent",
    )
    mutation = MemoryMutation.put(
        parent,
        binding,
        "owa/principal-memory/v1:" + hashlib.sha256(b"alice").hexdigest(),
        "preferences",
        "record",
        {"version": 1},
    )
    try:
        yield coordinator, handle, mutation, evidence, path, ownership
    finally:
        journal.close()
        ownership.close()
        invocations.close()


def phase(coordinator, mutation):
    return coordinator.connection.execute(
        f"SELECT phase FROM {TABLE} WHERE identity=?", (mutation.identity.key,)
    ).fetchone()[0]


@pytest.mark.asyncio
async def test_concurrent_duplicates_observe_one_committed_common_completion(writes):
    coordinator, handle, mutation, evidence, *_ = writes
    observed = []

    async def observer(identity, receipt):
        assert coordinator.journal.get(identity) == receipt
        assert phase(coordinator, mutation) == "completed"
        observed.append(receipt)

    await asyncio.gather(
        *(coordinator.mutate(handle, mutation, observer=observer) for _ in range(3))
    )
    assert evidence.calls == [mutation.identity.key]
    assert len(observed) == 3 and all(receipt.status == "completed" for receipt in observed)
    coordinator.journal.verify_invocation(handle.invocation_id, handle.workflow_fingerprint)


@pytest.mark.asyncio
async def test_native_commit_without_acknowledgement_reconciles_after_service_reopen(writes):
    coordinator, handle, mutation, evidence, path, _ = writes
    evidence.fail = "after"
    with pytest.raises(InvocationStateError) as error:
        await coordinator.mutate(handle, mutation)
    assert "secret" not in str(error.value)
    assert phase(coordinator, mutation) == "dispatched"
    assert coordinator.journal.get(mutation.identity).status == "pending"
    invocations = InvocationStore(path)
    journal = EffectJournal(path, invocations=invocations)
    restored = MemoryWrites(journal, invocations)
    restored.register(evidence.backend())
    try:
        saved = invocations.get(handle.invocation_id)
        saved._execution_lease = handle._execution_lease
        await restored.mutate(saved, mutation)
        assert evidence.calls == [mutation.identity.key]
        assert phase(restored, mutation) == "completed"
        journal.verify_invocation(handle.invocation_id, handle.workflow_fingerprint)
    finally:
        journal.close()
        invocations.close()


@pytest.mark.asyncio
async def test_dispatched_missing_marker_is_uncertain_and_never_redispatched(writes):
    coordinator, handle, mutation, evidence, *_ = writes
    evidence.fail = "before"
    with pytest.raises(InvocationStateError):
        await coordinator.mutate(handle, mutation)
    evidence.fail = None
    with pytest.raises(InvocationStateError) as error:
        await coordinator.mutate(handle, mutation)
    assert error.value.details["reason"] == "outcome_unknown"
    assert evidence.calls == [mutation.identity.key]
    assert phase(coordinator, mutation) == "dispatched"
    with pytest.raises(WorkflowExecutionError):
        coordinator.journal.verify_invocation(handle.invocation_id, handle.workflow_fingerprint)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage", ["native", "index", "receipt", "intent", "descriptor", "digest", "storage", "phase"]
)
async def test_missing_or_damaged_evidence_blocks_cached_recovery_and_provider_access(
    writes, damage
):
    coordinator, handle, mutation, evidence, *_ = writes
    await coordinator.mutate(handle, mutation)
    db = coordinator.connection
    if damage == "native":
        evidence.markers.clear()
    elif damage in {"index", "receipt", "intent"}:
        table = {
            "index": "workflow_effect_invocations",
            "receipt": "workflow_effects",
            "intent": TABLE,
        }[damage]
        db.execute(f"DELETE FROM {table} WHERE identity=?", (mutation.identity.key,))
    else:
        column, value = {
            "descriptor": ("request_json", "{}"),
            "digest": ("request_digest", "0" * 64),
            "storage": ("storage_identity", "b" * 64),
            "phase": ("phase", "prepared"),
        }[damage]
        db.execute(
            f"UPDATE {TABLE} SET {column}=? WHERE identity=?", (value, mutation.identity.key)
        )
    db.commit()
    with pytest.raises((InvocationStateError, WorkflowExecutionError)):
        await coordinator.mutate(handle, mutation)
    with pytest.raises(WorkflowExecutionError):
        coordinator.journal.verify_invocation(handle.invocation_id, handle.workflow_fingerprint)
    assert evidence.calls == [mutation.identity.key]


@pytest.mark.asyncio
async def test_completion_failure_rolls_back_phase_and_receipt_then_reconciles(writes):
    coordinator, handle, mutation, evidence, *_ = writes

    def reject(*_args, **_kwargs):
        raise RuntimeError("injected completion failure")

    coordinator.journal._execution_authorization_seal = reject
    with pytest.raises(RuntimeError, match="injected"):
        await coordinator.mutate(handle, mutation)
    assert phase(coordinator, mutation) == "dispatched"
    assert coordinator.journal.get(mutation.identity).status == "pending"
    coordinator.journal._execution_authorization_seal = None
    await coordinator.mutate(handle, mutation)
    assert phase(coordinator, mutation) == "completed"
    assert evidence.calls == [mutation.identity.key]


@pytest.mark.asyncio
async def test_changed_request_or_provider_location_never_changes_first_write(writes):
    coordinator, handle, mutation, evidence, *_ = writes
    await coordinator.mutate(handle, mutation)
    with pytest.raises(InvocationStateError):
        await coordinator.mutate(handle, replace(mutation, value_json='{"version":2}'))
    with pytest.raises(InvocationStateError):
        coordinator.register(evidence.backend("b" * 64))
    assert evidence.calls == [mutation.identity.key]


@pytest.mark.asyncio
async def test_unindexed_native_marker_is_not_adopted_as_a_new_intent(writes):
    coordinator, handle, mutation, evidence, *_ = writes
    evidence.markers[mutation.identity.key] = MemoryMutationMarker.for_request(mutation)
    with pytest.raises(InvocationStateError):
        await coordinator.mutate(handle, mutation)
    assert coordinator.journal.get(mutation.identity) is None
    assert evidence.calls == []


@pytest.mark.asyncio
async def test_common_intent_quota_is_durable_before_native_dispatch(writes):
    coordinator, handle, mutation, evidence, *_ = writes
    for index in range(MAX_MUTATIONS):
        await coordinator.mutate(handle, replace(mutation, key=f"record{index}"))
    with pytest.raises(InvocationStateError):
        await coordinator.mutate(handle, replace(mutation, key="overflow"))
    assert len(evidence.calls) == MAX_MUTATIONS
    await coordinator.mutate(handle, replace(mutation, key="record0"))
    assert len(evidence.calls) == MAX_MUTATIONS


@pytest.mark.asyncio
async def test_observer_failure_does_not_lose_the_completed_write(writes):
    coordinator, handle, mutation, evidence, *_ = writes

    def fail(*_):
        raise RuntimeError("observer lost")

    with pytest.raises(RuntimeError, match="observer lost"):
        await coordinator.mutate(handle, mutation, observer=fail)
    await coordinator.mutate(handle, mutation)
    assert evidence.calls == [mutation.identity.key]


@pytest.mark.asyncio
async def test_prepared_intent_can_resume_before_any_dispatch(writes, monkeypatch):
    coordinator, handle, mutation, evidence, *_ = writes
    original = module.owned_write_transaction
    transactions = []

    @contextmanager
    def stop_before_dispatch(*args, **kwargs):
        transactions.append(True)
        if len(transactions) == 2:
            raise RuntimeError("stop after prepared commit")
        with original(*args, **kwargs):
            yield

    with monkeypatch.context() as patch:
        patch.setattr(module, "owned_write_transaction", stop_before_dispatch)
        with pytest.raises(RuntimeError, match="prepared commit"):
            await coordinator.mutate(handle, mutation)
    assert phase(coordinator, mutation) == "prepared"
    assert evidence.calls == []
    coordinator.journal.verify_invocation(handle.invocation_id, handle.workflow_fingerprint)
    await coordinator.mutate(handle, mutation)
    assert evidence.calls == [mutation.identity.key]


@pytest.mark.asyncio
@pytest.mark.parametrize("committed", [False, True])
async def test_cancellation_preserves_dispatch_state_and_never_blindly_repeats(writes, committed):
    coordinator, handle, mutation, evidence, *_ = writes
    original = evidence.apply

    async def cancel(request, current):
        if committed:
            await original(request, current)
        else:
            evidence.calls.append(request.identity.key)
        raise asyncio.CancelledError

    coordinator.register(replace(evidence.backend(), apply=cancel))
    with pytest.raises(asyncio.CancelledError):
        await coordinator.mutate(handle, mutation)
    coordinator.register(evidence.backend())
    if committed:
        await coordinator.mutate(handle, mutation)
        assert phase(coordinator, mutation) == "completed"
    else:
        with pytest.raises(InvocationStateError):
            await coordinator.mutate(handle, mutation)
        assert phase(coordinator, mutation) == "dispatched"
    assert evidence.calls == [mutation.identity.key]


@pytest.mark.asyncio
async def test_takeover_after_native_commit_rejects_old_completion_then_new_owner_recovers(writes):
    coordinator, handle, mutation, evidence, _, ownership = writes
    original = evidence.apply
    takeover = []

    async def change_owner(request, current):
        marker = await original(request, current)
        ownership.connection.execute(
            "UPDATE invocation_ownership SET expires_at=1 WHERE invocation_id=?",
            (handle.invocation_id,),
        )
        ownership.connection.commit()
        takeover.append(ownership.acquire(handle, seconds=300))
        return marker

    coordinator.register(replace(evidence.backend(), apply=change_owner))
    with pytest.raises(InvocationStateConflict):
        await coordinator.mutate(handle, mutation)
    assert phase(coordinator, mutation) == "dispatched"
    handle._execution_lease = takeover[0]
    await coordinator.mutate(handle, mutation)
    assert phase(coordinator, mutation) == "completed"
    assert evidence.calls == [mutation.identity.key]
