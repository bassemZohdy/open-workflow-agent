"""Atomic embedded events and occurrence recovery on real common databases."""

from __future__ import annotations

import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier, Event
from typing import Any
from uuid import uuid4

import pytest
from open_workflow_agent.effects import (
    ChildInvocationDependency,
    EffectIdentity,
    EffectJournal,
    effect_request_digest,
)
from open_workflow_agent.errors import (
    EventValidationError,
    InvocationStateConflict,
    WorkflowExecutionError,
)
from open_workflow_agent.event_store import EventStore, EventStoreLimits, event_selection_identity
from open_workflow_agent.events import EventDecision, EventEnvelope, EventProgress
from open_workflow_agent.ownership import ExecutionOwnershipStore
from open_workflow_agent.persistence import InvocationStore


@pytest.fixture(params=["sqlite", "postgresql"])
def backend(request: Any, tmp_path: Path) -> str:
    if request.param == "postgresql":
        datasource = os.getenv("OWA_TEST_POSTGRES_URL")
        if not datasource:
            pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
        return datasource
    return str(tmp_path / "events.sqlite3")


def _identity(
    invocation: str | None = None,
    *,
    task: str = "/do/0/listen",
    ordinal: int = 1,
    operation: str = "listen",
) -> EffectIdentity:
    return EffectIdentity(
        invocation or str(uuid4()), "event-test-fingerprint", task, ((task, ordinal),), operation
    )


def _one(_event: EventEnvelope, _progress: EventProgress) -> EventDecision:
    return EventDecision((0,), terminal=True)


def _event(event_id: str = "event-1", event_type: str = "case.ready") -> dict[str, Any]:
    return {
        "id": event_id,
        "source": "urn:event-test",
        "type": event_type,
        "data": {"order": "order-1"},
    }


def test_acceptance_reopen_exact_duplicate_and_conflict(backend: str) -> None:
    scope = str(uuid4())
    journal = EffectJournal(backend)
    store = EventStore(journal)
    first = store.ingest(_event(), default_source="urn:ignored", scope=scope)
    journal.close()
    journal = EffectJournal(backend)
    store = EventStore(journal)
    try:
        second = store.ingest(_event(), default_source="urn:ignored", scope=scope)
        assert first == second
        assert store.arrivals(scope=scope) == ((store.arrivals(scope=scope)[0][0], first),)
        with pytest.raises(EventValidationError, match="different content"):
            store.ingest(
                {**_event(), "data": {"secret": "untrusted-change"}},
                default_source="urn:ignored",
                scope=scope,
            )
        assert len(store.arrivals(scope=scope)) == 1
        other = store.ingest(
            {**_event(), "source": "urn:another"}, default_source="urn:x", scope=scope
        )
        assert other.id == first.id and other.source != first.source
        assert len(store.arrivals(scope=scope)) == 2
    finally:
        journal.close()


def test_null_payload_is_present_and_absent_payload_stays_absent(backend: str) -> None:
    journal = EffectJournal(backend)
    store = EventStore(journal)
    scope = str(uuid4())
    try:
        explicit = store.ingest(
            {**_event("explicit"), "data": None}, default_source="urn:x", scope=scope
        )
        missing = store.ingest({"id": "missing", "type": "x"}, default_source="urn:x", scope=scope)
        assert explicit.as_dict()["data"] is None and "data" not in missing.as_dict()
        assert [e.as_dict() for _, e in store.arrivals(scope=scope)] == [
            explicit.as_dict(),
            missing.as_dict(),
        ]
    finally:
        journal.close()


def test_separate_connections_concurrently_dedupe_one_logical_arrival(backend: str) -> None:
    first_journal, second_journal = EffectJournal(backend), EffectJournal(backend)
    first, second = EventStore(first_journal), EventStore(second_journal)
    scope = str(uuid4())
    barrier = Barrier(2)

    def publish(store: EventStore) -> EventEnvelope:
        barrier.wait(timeout=5)
        return store.ingest(_event(), default_source="urn:test", scope=scope)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(publish, store) for store in (first, second)]
            accepted = [future.result(timeout=10) for future in futures]
        assert accepted[0] == accepted[1]
        assert len(first.arrivals(scope=scope)) == 1
    finally:
        first_journal.close()
        second_journal.close()


def test_postgresql_delayed_commit_cannot_let_later_arrival_cross_cursor(backend: str) -> None:
    if not backend.startswith(("postgresql:", "postgres:")):
        pytest.skip("actual PostgreSQL row-lock acceptance")
    journals = [EffectJournal(backend) for _ in range(3)]
    first, second, observer = [EventStore(journal) for journal in journals]
    allocated, release, second_started = Event(), Event(), Event()
    scope = str(uuid4())
    registration = observer.register(_identity(), {"any": [], "until": "${ .done }"}, scope=scope)

    def delayed_first() -> EventEnvelope:
        with first.transaction():
            envelope = first.ingest_in_transaction(
                _event("first"), default_source="urn:test", scope=scope
            )
            allocated.set()
            if not release.wait(timeout=10):
                raise AssertionError("first publisher was not released")
        return envelope

    def later_second() -> EventEnvelope:
        second_started.set()
        envelope = second.ingest(_event("second"), default_source="urn:test", scope=scope)
        assert [event.id for _, event in second.arrivals(scope=scope)] == ["first", "second"]
        return envelope

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first_future = executor.submit(delayed_first)
            assert allocated.wait(timeout=5)
            second_future = executor.submit(later_second)
            try:
                assert second_started.wait(timeout=5)
                deadline = time.monotonic() + 5
                blocked = False
                # Observe the real DB row-lock wait; thread start or a timing
                # sleep alone would not establish the cross-connection guard.
                while time.monotonic() < deadline:
                    row = observer.connection.execute(
                        "SELECT wait_event_type FROM pg_stat_activity WHERE pid = ?",
                        (second.connection.raw.info.backend_pid,),
                    ).fetchone()
                    if row is not None and row[0] == "Lock":
                        blocked = True
                        break
                    time.sleep(0.01)
                assert blocked and not second_future.done()
                assert observer.arrivals(scope=scope) == ()
            finally:
                release.set()
            assert first_future.result(timeout=10).id == "first"
            assert second_future.result(timeout=10).id == "second"
        arrivals = observer.arrivals(scope=scope)
        assert [event.id for _, event in arrivals] == ["first", "second"]
        assert arrivals[0][0] < arrivals[1][0]
        first_selection = observer.select(
            registration, evaluate_candidate=lambda *_: EventDecision((0,))
        )
        second_selection = observer.select(
            registration, evaluate_candidate=lambda *_: EventDecision((0,), terminal=True)
        )
        assert first_selection.events[0].id == "first"
        assert second_selection.completed and [e.id for e in second_selection.events] == [
            "first",
            "second",
        ]
    finally:
        release.set()
        for journal in journals:
            journal.close()


def test_generated_publication_is_stable_across_crash_and_delivers_once(backend: str) -> None:
    journal = EffectJournal(backend)
    store = EventStore(journal)
    identity = _identity(operation="emit")
    scope = str(uuid4())
    properties = {"type": "case.created", "data": {"ok": True}}
    digest = effect_request_digest("emit", properties)
    first = store.publish_effect(
        identity, digest, properties, default_source="urn:test", scope=scope
    )
    assert store.arrivals(scope=scope) == ()
    journal.close()
    journal = EffectJournal(backend)
    store = EventStore(journal)
    try:
        replay = store.publish_effect(
            identity, digest, properties, default_source="urn:test", scope=scope
        )
        assert replay == first
        assert store.deliver_outbox() >= 1
        assert store.deliver_outbox() == 0
        assert [e.as_dict() for _, e in store.arrivals(scope=scope)] == [first.output]
        store.verify_invocation(identity.invocation_id, identity.workflow_fingerprint)
        with pytest.raises(WorkflowExecutionError, match="request is inconsistent"):
            store.publish_effect(
                identity,
                effect_request_digest("emit", {"changed": True}),
                properties,
                default_source="urn:test",
                scope=scope,
            )
    finally:
        journal.close()


@pytest.mark.parametrize("boundary", ["registration", "publication", "selection", "delivery"])
def test_event_atomic_failpoints_leave_no_half_acceptance(
    backend: str, monkeypatch: Any, boundary: str
) -> None:
    journal = EffectJournal(backend)
    store = EventStore(journal)
    scope = str(uuid4())
    identity = _identity(operation="emit" if boundary in {"publication", "delivery"} else "listen")
    registration = None
    if boundary == "selection":
        registration = store.register(
            identity, {"one": {"with": {"type": "case.ready"}}}, scope=scope
        )
        store.ingest(_event(), default_source="urn:test", scope=scope)
    if boundary == "delivery":
        store.publish_effect(
            identity,
            effect_request_digest("emit", _event()),
            _event(),
            default_source="urn:test",
            scope=scope,
        )
    execute = journal.connection.execute

    def fail(connection: Any, statement: str, parameters: tuple[Any, ...] = ()) -> Any:
        if boundary == "delivery":
            selected = "UPDATE event_outbox SET delivered" in statement
        else:
            target = (
                identity.key
                if boundary != "registration"
                else replace(identity, operation="listen:registration:v1").key
            )
            selected = "INSERT INTO workflow_effects(" in statement and parameters[0] == target
        if selected:
            raise RuntimeError("private injected failure never exposed")
        return execute(statement, parameters)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(type(journal.connection), "execute", fail)
            with pytest.raises(WorkflowExecutionError, match="transaction could not be stored"):
                if boundary == "registration":
                    store.register(identity, {"one": {"with": {"type": "x"}}}, scope=scope)
                elif boundary == "publication":
                    store.publish_effect(
                        identity,
                        effect_request_digest("emit", _event()),
                        _event(),
                        default_source="urn:test",
                        scope=scope,
                    )
                elif boundary == "selection":
                    assert registration is not None
                    store.select(registration, evaluate_candidate=_one)
                else:
                    store.deliver_outbox()
        if boundary == "registration":
            assert (
                journal.connection.execute(
                    "SELECT 1 FROM event_subscriptions WHERE identity = ?", (identity.key,)
                ).fetchone()
                is None
            )
            assert journal.get(replace(identity, operation="listen:registration:v1")) is None
        elif boundary == "publication":
            assert journal.get(identity) is None
            assert (
                journal.connection.execute(
                    "SELECT 1 FROM event_outbox WHERE identity = ?", (identity.key,)
                ).fetchone()
                is None
            )
        elif boundary == "selection":
            assert registration is not None
            assert store.state(registration).events == ()
            assert journal.get(event_selection_identity(registration, 1)) is None
            assert store.select(registration, evaluate_candidate=_one).completed
        else:
            assert store.arrivals(scope=scope) == ()
            assert (
                journal.connection.execute(
                    "SELECT delivered FROM event_outbox WHERE identity = ?", (identity.key,)
                ).fetchone()[0]
                == 0
            )
            store.deliver_outbox()
            assert len(store.arrivals(scope=scope)) == 1
    finally:
        journal.close()


def test_partial_multiple_event_binding_and_terminator_survive_reopen(backend: str) -> None:
    journal = EffectJournal(backend)
    store = EventStore(journal)
    identity = _identity()
    scope = str(uuid4())
    strategy = {"any": [], "until": {"one": {"with": {"type": "done"}}}}
    registration = store.register(identity, strategy, scope=scope)

    def match(event: EventEnvelope, progress: EventProgress) -> EventDecision | None:
        if event.type == "done":
            return EventDecision(terminal=True, terminator=True, progress={"stop": True})
        if event.data["order"] != progress.correlations.get("order", event.data["order"]):
            return None
        return EventDecision(
            (0,), {"order": event.data["order"]}, progress={"count": len(progress.events) + 1}
        )

    store.ingest(_event("first"), default_source="urn:test", scope=scope)
    first = store.select(registration, evaluate_candidate=match)
    assert first.selected is not None and first.event_receipt is not None
    assert not first.completed and first.correlations == {"order": "order-1"}
    journal.close()
    journal = EffectJournal(backend)
    store = EventStore(journal)
    try:
        assert store.register(identity, strategy, scope=scope) == registration
        saved = store.state(registration)
        assert saved.correlations == first.correlations and saved.events == first.events
        store.ingest(
            {**_event("unrelated"), "data": {"order": "other"}},
            default_source="urn:test",
            scope=scope,
        )
        store.ingest(_event("second"), default_source="urn:test", scope=scope)
        second = store.select(registration, evaluate_candidate=match)
        assert [e.id for e in second.events] == ["first", "second"]
        store.ingest(_event("terminator", "done"), default_source="urn:test", scope=scope)
        terminal = store.select(registration, evaluate_candidate=match)
        assert terminal.completed and terminal.selected is None
        assert terminal.receipt is not None and len(terminal.receipt.output) == 2
        assert [e.id for e in terminal.events] == ["first", "second"]
        assert (
            store.select(
                registration, evaluate_candidate=lambda *_: pytest.fail("replayed matcher")
            ).receipt
            == terminal.receipt
        )
        store.verify_invocation(identity.invocation_id, identity.workflow_fingerprint)
    finally:
        journal.close()


def test_active_broadcast_and_sequential_registration_cutoff(backend: str) -> None:
    journal = EffectJournal(backend)
    store = EventStore(journal)
    scope = str(uuid4())
    invocation = str(uuid4())
    strategy = {"one": {"with": {"type": "case.ready"}}}
    try:
        # Early event is available to a new invocation and both active fork listeners.
        store.ingest(_event(), default_source="urn:test", scope=scope)
        first = store.register(_identity(invocation, task="/fork/left"), strategy, scope=scope)
        second = store.register(_identity(invocation, task="/fork/right"), strategy, scope=scope)
        assert store.select(first, evaluate_candidate=_one).events[0].id == "event-1"
        assert store.select(second, evaluate_candidate=_one).events[0].id == "event-1"
        sequential = store.register(
            _identity(invocation, task="/next/listen"), strategy, scope=scope
        )
        repeated = store.register(
            _identity(invocation, task="/fork/left", ordinal=2), strategy, scope=scope
        )
        assert not store.select(sequential, evaluate_candidate=_one).completed
        assert not store.select(repeated, evaluate_candidate=_one).completed
        another = store.register(_identity(), strategy, scope=scope)
        assert store.select(another, evaluate_candidate=_one).events[0].id == "event-1"
        store.ingest(_event("event-2"), default_source="urn:test", scope=scope)
        assert store.select(sequential, evaluate_candidate=_one).events[0].id == "event-2"
        assert store.select(repeated, evaluate_candidate=_one).events[0].id == "event-2"
    finally:
        journal.close()


def test_trusted_scope_isolates_same_event_identifiers(backend: str) -> None:
    journal = EffectJournal(backend)
    store = EventStore(journal)
    first_scope, second_scope = str(uuid4()), str(uuid4())
    try:
        registration = store.register(_identity(), {"any": []}, scope=first_scope)
        store.ingest(
            {**_event(), "principal": first_scope}, default_source="urn:test", scope=second_scope
        )
        assert not store.select(registration, evaluate_candidate=_one).completed
        assert store.arrivals(scope=first_scope) == ()
        store.ingest(_event(), default_source="urn:test", scope=first_scope)
        assert store.select(registration, evaluate_candidate=_one).completed
    finally:
        journal.close()


def test_registration_conflict_and_empty_completion(backend: str) -> None:
    journal = EffectJournal(backend)
    store = EventStore(journal)
    identity = _identity()
    scope = str(uuid4())
    try:
        registration = store.register(identity, {"all": []}, scope=scope)
        with pytest.raises(WorkflowExecutionError, match="inconsistent"):
            store.register(identity, {"one": {"with": {"type": "x"}}}, scope=scope)
        empty = store.complete(registration)
        assert empty.completed and empty.receipt is not None and empty.receipt.output == []
        assert store.complete(registration) == empty
    finally:
        journal.close()


@pytest.mark.parametrize(
    "corruption",
    [
        "event-delete",
        "event-change",
        "selection-delete",
        "subscription-delete",
        "registration-delete",
        "outbox-delete",
    ],
)
def test_corrupt_event_evidence_fails_reconciliation(backend: str, corruption: str) -> None:
    journal = EffectJournal(backend)
    store = EventStore(journal)
    identity = _identity()
    scope = str(uuid4())
    registration = store.register(identity, {"one": {"with": {"type": "case.ready"}}}, scope=scope)
    store.ingest(_event(), default_source="urn:test", scope=scope)
    store.select(registration, evaluate_candidate=_one)
    try:
        with store.transaction():
            if corruption == "event-delete":
                journal.connection.execute("DELETE FROM event_inbox WHERE scope = ?", (scope,))
            elif corruption == "event-change":
                journal.connection.execute(
                    "UPDATE event_inbox SET envelope = '{}' WHERE scope = ?", (scope,)
                )
            elif corruption == "selection-delete":
                journal.connection.execute(
                    "DELETE FROM event_selections WHERE subscription = ?", (identity.key,)
                )
            elif corruption == "subscription-delete":
                journal.connection.execute(
                    "DELETE FROM event_subscriptions WHERE identity = ?", (identity.key,)
                )
            elif corruption == "registration-delete":
                journal.connection.execute(
                    "DELETE FROM workflow_effects WHERE identity = ?",
                    (registration.receipt_identity.key,),
                )
        if corruption == "outbox-delete":
            emit = _identity(identity.invocation_id, task="/emit", operation="emit")
            store.publish_effect(
                emit,
                effect_request_digest("emit", _event("output")),
                _event("output"),
                default_source="urn:test",
                scope=scope,
            )
            with store.transaction():
                journal.connection.execute(
                    "DELETE FROM event_outbox WHERE identity = ?", (emit.key,)
                )
        with pytest.raises(WorkflowExecutionError, match="could not be reconciled"):
            store.verify_invocation(identity.invocation_id, identity.workflow_fingerprint)
    finally:
        journal.close()


def test_profile_is_immutable_and_missing_legacy_profile_rejected(backend: str) -> None:
    journal = EffectJournal(backend)
    store = EventStore(journal)
    identity = _identity()
    scope = str(uuid4())
    try:
        with pytest.raises(WorkflowExecutionError, match="could not be reconciled"):
            store.verify_invocation(
                identity.invocation_id, identity.workflow_fingerprint, require_profile=True
            )
        store.bind_invocation(identity.invocation_id, identity.workflow_fingerprint, scope)
        store.bind_invocation(identity.invocation_id, identity.workflow_fingerprint, scope)
        store.verify_invocation(
            identity.invocation_id, identity.workflow_fingerprint, require_profile=True
        )
        with pytest.raises(WorkflowExecutionError, match="incompatible"):
            store.bind_invocation(identity.invocation_id, identity.workflow_fingerprint, "forged")
        with store.transaction():
            journal.connection.execute(
                "DELETE FROM event_invocation_profiles WHERE invocation_id = ?",
                (identity.invocation_id,),
            )
        with pytest.raises(WorkflowExecutionError, match="could not be reconciled"):
            store.verify_invocation(
                identity.invocation_id, identity.workflow_fingerprint, require_profile=True
            )
    finally:
        journal.close()


def test_capacity_invalid_input_and_retention_are_fail_closed(tmp_path: Path) -> None:
    journal = EffectJournal(tmp_path / "events.sqlite3")
    store = EventStore(
        journal,
        limits=EventStoreLimits(
            max_retained_events=1, max_event_bytes=256, max_subscriptions=1, retention_seconds=1
        ),
    )
    try:
        original = store.ingest(_event(), default_source="urn:test")
        assert store.ingest(_event(), default_source="urn:test") == original
        with pytest.raises(EventValidationError, match="capacity"):
            store.ingest(_event("new"), default_source="urn:test")
        for value in ({"type": "x", "data": float("inf")}, {"type": "x", "data": "secret" * 100}):
            with pytest.raises(EventValidationError) as error:
                store.ingest(value, default_source="urn:test")
            assert "secret" not in str(error.value)
        registration = store.register(_identity(), {"one": {"with": {"type": "case.ready"}}})
        with pytest.raises(EventValidationError, match="subscription capacity"):
            store.register(_identity(), {"any": []})
        assert store.prune(now=time.time() + 10) == 0  # Pending listener retains early replay.
        store.select(registration, evaluate_candidate=_one)
        assert store.prune(now=time.time() + 10) == 0  # Immutable selected evidence remains pinned.
        assert store.arrivals()[0][1] == original
    finally:
        journal.close()


def test_expired_unselected_arrivals_can_be_pruned(tmp_path: Path) -> None:
    journal = EffectJournal(tmp_path / "events.sqlite3")
    store = EventStore(journal, limits=EventStoreLimits(retention_seconds=1))
    try:
        store.ingest(_event(), default_source="urn:test")
        assert store.prune(now=time.time() + 10) == 1
        assert store.arrivals() == ()
    finally:
        journal.close()


def test_acknowledged_outbox_reserves_inbox_capacity_and_canonical_identity(tmp_path: Path) -> None:
    journal = EffectJournal(tmp_path / "events.sqlite3")
    store = EventStore(journal, limits=EventStoreLimits(max_retained_events=1))
    emit = _identity(operation="emit")
    try:
        receipt = store.publish_effect(
            emit, effect_request_digest("emit", _event()), _event(), default_source="urn:test"
        )
        with pytest.raises(EventValidationError, match="capacity"):
            store.ingest(_event("racing-api-event"), default_source="urn:test")
        # A matching API retry exchanges the publication reservation for the
        # same canonical event, including its originally generated timestamp.
        accepted = store.ingest(_event(), default_source="urn:test")
        assert accepted.as_dict() == receipt.output
        assert store.deliver_outbox() == 1
        assert len(store.arrivals()) == 1
        second_emit = _identity(emit.invocation_id, task="/emit/second", operation="emit")
        duplicate = store.publish_effect(
            second_emit,
            effect_request_digest("emit", _event()),
            _event(),
            default_source="urn:test",
        )
        assert duplicate.output == receipt.output
        assert store.deliver_outbox() == 1
        assert len(store.arrivals()) == 1
    finally:
        journal.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("correlations", {"order": "forged"}),
        ("progress", {"done": True}),
        ("matched_filters", [17]),
        ("cursor", 999999),
    ],
)
def test_partial_correlation_and_routing_progress_cannot_be_changed(
    backend: str, field: str, value: Any
) -> None:
    journal = EffectJournal(backend)
    store = EventStore(journal)
    identity = _identity()
    scope = str(uuid4())
    registration = store.register(identity, {"any": [], "until": "${false}"}, scope=scope)
    try:
        store.ingest(_event(), default_source="urn:test", scope=scope)
        original = store.select(
            registration,
            evaluate_candidate=lambda event, _progress: EventDecision(
                (0,), {"order": event.data["order"]}, progress={"done": False}
            ),
        )
        assert original.decision_receipt is not None and original.decision_ordinal == 1
        assert not original.completed
        with store.transaction():
            encoded = journal.connection.execute(
                "SELECT payload FROM event_subscriptions WHERE identity = ?", (identity.key,)
            ).fetchone()[0]
            payload = json.loads(encoded)
            payload[field] = value
            journal.connection.execute(
                "UPDATE event_subscriptions SET payload = ? WHERE identity = ?",
                (json.dumps(payload), identity.key),
            )
        with pytest.raises(WorkflowExecutionError, match="could not be reconciled"):
            store.verify_invocation(identity.invocation_id, identity.workflow_fingerprint)
    finally:
        journal.close()


@pytest.mark.parametrize("field", ["event", "correlation"])
def test_boolean_cannot_be_substituted_with_numeric_one_in_saved_evidence(
    backend: str, field: str
) -> None:
    journal = EffectJournal(backend)
    store = EventStore(journal)
    scope = str(uuid4())
    identity = _identity()
    registration = store.register(identity, {"any": []}, scope=scope)
    try:
        store.ingest({**_event(), "data": {"order": True}}, default_source="urn:test", scope=scope)
        store.select(
            registration,
            evaluate_candidate=lambda event, _progress: EventDecision(
                (0,), {"order": event.data["order"]}
            ),
        )
        with store.transaction():
            if field == "event":
                raw = journal.connection.execute(
                    "SELECT envelope FROM event_inbox WHERE scope = ?", (scope,)
                ).fetchone()[0]
                event = json.loads(raw)
                event["data"]["order"] = 1
                encoded = json.dumps(
                    event, sort_keys=True, ensure_ascii=False, separators=(",", ":")
                )
                digest = hashlib.sha256(encoded.encode()).hexdigest()
                journal.connection.execute(
                    "UPDATE event_inbox SET envelope = ?, digest = ? WHERE scope = ?",
                    (encoded, digest, scope),
                )
                journal.connection.execute(
                    "UPDATE event_selections SET digest = ? WHERE subscription = ?",
                    (digest, identity.key),
                )
            else:
                raw = journal.connection.execute(
                    "SELECT payload FROM event_subscriptions WHERE identity = ?", (identity.key,)
                ).fetchone()[0]
                value = json.loads(raw)
                value["correlations"]["order"] = 1
                journal.connection.execute(
                    "UPDATE event_subscriptions SET payload = ? WHERE identity = ?",
                    (json.dumps(value), identity.key),
                )
        with pytest.raises(WorkflowExecutionError, match="could not be reconciled"):
            store.verify_invocation(identity.invocation_id, identity.workflow_fingerprint)
    finally:
        journal.close()


def test_ownership_loss_blocks_registration_publication_and_selection(tmp_path: Path) -> None:
    path = tmp_path / "events.sqlite3"
    invocations = InvocationStore(path)
    ownership = ExecutionOwnershipStore(path)
    journal = EffectJournal(path, invocations=invocations)
    store = EventStore(journal)
    handle = invocations.create(
        engine="langgraph",
        session_id=None,
        user_id=None,
        workflow_name="events",
        workflow_version="1.0.0",
        workflow_fingerprint="event-test-fingerprint",
    )
    lease = ownership.acquire(handle)
    identity = _identity(handle.invocation_id)
    registration = store.register(identity, {"any": []}, execution_lease=lease)
    store.ingest(_event(), default_source="urn:test")
    ownership.release(lease)
    next_lease = ownership.acquire(handle)
    try:
        with pytest.raises(InvocationStateConflict):
            store.select(registration, evaluate_candidate=_one, execution_lease=lease)
        with pytest.raises(InvocationStateConflict):
            store.register(
                _identity(handle.invocation_id, ordinal=2), {"any": []}, execution_lease=lease
            )
        with pytest.raises(InvocationStateConflict):
            store.publish_effect(
                _identity(handle.invocation_id, operation="emit"),
                effect_request_digest("emit", _event()),
                _event(),
                default_source="urn:test",
                execution_lease=lease,
            )
        assert store.state(registration).events == ()
        assert store.select(
            registration, evaluate_candidate=_one, execution_lease=next_lease
        ).completed
    finally:
        ownership.release(next_lease)
        journal.close()
        ownership.close()
        invocations.close()


def test_terminal_disposition_is_distinct_from_process_interruption(tmp_path: Path) -> None:
    path = tmp_path / "events.sqlite3"
    invocations = InvocationStore(path)
    journal = EffectJournal(path, invocations=invocations)
    store = EventStore(journal)
    handle = invocations.create(
        engine="adk",
        session_id=None,
        user_id=None,
        workflow_name="events",
        workflow_version="1.0.0",
        workflow_fingerprint="event-test-fingerprint",
    )
    identity = _identity(handle.invocation_id)
    registration = store.register(identity, {"any": []})
    journal.close()  # Process/service interruption is not a durable cancel.
    journal = EffectJournal(path, invocations=invocations)
    store = EventStore(journal)
    try:
        assert not store.state(registration).closed
        with pytest.raises(InvocationStateConflict):
            store.close_invocation(handle.invocation_id, handle.workflow_fingerprint)
        invocations.update(handle, status="cancelled")
        store.close_invocation(handle.invocation_id, handle.workflow_fingerprint)
        store.ingest(_event(), default_source="urn:test")
        result = store.select(
            registration, evaluate_candidate=lambda *_: pytest.fail("late matcher")
        )
        assert result.closed and result.events == ()
    finally:
        journal.close()
        invocations.close()


def test_parent_recovery_reconciles_terminal_child_event_evidence(backend: str) -> None:
    invocations = InvocationStore(backend)
    journal = EffectJournal(backend, invocations=invocations)
    store = EventStore(journal)
    scope = str(uuid4())
    parent = invocations.create(
        engine="langgraph",
        session_id=None,
        user_id=None,
        workflow_name="parent-events",
        workflow_version="1.0.0",
        workflow_fingerprint="event-test-fingerprint",
    )
    child = invocations.create(
        engine="langgraph",
        session_id=None,
        user_id=None,
        workflow_name="child-events",
        workflow_version="1.0.0",
        workflow_fingerprint="event-test-fingerprint",
        parent_invocation_id=parent.invocation_id,
        parent_task_reference="/do/0/run",
    )
    invocations.bind_input(child, {"child": "input"})
    store.bind_invocation(child.invocation_id, child.workflow_fingerprint, scope)
    registration = store.register(_identity(child.invocation_id), {"any": []}, scope=scope)
    try:
        store.ingest(_event(), default_source="urn:test", scope=scope)
        child_result = store.select(registration, evaluate_candidate=_one)
        assert child_result.receipt is not None
        invocations.update(child, status="completed", output=child_result.receipt.output)
        parent_effect = _identity(parent.invocation_id, task="/do/0/run", operation="run:workflow")
        digest = effect_request_digest(
            "run:workflow",
            {"workflow_fingerprint": child.workflow_fingerprint, "input": {"child": "input"}},
        )
        journal.begin(parent_effect, digest, "parent-action")
        journal.finish(
            parent_effect,
            "parent-action",
            output=child.output,
            child_invocation=ChildInvocationDependency.capture(invocations, child.invocation_id),
        )
        store.verify_invocation(parent.invocation_id, parent.workflow_fingerprint)
        with store.transaction():
            journal.connection.execute(
                "DELETE FROM event_subscriptions WHERE identity = ?", (registration.identity.key,)
            )
        with pytest.raises(WorkflowExecutionError, match="could not be reconciled"):
            store.verify_invocation(parent.invocation_id, parent.workflow_fingerprint)
        current_parent = invocations.get(parent.invocation_id)
        assert current_parent is not None and current_parent.status == "running"
    finally:
        journal.close()
        invocations.close()
