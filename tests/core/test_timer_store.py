"""Verify common timer receipts independently of native checkpoint formats."""

import os
from dataclasses import replace
from uuid import uuid4

import pytest
from open_workflow_agent.effects import EffectIdentity, EffectJournal
from open_workflow_agent.errors import WorkflowExecutionError
from open_workflow_agent.timers import TimerStore, TimerSuspension


def identity():
    return EffectIdentity("inv", "fingerprint", "/do/0/pause", (("/do/0/pause", 1),), "wait:arm:v1")


def test_timer_reopen_keeps_original_due_and_manual_wake_decision(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    journal = EffectJournal(path)
    store = TimerStore(journal)
    armed = store.arm(identity(), 601, now=1000, workflow_source={"document": {"name": "test"}})
    assert armed.due_at == 1601
    journal.close()
    journal = EffectJournal(path)
    try:
        store = TimerStore(journal)
        reopened = store.arm(
            identity(), 601, now=1100, workflow_source={"document": {"name": "test"}}
        )
        assert reopened == armed
        assert store.pending_for_invocation("inv", "fingerprint") == [armed]
        with pytest.raises(WorkflowExecutionError, match="eligible"):
            store.wake(reopened, "timer_due", now=1200)
        decision = store.wake(reopened, "resume_requested", {"value": None}, now=1200)
        duplicate = store.wake(reopened, "timer_due", now=1700)
        assert duplicate == decision
        assert decision.resume_input == {"value": None}
        assert decision.reason == "resume_requested"
        store.verify_invocation("inv", "fingerprint")
    finally:
        journal.close()


@pytest.mark.parametrize("boundary", ["row", "arm", "wake", "index"])
def test_missing_timer_evidence_fails_closed(tmp_path, boundary):
    journal = EffectJournal(tmp_path / "runtime.sqlite3")
    try:
        store = TimerStore(journal)
        armed = store.arm(identity(), 60, now=1000, workflow_source={})
        store.wake(armed, "timer_due", now=1060)
        table, key = {
            "row": ("workflow_timers", identity().key),
            "arm": ("workflow_effects", identity().key),
            "wake": ("workflow_effects", replace(identity(), operation="wait:wake:v1").key),
            "index": ("workflow_effect_invocations", identity().key),
        }[boundary]
        journal.connection.execute(f"DELETE FROM {table} WHERE identity = ?", (key,))
        journal.connection.commit()
        with pytest.raises(WorkflowExecutionError):
            store.verify_invocation("inv", "fingerprint")
    finally:
        journal.close()


def test_timer_row_failure_rolls_back_receipt_and_index(tmp_path, monkeypatch):
    journal = EffectJournal(tmp_path / "runtime.sqlite3")
    try:
        store = TimerStore(journal)
        execute = journal.connection.execute

        def fail(statement, parameters=()):
            if "INSERT INTO workflow_timers" in statement:
                raise RuntimeError("injected timer row failure")
            return execute(statement, parameters)

        with monkeypatch.context() as scoped:
            scoped.setattr(
                type(journal.connection),
                "execute",
                lambda self, sql, parameters=(): fail(sql, parameters),
            )
            with pytest.raises(RuntimeError, match="injected"):
                store.arm(identity(), 60, now=1000, workflow_source={})
        assert journal.get(identity()) is None
        assert execute("SELECT COUNT(*) FROM workflow_effect_invocations").fetchone()[0] == 0
        assert store.arm(identity(), 60, now=1000, workflow_source={}).due_at == 1060
    finally:
        journal.close()


def test_timer_changed_request_and_capacity_are_not_silent(tmp_path):
    journal = EffectJournal(tmp_path / "runtime.sqlite3")
    try:
        store = TimerStore(journal, max_pending=1)
        store.arm(identity(), 60, now=1000, workflow_source={})
        with pytest.raises(WorkflowExecutionError, match="differs"):
            store.arm(identity(), 61, now=1000, workflow_source={})
        with pytest.raises(WorkflowExecutionError, match="capacity"):
            store.arm(
                replace(identity(), occurrence=(("/do/0/pause", 2),)),
                60,
                now=1000,
                workflow_source={},
            )
    finally:
        journal.close()


def test_private_suspension_preserves_identity_and_rejects_boolean_due():
    signal = TimerSuspension(identity(), 1060)
    assert TimerSuspension.from_value(signal.as_value()).identity == identity()
    with pytest.raises(WorkflowExecutionError):
        TimerSuspension.from_value({**signal.as_value(), "due_at": True})


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_due_claim_is_fenced_across_connections_and_stale_release(tmp_path, backend):
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    path = datasource or tmp_path / "runtime.sqlite3"
    first, second = EffectJournal(path), EffectJournal(path)
    timer_identity = replace(identity(), invocation_id=str(uuid4()))
    try:
        owner, contender = TimerStore(first), TimerStore(second)
        armed = owner.arm(timer_identity, 60, now=1000, workflow_source={})
        assert owner.claim_due(owner="first", now=1059, timer_key=armed.timer_key) is None
        claim = owner.claim_due(
            owner="first", now=1060, timer_key=armed.timer_key, lease_seconds=10
        )
        assert claim is not None
        assert contender.claim_due(owner="second", now=1061, timer_key=armed.timer_key) is None
        recovered = contender.claim_due(owner="second", now=1070, timer_key=armed.timer_key)
        assert recovered is not None and recovered.generation == claim.generation + 1
        with pytest.raises(WorkflowExecutionError, match="stale"):
            owner.verify_claim(claim, now=1070)
        owner.release_claim(claim)
        contender.verify_claim(recovered, now=1071)
        contender.release_claim(recovered)
        assert owner.due_keys(now=1071, invocation_id=timer_identity.invocation_id) == [
            armed.timer_key
        ]
    finally:
        try:
            first.connection.execute(
                "DELETE FROM workflow_timers WHERE identity=?", (timer_identity.key,)
            )
            first.connection.execute(
                "DELETE FROM workflow_effects WHERE identity=?", (timer_identity.key,)
            )
            first.connection.execute(
                "DELETE FROM workflow_effect_invocations WHERE identity=?", (timer_identity.key,)
            )
            first.connection.commit()
        finally:
            second.close()
            first.close()


def test_deadline_delivery_preserves_original_wait_due_after_reopen(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    journal = EffectJournal(path)
    armed = TimerStore(journal).arm(identity(), 601, now=1000, workflow_source={}, deadline_at=1030)
    assert armed.due_at == 1601 and armed.wake_at == 1030
    journal.close()
    journal = EffectJournal(path)
    try:
        store = TimerStore(journal)
        assert store.due_keys(now=1029) == []
        assert store.due_keys(now=1030) == [armed.timer_key]
        wake = store.wake(armed, "timer_due", now=1030)
        assert wake.due_at == 1601
        store.consume(armed, execution_lease=None)
        assert store.due_keys(now=1601) == []
        assert store.known_wakes("inv", "fingerprint")[armed.timer_key] == wake
        store.verify_invocation("inv", "fingerprint")
    finally:
        journal.close()
