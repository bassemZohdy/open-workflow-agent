"""Common ownership contention, stale generations and crash-expiry recovery."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier
from typing import Any
from uuid import uuid4

import pytest
from open_workflow_agent.errors import InvocationStateConflict, WorkflowExecutionError
from open_workflow_agent.ownership import ExecutionLease, ExecutionOwnershipStore
from open_workflow_agent.persistence import ExecutionHandle, InvocationStore
from open_workflow_agent.storage import StorageConnection


@pytest.fixture(params=["sqlite", "postgresql"])
def datasource(request: pytest.FixtureRequest, tmp_path: Path) -> str:
    if request.param == "postgresql":
        value = os.getenv("OWA_TEST_POSTGRES_URL")
        if not value:
            pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
        return value
    return str(tmp_path / "runtime.sqlite3")


def _handle(datasource: str) -> ExecutionHandle:
    invocations = InvocationStore(datasource)
    try:
        return invocations.create(
            engine="test",
            session_id=None,
            user_id=None,
            workflow_name="ownership",
            workflow_version="1.0.0",
            workflow_fingerprint="definition",
        )
    finally:
        invocations.close()


def _expire(store: ExecutionOwnershipStore, handle: ExecutionHandle) -> None:
    # Advance the persisted expiry deterministically; no sleeps or host-clock assumptions.
    store.connection.execute(
        "UPDATE invocation_ownership SET expires_at = 1 WHERE invocation_id = ?",
        (handle.invocation_id,),
    )
    store.connection.commit()


def test_independent_connections_reopen_and_reject_competing_owners(datasource: str) -> None:
    handle = _handle(datasource)
    first = ExecutionOwnershipStore(datasource)
    second = ExecutionOwnershipStore(datasource)
    try:
        lease = first.acquire(handle)
        second.verify(lease)
        with pytest.raises(InvocationStateConflict):
            second.acquire(handle)
        renewed = second.renew(lease, seconds=60)
        assert renewed.owner == lease.owner and renewed.generation == lease.generation
        assert renewed.expires_at > lease.expires_at
        first.close()
        first = ExecutionOwnershipStore(datasource)
        first.verify(lease)
        first.release(lease)
        acquired = second.acquire(handle)
        assert acquired.owner != lease.owner and acquired.generation == lease.generation + 1
        for operation in (first.verify, first.renew, first.release):
            with pytest.raises(InvocationStateConflict):
                operation(lease)
        second.verify(acquired)
    finally:
        first.close()
        second.close()


def test_expired_owner_cannot_renew_release_or_replace_its_successor(datasource: str) -> None:
    handle = _handle(datasource)
    first = ExecutionOwnershipStore(datasource)
    other = ExecutionOwnershipStore(datasource)
    try:
        old = first.acquire(handle)
        _expire(other, handle)
        for operation in (first.verify, first.renew, first.release):
            with pytest.raises(InvocationStateConflict):
                operation(old)
        current = other.acquire(handle)
        assert current.generation == old.generation + 1 and current.owner != old.owner
        for operation in (first.verify, first.renew, first.release):
            with pytest.raises(InvocationStateConflict):
                operation(old)
        other.verify(current)
    finally:
        first.close()
        other.close()


def test_simultaneous_claims_have_one_winner(datasource: str) -> None:
    handle = _handle(datasource)
    ready = Barrier(2)

    def claim(_: int) -> ExecutionLease | None:
        store = ExecutionOwnershipStore(datasource)
        try:
            ready.wait(timeout=10)
            try:
                return store.acquire(handle)
            except InvocationStateConflict:
                return None
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as workers:
        claims = list(workers.map(claim, (1, 2)))
    assert sum(lease is not None for lease in claims) == 1


@pytest.mark.parametrize("changed", ["engine", "workflow_fingerprint"])
def test_expiry_does_not_allow_identity_rebinding(datasource: str, changed: str) -> None:
    handle = _handle(datasource)
    store = ExecutionOwnershipStore(datasource)
    try:
        old = store.acquire(handle)
        _expire(store, handle)
        changes: dict[str, Any] = {changed: "different"}
        with pytest.raises(InvocationStateConflict):
            store.acquire(replace(handle, **changes))
        new = store.acquire(handle)
        assert new.generation == old.generation + 1
    finally:
        store.close()


@pytest.mark.parametrize("operation", ["verify", "renew", "release"])
def test_forged_owner_or_generation_cannot_control_lease(datasource: str, operation: str) -> None:
    handle = _handle(datasource)
    store = ExecutionOwnershipStore(datasource)
    try:
        lease = store.acquire(handle)
        for forged in (replace(lease, owner=uuid4().hex), replace(lease, generation=2)):
            with pytest.raises(InvocationStateConflict):
                getattr(store, operation)(forged)
        store.verify(lease)
    finally:
        store.close()


@pytest.mark.parametrize("duration", [True, 0, -1, float("nan"), float("inf"), 3601])
def test_invalid_duration_does_not_mutate_ownership(duration: float) -> None:
    store = ExecutionOwnershipStore()
    handle = _handle(":memory:")
    try:
        with pytest.raises(ValueError):
            store.acquire(handle, seconds=duration)
        lease = store.acquire(handle)
        with pytest.raises(ValueError):
            store.renew(lease, seconds=duration)
        store.verify(lease)
    finally:
        store.close()


def test_closed_storage_uses_sanitized_common_error() -> None:
    handle = _handle(":memory:")
    store = ExecutionOwnershipStore()
    lease = store.acquire(handle)
    store.close()
    for operation in (
        lambda: store.acquire(handle),
        lambda: store.verify(lease),
        lambda: store.renew(lease),
        lambda: store.release(lease),
    ):
        with pytest.raises(WorkflowExecutionError) as error:
            operation()
        assert handle.invocation_id not in str(error.value)
        assert lease.owner not in str(error.value)
        assert error.value.__cause__ is None


@pytest.mark.parametrize("operation", ["acquire", "renew", "release"])
def test_failed_sqlite_commit_rolls_back_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    datasource = str(tmp_path / "runtime.sqlite3")
    handle = _handle(datasource)
    store = ExecutionOwnershipStore(datasource)
    try:
        lease = store.acquire(handle) if operation != "acquire" else None
        before = store.connection.execute("SELECT * FROM invocation_ownership").fetchall()

        def fail_commit(_: StorageConnection) -> None:
            raise RuntimeError("private-database-credential")

        with monkeypatch.context() as patch:
            patch.setattr(StorageConnection, "commit", fail_commit)
            with pytest.raises(WorkflowExecutionError) as error:
                if operation == "acquire":
                    store.acquire(handle)
                else:
                    assert lease is not None
                    getattr(store, operation)(lease)
            assert "private-database-credential" not in str(error.value)
            assert error.value.__cause__ is None
        assert store.connection.execute("SELECT * FROM invocation_ownership").fetchall() == before
    finally:
        store.close()


@pytest.mark.parametrize("changes", [{"generation": True}, {"generation": 0}, {"owner": "bad"}])
def test_invalid_private_tokens_cannot_impersonate_an_owner(changes: dict[str, Any]) -> None:
    store = ExecutionOwnershipStore()
    try:
        lease = store.acquire(_handle(":memory:"))
        with pytest.raises(ValueError):
            replace(lease, **changes)
        store.verify(lease)
    finally:
        store.close()


def test_clock_skew_does_not_grant_execution_ownership(
    datasource: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    handle = _handle(datasource)
    store = ExecutionOwnershipStore(datasource)
    other = ExecutionOwnershipStore(datasource)
    try:
        with monkeypatch.context() as patch:
            patch.setattr("time.time", lambda: 1e30)
            lease = store.acquire(handle)
            assert lease.expires_at < 1e30
            with pytest.raises(InvocationStateConflict):
                other.acquire(handle)
        with monkeypatch.context() as patch:
            patch.setattr("time.time", lambda: 0)
            _expire(other, handle)
            with pytest.raises(InvocationStateConflict):
                store.renew(lease)
            assert other.acquire(handle).generation == lease.generation + 1
    finally:
        store.close()
        other.close()


@pytest.mark.parametrize("operation", ["verify", "renew", "release"])
def test_corrupt_infinite_expiry_cannot_be_adopted_or_repaired(
    datasource: str, operation: str
) -> None:
    handle = _handle(datasource)
    store = ExecutionOwnershipStore(datasource)
    try:
        lease = store.acquire(handle)
        store.connection.execute(
            "UPDATE invocation_ownership SET expires_at = ? WHERE invocation_id = ?",
            (float("inf"), handle.invocation_id),
        )
        store.connection.commit()
        with pytest.raises(InvocationStateConflict):
            getattr(store, operation)(lease)
        assert store.connection.execute(
            "SELECT expires_at FROM invocation_ownership WHERE invocation_id = ?",
            (handle.invocation_id,),
        ).fetchone()[0] == float("inf")
    finally:
        store.close()
