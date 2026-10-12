"""Independent-connection scheduling claim/binding boundaries."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any
from uuid import uuid4

import pytest
from open_workflow_agent._schedule_storage import initialize_schedule_tables
from open_workflow_agent.errors import (
    ScheduleOperationConflict,
    ScheduleValidationError,
    WorkflowExecutionError,
)
from open_workflow_agent.persistence import InvocationStore
from open_workflow_agent.scheduling import ScheduleStore
from open_workflow_agent.storage import open_storage
from open_workflow_agent.workflow import WorkflowPlan, compile_workflow


def _plan(kind: str = "after") -> WorkflowPlan:
    return compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "schedule-store",
                "name": "slot-" + uuid4().hex,
                "version": "1.0.0",
            },
            "schedule": {kind: {"seconds": 2}},
            "do": [{"finish": {"set": {"done": True}}}],
        }
    )


@pytest.fixture(params=["sqlite", "postgres"])
def database(request: pytest.FixtureRequest, tmp_path: Path) -> str:
    if request.param == "postgres":
        url = os.getenv("OWA_TEST_POSTGRES_URL")
        if not url:
            pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
        return url
    return str(tmp_path / "runtime.sqlite3")


def test_epoch_fractional_schedule_clocks_survive_reopen_and_binding(database: str) -> None:
    epoch = 1_791_787_000.125
    due, claim_at = epoch + 2, epoch + 2.25
    store = ScheduleStore(database)
    created = store.create(_plan("every"), {"original": True}, now=epoch)
    assert created.next_run_at == due
    store.close()
    store = ScheduleStore(database)
    try:
        saved = store.get(created.schedule_id)
        assert saved is not None and saved.next_run_at == due
        assert store.claim_due(now=due - 0.125, schedule_id=created.schedule_id) is None
        claimed = store.claim_due(
            now=claim_at, lease_seconds=30.125, schedule_id=created.schedule_id
        )
        assert claimed is not None and claimed._claim is not None
        assert claimed._claim.lease_until == claim_at + 30.125
        handle = store.bind_invocation(claimed, engine="test", now=claim_at)
        assert store.invocations.read_input(handle) == {"original": True}
        clocks = store.connection.execute(
            "SELECT nominal_at,lease_until FROM schedule_slots WHERE schedule_id=?",
            (created.schedule_id,),
        ).fetchone()
        assert clocks == (due, claim_at + 30.125)
        store.close()
        store = ScheduleStore(database)
        assert (
            store.bind_invocation(claimed, engine="test", now=claim_at).invocation_id
            == handle.invocation_id
        )
        saved = store.get(created.schedule_id)
        assert saved is not None and saved.next_run_at == due + 2
        with store.transactions.transaction() as connection:
            connection.execute(
                "UPDATE schedules SET next_run_at=? WHERE schedule_id=?",
                (saved.next_run_at + 0.125, created.schedule_id),
            )
        try:
            with pytest.raises(ScheduleValidationError, match="inconsistent"):
                store.get(created.schedule_id)
        finally:
            with store.transactions.transaction() as connection:
                connection.execute(
                    "UPDATE schedules SET next_run_at=? WHERE schedule_id=?",
                    (saved.next_run_at, created.schedule_id),
                )
    finally:
        store.cancel(created.schedule_id)
        store.close()


def test_postgresql_legacy_float4_clocks_widen_without_repairing_evidence() -> None:
    url = os.getenv("OWA_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    namespace = "owa_schedule_precision_" + uuid4().hex
    connection = open_storage(url, namespace)
    try:
        connection.execute(
            "CREATE TABLE schedules (schedule_id TEXT PRIMARY KEY,payload TEXT NOT NULL,"
            "status TEXT NOT NULL,next_run_at REAL NOT NULL,lease_until REAL,"
            "operation_key TEXT UNIQUE)"
        )
        connection.execute(
            "CREATE TABLE schedule_slots (slot_key TEXT PRIMARY KEY,schedule_id TEXT NOT NULL,"
            "occurrence_key TEXT NOT NULL,nominal_at REAL NOT NULL,state TEXT NOT NULL,"
            "owner TEXT NOT NULL,generation BIGINT NOT NULL,lease_until REAL,"
            "invocation_id TEXT UNIQUE,input_payload TEXT NOT NULL,"
            "UNIQUE(schedule_id,occurrence_key))"
        )
        epoch = 1_791_787_000.125
        payload = '{"next_run_at":1791787000.125,"legacy":"preserve"}'
        connection.execute(
            "INSERT INTO schedules VALUES ('legacy',?,'active',?,?,NULL)",
            (payload, epoch, epoch + 30.375),
        )
        connection.execute(
            "INSERT INTO schedule_slots VALUES "
            "('slot','legacy','slot:1',?,'claimed','old',1,?,NULL,?)",
            (epoch + 2.25, epoch + 30.375, '{"original":true}'),
        )
        schedule_before = connection.execute("SELECT * FROM schedules").fetchone()
        slot_before = connection.execute("SELECT * FROM schedule_slots").fetchone()
        assert schedule_before[3] != epoch  # The old float4 value already lost precision.
        initialize_schedule_tables(connection)
        initialize_schedule_tables(connection)  # Reopening the widened layout is idempotent.
        assert connection.execute("SELECT * FROM schedules").fetchone() == schedule_before
        assert connection.execute("SELECT * FROM schedule_slots").fetchone() == slot_before
        types = connection.execute(
            "SELECT udt_name FROM information_schema.columns "
            "WHERE table_schema=current_schema() AND "
            "((table_name='schedules' AND column_name IN ('next_run_at','lease_until')) OR "
            "(table_name='schedule_slots' AND column_name IN ('nominal_at','lease_until')))"
        ).fetchall()
        assert len(types) == 4 and all(row[0] == "float8" for row in types)
    finally:
        try:
            # This identifier contains only the fixed fixture prefix and UUID hex.
            connection.execute(f"DROP SCHEMA {namespace} CASCADE")
        finally:
            connection.close()


def test_independent_connections_atomically_claim_one_after_slot(database: str) -> None:
    first, second = ScheduleStore(database), ScheduleStore(database)
    try:
        created = first.create(_plan(), {"input": "original"}, now=100)
        barrier = Barrier(2)

        def claim(store: ScheduleStore) -> Any:
            barrier.wait(timeout=10)
            return store.claim_due(now=102, owner=uuid4().hex, schedule_id=created.schedule_id)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(claim, value) for value in (first, second)]
            results = [future.result(timeout=15) for future in futures]
        selected = [record for record in results if record is not None]
        assert len(selected) == 1
        assert selected[0].schedule_id == created.schedule_id
        assert selected[0]._claim.occurrence_key == "slot:1"
    finally:
        first.cancel(created.schedule_id)
        first.close()
        second.close()


def test_expired_claim_recovers_original_handle_and_fences_old_owner(database: str) -> None:
    store = ScheduleStore(database)
    try:
        created = store.create(_plan(), {"original": [1, 2]}, now=100, owner_principal="trusted")
        first = store.claim_due(
            now=102, lease_seconds=2, owner="first", schedule_id=created.schedule_id
        )
        assert first is not None and first._claim is not None
        handle = store.bind_invocation(first, engine="test", now=102)
        second = store.claim_due(
            now=105, lease_seconds=10, owner="second", schedule_id=created.schedule_id
        )
        assert second is not None and second._claim is not None
        recovered = store.bind_invocation(second, engine="test", now=105)
        assert recovered.invocation_id == handle.invocation_id
        assert recovered.owner_principal == "trusted"
        assert store.invocations.read_input(recovered) == {"original": [1, 2]}
        with pytest.raises(ScheduleOperationConflict):
            store.bind_invocation(first, engine="test", now=105)
        with pytest.raises(ScheduleOperationConflict):
            store.renew(first._claim, now=105)
        assert (
            store.connection.execute(
                "SELECT COUNT(*) FROM schedule_slots WHERE schedule_id=?", (created.schedule_id,)
            ).fetchone()[0]
            == 1
        )
    finally:
        store.cancel(created.schedule_id)
        store.close()


@pytest.mark.parametrize(
    "boundary", ["invocation-inserted-before-binding", "binding-before-commit"]
)
def test_invocation_input_and_slot_binding_roll_back_together(database: str, boundary: str) -> None:
    store = ScheduleStore(database)
    try:
        created = store.create(_plan(), None, now=100)
        claimed = store.claim_due(now=102, schedule_id=created.schedule_id)
        assert claimed is not None

        def stop(name: str) -> None:
            if name == boundary:
                raise RuntimeError("simulated stop")

        store._failpoint = stop
        before = store.connection.execute("SELECT COUNT(*) FROM invocations").fetchone()[0]
        with pytest.raises(RuntimeError, match="simulated stop"):
            store.bind_invocation(claimed, engine="test", now=102)
        assert store.connection.execute("SELECT COUNT(*) FROM invocations").fetchone()[0] == before
        assert (
            store.connection.execute(
                "SELECT invocation_id FROM schedule_slots WHERE schedule_id=?",
                (created.schedule_id,),
            ).fetchone()[0]
            is None
        )
        store._failpoint = lambda _name: None
        recovered = store.bind_invocation(claimed, engine="test", now=102)
        assert store.invocations.read_input(recovered) is None
        assert (
            store.bind_invocation(claimed, engine="test", now=102).invocation_id
            == recovered.invocation_id
        )
    finally:
        store.cancel(created.schedule_id)
        store.close()


def test_every_uses_nominal_slots_before_prior_completion(database: str) -> None:
    store = ScheduleStore(database)
    try:
        created = store.create(_plan("every"), {"value": 1}, now=100)
        first = store.claim_due(now=102, schedule_id=created.schedule_id)
        assert first is not None and first._claim is not None
        original = store.bind_invocation(first, engine="test", now=102)
        second = store.claim_due(now=104, schedule_id=created.schedule_id)
        assert second is not None and second._claim is not None
        later = store.bind_invocation(second, engine="test", now=104)
        assert later.invocation_id != original.invocation_id
        assert original.status == "running"
        assert second._claim.occurrence_key == "slot:2"
        persisted = store.get(created.schedule_id)
        assert persisted is not None and persisted.next_run_at == 106
    finally:
        store.cancel(created.schedule_id)
        store.close()


def test_after_repeats_only_from_completed_invocation(database: str) -> None:
    store = ScheduleStore(database)
    try:
        created = store.create(_plan(), {}, now=100)
        first = store.claim_due(now=102, lease_seconds=100, schedule_id=created.schedule_id)
        assert first is not None and first._claim is not None
        handle = store.bind_invocation(first, engine="test", now=102)
        assert store.claim_due(now=150, schedule_id=created.schedule_id) is None
        handle.status = "completed"
        store.invocations.update(handle, completed_at=160)
        result = store.finish(
            created.schedule_id,
            invocation_id=handle.invocation_id,
            status="completed",
            claim=first._claim,
            now=160,
        )
        assert result.status == "active" and result.next_run_at == 162
        assert store.claim_due(now=161, schedule_id=created.schedule_id) is None
        second = store.claim_due(now=162, schedule_id=created.schedule_id)
        assert second is not None and second._claim is not None
        assert second._claim.occurrence_key == "slot:2"
    finally:
        store.cancel(created.schedule_id)
        store.close()


def test_cancel_fences_existing_claim_without_erasing_binding(database: str) -> None:
    store = ScheduleStore(database)
    try:
        created = store.create(_plan(), {}, now=100)
        first = store.claim_due(now=102, schedule_id=created.schedule_id)
        assert first is not None
        handle = store.bind_invocation(first, engine="test", now=102)
        assert store.cancel(created.schedule_id).status == "cancelled"
        with pytest.raises(ScheduleOperationConflict):
            store.bind_invocation(first, engine="test", now=103)
        assert handle.invocation_id in store.active_invocations(created.schedule_id)
        assert store.claim_due(now=1000, schedule_id=created.schedule_id) is None
    finally:
        store.close()


@pytest.mark.parametrize("evidence", ["invocations", "invocation_inputs", "schedule_bindings"])
def test_missing_bound_evidence_fails_before_claim_generation_changes(
    database: str, evidence: str
) -> None:
    store = ScheduleStore(database)
    try:
        created = store.create(_plan(), {}, now=100)
        first = store.claim_due(now=102, lease_seconds=2, schedule_id=created.schedule_id)
        assert first is not None
        handle = store.bind_invocation(first, engine="test", now=102)
        if evidence == "schedule_bindings":
            store.connection.execute(
                "DELETE FROM schedule_bindings WHERE slot_key="
                "(SELECT slot_key FROM schedule_slots WHERE invocation_id=?)",
                (handle.invocation_id,),
            )
        else:
            store.connection.execute(
                f"DELETE FROM {evidence} WHERE invocation_id=?", (handle.invocation_id,)
            )
        store.connection.commit()
        before = store.connection.execute(
            "SELECT generation FROM schedule_slots WHERE invocation_id=?", (handle.invocation_id,)
        ).fetchone()[0]
        with pytest.raises(ScheduleValidationError):
            store.claim_due(now=105, schedule_id=created.schedule_id)
        assert (
            store.connection.execute(
                "SELECT generation FROM schedule_slots WHERE invocation_id=?",
                (handle.invocation_id,),
            ).fetchone()[0]
            == before
        )
    finally:
        store.cancel(created.schedule_id)
        store.close()


def test_borrowed_invocation_connection_is_not_closed_by_schedule_store(database: str) -> None:
    invocations = InvocationStore(database)
    schedule = ScheduleStore(database, invocations=invocations)
    schedule.close()
    assert invocations.connection.execute("SELECT 1").fetchone()[0] == 1
    invocations.close()


def test_claim_before_first_binding_survives_close_and_reopens_same_slot(database: str) -> None:
    first = ScheduleStore(database)
    created = first.create(_plan(), {"original": True}, now=100)
    claim = first.claim_due(now=102, lease_seconds=2, schedule_id=created.schedule_id)
    assert claim is not None and claim._claim is not None
    first.close()
    reopened = ScheduleStore(database)
    try:
        recovered = reopened.claim_due(now=105, schedule_id=created.schedule_id)
        assert recovered is not None and recovered._claim is not None
        assert recovered._claim.occurrence_key == claim._claim.occurrence_key
        assert recovered._claim.generation == claim._claim.generation + 1
        handle = reopened.bind_invocation(recovered, engine="test", now=105)
        assert reopened.invocations.read_input(handle) == {"original": True}
    finally:
        reopened.cancel(created.schedule_id)
        reopened.close()


def test_terminal_completion_time_is_immutable_and_missing_evidence_fails(database: str) -> None:
    store = ScheduleStore(database)
    try:
        created = store.create(_plan(), {}, now=100)
        claim = store.claim_due(now=102, lease_seconds=100, schedule_id=created.schedule_id)
        assert claim is not None and claim._claim is not None
        handle = store.bind_invocation(claim, engine="test", now=102)
        store.invocations.update(handle, status="completed", completed_at=110)
        store.invocations.update(handle, status="completed", completed_at=120)
        assert store.invocations.terminal_completed_at(handle) == 110
        original = store.connection.execute(
            "SELECT payload FROM invocation_terminal_times WHERE invocation_id=?",
            (handle.invocation_id,),
        ).fetchone()[0]
        store.connection.execute(
            "DELETE FROM invocation_terminal_times WHERE invocation_id=?", (handle.invocation_id,)
        )
        store.connection.commit()
        with pytest.raises(WorkflowExecutionError):
            store.invocations.terminal_completed_at(handle)
        before = store.connection.execute(
            "SELECT generation FROM schedule_slots WHERE invocation_id=?", (handle.invocation_id,)
        ).fetchone()[0]
        with pytest.raises(ScheduleValidationError):
            store.claim_due(now=203, schedule_id=created.schedule_id)
        assert (
            store.connection.execute(
                "SELECT generation FROM schedule_slots WHERE invocation_id=?",
                (handle.invocation_id,),
            ).fetchone()[0]
            == before
        )
        store.connection.execute(
            "INSERT INTO invocation_terminal_times(invocation_id,payload) VALUES (?,?)",
            (handle.invocation_id, original),
        )
        store.connection.commit()
    finally:
        store.cancel(created.schedule_id)
        store.close()


def test_invalid_terminal_clock_rolls_back_common_status_and_timestamp(database: str) -> None:
    store = ScheduleStore(database)
    try:
        created = store.create(_plan(), {}, now=100)
        claim = store.claim_due(now=102, schedule_id=created.schedule_id)
        assert claim is not None
        handle = store.bind_invocation(claim, engine="test", now=102)
        with pytest.raises(WorkflowExecutionError):
            store.invocations.update(handle, status="completed", completed_at=float("nan"))
        persisted = store.invocations.get(handle.invocation_id)
        assert persisted is not None and persisted.status == "running"
        assert (
            store.connection.execute(
                "SELECT 1 FROM invocation_terminal_times WHERE invocation_id=?",
                (handle.invocation_id,),
            ).fetchone()
            is None
        )
    finally:
        store.cancel(created.schedule_id)
        store.close()


def test_trigger_cycle_binding_and_projected_input_share_outer_transaction(database: str) -> None:
    source = _plan().source
    source["schedule"] = {"on": {"one": {"with": {"type": "test.trigger"}}}}
    store = ScheduleStore(database)
    created = store.create(
        compile_workflow(source), {}, owner_principal="trusted", event_scope="trusted"
    )
    try:
        with pytest.raises(RuntimeError, match="outer stop"):
            with store.transactions.transaction() as connection:
                store.bind_trigger_in_transaction(
                    connection,
                    created.schedule_id,
                    "cycle:1",
                    [{"value": 1}],
                    engine="test",
                    workflow_fingerprint=created.workflow_fingerprint,
                    event_scope="trusted",
                )
                raise RuntimeError("outer stop")
        assert (
            store.connection.execute(
                "SELECT 1 FROM schedule_slots WHERE schedule_id=?", (created.schedule_id,)
            ).fetchone()
            is None
        )
        with store.transactions.transaction() as connection:
            handle = store.bind_trigger_in_transaction(
                connection,
                created.schedule_id,
                "cycle:1",
                [{"value": 1}],
                engine="test",
                workflow_fingerprint=created.workflow_fingerprint,
                event_scope="trusted",
            )
        assert handle.owner_principal == "trusted"
        assert store.invocations.read_input(handle) == [{"value": 1}]
        with store.transactions.transaction() as connection:
            replay = store.bind_trigger_in_transaction(
                connection,
                created.schedule_id,
                "cycle:1",
                [{"value": 1}],
                engine="test",
                event_scope="trusted",
            )
        assert replay.invocation_id == handle.invocation_id
        with pytest.raises(ScheduleOperationConflict):
            with store.transactions.transaction() as connection:
                store.bind_trigger_in_transaction(
                    connection,
                    created.schedule_id,
                    "cycle:1",
                    [{"value": 1}],
                    engine="other",
                    event_scope="trusted",
                )
        with pytest.raises(ScheduleOperationConflict):
            with store.transactions.transaction() as connection:
                store.bind_trigger_in_transaction(
                    connection,
                    created.schedule_id,
                    "cycle:2",
                    [],
                    engine="test",
                    event_scope="other",
                )
    finally:
        store.cancel(created.schedule_id)
        store.close()
