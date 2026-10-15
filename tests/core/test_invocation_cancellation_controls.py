"""Cancellation commits its terminal snapshot and lease revocation atomically."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from open_workflow_agent.errors import InvocationStateConflict, WorkflowExecutionError
from open_workflow_agent.ownership import ExecutionOwnershipStore
from open_workflow_agent.persistence import ExecutionHandle, InvocationStore
from open_workflow_agent.storage import StorageConnection

ERROR = {"code": "invocation_cancelled", "message": "authorized cancellation", "details": {}}


@pytest.fixture(params=["sqlite", "postgresql"])
def stores(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Iterator[tuple[InvocationStore, ExecutionOwnershipStore]]:
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if request.param == "postgresql" else None
    if request.param == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    invocations = InvocationStore(datasource or tmp_path / "runtime.sqlite3")
    ownership = ExecutionOwnershipStore(datasource or tmp_path / "runtime.sqlite3")
    try:
        yield invocations, ownership
    finally:
        invocations.close()
        ownership.close()


def _create(store: InvocationStore, parent: ExecutionHandle | None = None) -> ExecutionHandle:
    return store.create(
        engine="test",
        session_id=None,
        user_id="correlation",
        owner_principal="principal",
        workflow_name="atomic-control",
        workflow_version="1.0.0",
        workflow_fingerprint="definition",
        parent_invocation_id=parent.invocation_id if parent else None,
        parent_task_reference="/do/0/child" if parent else None,
    )


def _snapshot(store: InvocationStore, handle: ExecutionHandle) -> tuple[Any, ...]:
    return tuple(
        store.connection.execute(statement, (handle.invocation_id,)).fetchall()
        for statement in (
            "SELECT payload,status FROM invocations WHERE invocation_id=?",
            "SELECT payload FROM invocation_terminal_times WHERE invocation_id=?",
            "SELECT engine,fingerprint,owner,generation,expires_at "
            "FROM invocation_ownership WHERE invocation_id=?",
        )
    )


def _cancel(
    store: InvocationStore,
    handle: ExecutionHandle,
    parent: ExecutionHandle,
    mode: str,
) -> ExecutionHandle:
    if mode == "operator":
        return store.cancel_authoritatively(handle, error=ERROR, completed_at=1234.5)
    return store.cancel_under_parent_control(
        handle,
        parent,
        execution_lease=parent._execution_lease,
        error=ERROR,
        completed_at=1234.5,
    )


@pytest.mark.parametrize("mode", ["operator", "parent"])
def test_cancel_commits_terminal_instant_and_revokes_active_child_owner(
    stores: tuple[InvocationStore, ExecutionOwnershipStore], mode: str
) -> None:
    invocations, ownership = stores
    parent = _create(invocations)
    child = _create(invocations, parent)
    parent._execution_lease = ownership.acquire(parent, seconds=60)
    old_child_lease = ownership.acquire(child, seconds=60)
    child._execution_lease = old_child_lease
    parent_before = _snapshot(invocations, parent)

    assert _cancel(invocations, child, parent, mode) is child
    saved = invocations.get(child.invocation_id)
    assert saved is not None and saved.status == "cancelled"
    assert saved.output is None and saved.error == ERROR
    assert invocations.terminal_completed_at(saved) == 1234.5
    assert _snapshot(invocations, child)[2][0][-1] == 0
    with pytest.raises(InvocationStateConflict):
        ownership.renew(old_child_lease, seconds=60)
    assert _snapshot(invocations, parent) == parent_before

    before = _snapshot(invocations, child)
    _cancel(invocations, child, parent, mode)
    assert _snapshot(invocations, child) == before


@pytest.mark.parametrize("mode", ["operator", "parent"])
@pytest.mark.parametrize("boundary", ["terminal_timestamp", "lease_revocation"])
def test_cancel_transaction_rolls_back_all_rows_and_live_handle_after_partial_write(
    stores: tuple[InvocationStore, ExecutionOwnershipStore],
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    boundary: str,
) -> None:
    invocations, ownership = stores
    parent = _create(invocations)
    child = _create(invocations, parent)
    parent._execution_lease = ownership.acquire(parent, seconds=60)
    child_lease = ownership.acquire(child, seconds=60)
    before = _snapshot(invocations, child)
    expected_payload = child._persisted_payload
    actual_execute = StorageConnection.execute
    injected: list[str] = []

    def fail_after_write(
        connection: StorageConnection, statement: str, parameters: tuple[Any, ...] = ()
    ) -> Any:
        result = actual_execute(connection, statement, parameters)
        match = (
            statement.startswith("INSERT INTO invocation_terminal_times")
            if boundary == "terminal_timestamp"
            else statement.startswith("UPDATE invocation_ownership SET expires_at=0")
        )
        if connection is invocations.connection and match:
            injected.append(statement)
            raise RuntimeError("controlled storage failure after durable row mutation")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(StorageConnection, "execute", fail_after_write)
        with pytest.raises(
            WorkflowExecutionError, match="cancellation control could not be stored"
        ):
            _cancel(invocations, child, parent, mode)
    assert len(injected) == 1
    assert _snapshot(invocations, child) == before
    assert child.status == "running" and child.error is None
    assert child._persisted_payload == expected_payload
    # The pre-control worker retains authority after the transaction rollback.
    ownership.renew(child_lease, seconds=60)


@pytest.mark.parametrize("stale", ["parent_snapshot", "lease_takeover", "terminal_parent"])
def test_parent_control_rejects_stale_authority_before_changing_child(
    stores: tuple[InvocationStore, ExecutionOwnershipStore], stale: str
) -> None:
    invocations, ownership = stores
    parent = _create(invocations)
    child = _create(invocations, parent)
    lease = ownership.acquire(parent, seconds=60)
    parent._execution_lease = lease
    ownership.acquire(child, seconds=60)
    before = _snapshot(invocations, child)
    if stale == "lease_takeover":
        ownership.connection.execute(
            "UPDATE invocation_ownership SET expires_at=1 WHERE invocation_id=?",
            (parent.invocation_id,),
        )
        ownership.connection.commit()
        successor = ownership.acquire(parent, seconds=60)
        assert successor.generation == lease.generation + 1
    else:
        fresh = invocations.get(parent.invocation_id)
        assert fresh is not None
        fresh._execution_lease = lease
        invocations.update(
            fresh,
            status="waiting" if stale == "parent_snapshot" else "completed",
            output=None if stale == "parent_snapshot" else {"finished": True},
        )
    with pytest.raises(InvocationStateConflict):
        _cancel(invocations, child, parent, "parent")
    assert _snapshot(invocations, child) == before
    assert child.status == "running" and child.error is None


@pytest.mark.parametrize("mode", ["operator", "parent"])
@pytest.mark.parametrize("outcome", ["value", "null", "fault"])
def test_late_cancel_preserves_immutable_completed_or_faulted_outcome(
    stores: tuple[InvocationStore, ExecutionOwnershipStore], mode: str, outcome: str
) -> None:
    invocations, ownership = stores
    parent = _create(invocations)
    child = _create(invocations, parent)
    parent._execution_lease = ownership.acquire(parent, seconds=60)
    lease = ownership.acquire(child, seconds=60)
    fresh = invocations.get(child.invocation_id)
    assert fresh is not None
    fresh._execution_lease = lease
    invocations.update(
        fresh,
        status="faulted" if outcome == "fault" else "completed",
        output={"original": True} if outcome == "value" else None,
        error={"code": "original", "message": "original fault", "details": {}}
        if outcome == "fault"
        else None,
        completed_at=1000.0,
    )
    before = _snapshot(invocations, child)
    result = _cancel(invocations, child, parent, mode)
    assert result.status == fresh.status and result.output == fresh.output
    assert result.error == fresh.error and invocations.terminal_completed_at(result) == 1000.0
    assert _snapshot(invocations, child) == before


def test_parent_control_cannot_cancel_an_unrelated_invocation(
    stores: tuple[InvocationStore, ExecutionOwnershipStore],
) -> None:
    invocations, ownership = stores
    parent = _create(invocations)
    unrelated = _create(invocations)
    parent._execution_lease = ownership.acquire(parent, seconds=60)
    ownership.acquire(unrelated, seconds=60)
    before = _snapshot(invocations, unrelated)
    with pytest.raises(InvocationStateConflict, match="child control identity is inconsistent"):
        _cancel(invocations, unrelated, parent, "parent")
    assert _snapshot(invocations, unrelated) == before
