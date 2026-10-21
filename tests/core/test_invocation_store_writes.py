"""Independent store writers must retain a newer common invocation record."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from open_workflow_agent.errors import InvocationStateError, WorkflowExecutionError
from open_workflow_agent.persistence import InvocationStore


def _handle(store):
    return store.create(
        engine="test",
        session_id=None,
        user_id=None,
        workflow_name="competing-writers",
        workflow_version="1.0.0",
        workflow_fingerprint="definition",
    )


@pytest.mark.parametrize("committed", ["value", "null", "fault", "cancelled"])
@pytest.mark.parametrize("late", ["completed", "faulted"])
def test_stale_writer_cannot_replace_committed_terminal_state(tmp_path, committed, late):
    first = InvocationStore(tmp_path / "invocations.sqlite")
    other = InvocationStore(tmp_path / "invocations.sqlite")
    stale = _handle(first)
    fresh = other.get(stale.invocation_id)
    status = {"value": "completed", "null": "completed", "fault": "faulted"}.get(
        committed, "cancelled"
    )
    output = {"retained": True} if committed == "value" else None
    error = (
        {"code": committed, "message": "retained", "details": {}} if status != "completed" else None
    )
    try:
        other.update(fresh, status=status, output=output, error=error)
        with pytest.raises(InvocationStateError):
            first.update(stale, status=late, output={"late": True}, error={"late": True})
        saved = other.get(stale.invocation_id)
        assert saved.status == status and saved.output == output and saved.error == error
        assert stale.status == "running" and stale.output is None and stale.error is None
    finally:
        first.close()
        other.close()


def test_return_to_running_does_not_restore_an_old_writers_authority(tmp_path):
    first = InvocationStore(tmp_path / "invocations.sqlite")
    other = InvocationStore(tmp_path / "invocations.sqlite")
    stale = _handle(first)
    fresh = other.get(stale.invocation_id)
    try:
        other.update(fresh, status="waiting")
        other.update(fresh, status="running")
        with pytest.raises(InvocationStateError):
            first.update(stale, status="completed", output={"late": True})
        saved = other.get(stale.invocation_id)
        assert saved.status == "running" and saved.output is None
    finally:
        first.close()
        other.close()


def test_two_simultaneous_writers_retain_exactly_one_terminal_outcome(tmp_path):
    stores = [InvocationStore(tmp_path / "invocations.sqlite") for _ in range(2)]
    initial = _handle(stores[0])
    handles = [store.get(initial.invocation_id) for store in stores]
    barrier = Barrier(2)

    def write(index):
        barrier.wait(timeout=10)
        try:
            stores[index].update(handles[index], status="completed", output={"winner": index})
            return index
        except InvocationStateError:
            return None

    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(write, range(2)))
        winners = [index for index in results if index is not None]
        assert len(winners) == 1
        saved = stores[0].get(initial.invocation_id)
        assert saved.status == "completed" and saved.output == {"winner": winners[0]}
    finally:
        for store in stores:
            store.close()


@pytest.mark.parametrize("status", ["completed", "faulted", "cancelled"])
def test_duplicate_terminal_write_keeps_the_first_output_and_error(status):
    store = InvocationStore()
    handle = _handle(store)
    output = None
    error = {"code": status, "message": "first", "details": {}} if status != "completed" else None
    try:
        store.update(handle, status=status, output=output, error=error)
        same = store.update(handle, status=status, output={"late": True}, error={"late": True})
        assert same is handle
        assert same.output is None and same.error == error
        saved = store.get(handle.invocation_id)
        assert saved.output is None and saved.error == error
    finally:
        store.close()


def test_legacy_row_without_write_generation_or_creator_remains_readable(tmp_path):
    store = InvocationStore(tmp_path / "invocations.sqlite")
    handle = _handle(store)
    try:
        encoded = store.connection.execute(
            "SELECT payload FROM invocations WHERE invocation_id = ?", (handle.invocation_id,)
        ).fetchone()[0]
        payload = json.loads(encoded)
        payload.pop("_revision")
        payload.pop("owner_principal")
        store.connection.execute(
            "UPDATE invocations SET payload = ? WHERE invocation_id = ?",
            (json.dumps(payload), handle.invocation_id),
        )
        store.connection.commit()
        loaded = store.get(handle.invocation_id)
        assert loaded.owner_principal is None
        store.update(loaded, status="completed", output={"retained": True})
    finally:
        store.close()
    reopened = InvocationStore(tmp_path / "invocations.sqlite")
    try:
        loaded = reopened.get(handle.invocation_id)
        assert loaded.status == "completed" and loaded.output == {"retained": True}
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "identity", ["workflow_fingerprint", "engine_execution_reference", "owner_principal"]
)
def test_conditional_write_cannot_rebind_invocation_identity(identity):
    store = InvocationStore()
    handle = _handle(store)
    try:
        with pytest.raises(InvocationStateError, match="identity cannot be changed"):
            store.update(handle, **{identity: "changed"}, status="completed")
        assert store.get(handle.invocation_id) == handle and handle.status == "running"
    finally:
        store.close()


def test_stale_executor_cannot_refresh_nonterminal_state_to_acquire_write_authority(tmp_path):
    first = InvocationStore(tmp_path / "invocations.sqlite")
    other = InvocationStore(tmp_path / "invocations.sqlite")
    stale = _handle(first)
    fresh = other.get(stale.invocation_id)
    try:
        other.update(fresh, status="waiting")
        with pytest.raises(InvocationStateError):
            first.refresh(stale, terminal_only=True)
        assert stale.status == "running"
        with pytest.raises(InvocationStateError):
            first.update(stale, status="faulted", error={"late": True})
        assert other.get(stale.invocation_id).status == "waiting"
    finally:
        first.close()
        other.close()


@pytest.mark.parametrize(
    "corruption", ["json", "revision", "null-revision", "duplicate", "status", "identity"]
)
def test_corrupt_invocation_records_fail_with_a_sanitized_common_error(corruption):
    store = InvocationStore()
    handle = _handle(store)
    try:
        encoded = store.connection.execute(
            "SELECT payload FROM invocations WHERE invocation_id = ?", (handle.invocation_id,)
        ).fetchone()[0]
        payload = json.loads(encoded)
        if corruption == "revision":
            payload["_revision"] = "database-secret-must-not-leak"
        elif corruption == "null-revision":
            payload["_revision"] = None
        elif corruption == "status":
            payload["status"] = "waiting"
        elif corruption == "identity":
            payload["invocation_id"] = "different"
        encoded = '{"secret":"must-not-leak"' if corruption == "json" else json.dumps(payload)
        if corruption == "duplicate":
            encoded = encoded[:-1] + ', "status":"running"}'
        store.connection.execute(
            "UPDATE invocations SET payload = ? WHERE invocation_id = ?",
            (encoded, handle.invocation_id),
        )
        store.connection.commit()
        with pytest.raises(WorkflowExecutionError) as failure:
            store.get(handle.invocation_id)
        assert failure.value.message == "invocation state could not be recovered"
        assert failure.value.details == {}
    finally:
        store.close()
