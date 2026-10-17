"""Common invocation writes compare execution ownership inside the same statement."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from open_workflow_agent import persistence
from open_workflow_agent.a2a_tasks import project_a2a_task
from open_workflow_agent.errors import InvocationStateConflict
from open_workflow_agent.ownership import ExecutionOwnershipStore
from open_workflow_agent.persistence import ExecutionHandle, InvocationStore


@pytest.fixture(params=["sqlite", "postgresql"])
def datasource(request: pytest.FixtureRequest, tmp_path: Path) -> str:
    if request.param == "postgresql":
        value = os.getenv("OWA_TEST_POSTGRES_URL")
        if not value:
            pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
        return value
    return str(tmp_path / "runtime.sqlite3")


def _create(store: InvocationStore) -> ExecutionHandle:
    return store.create(
        engine="test",
        session_id=None,
        user_id=None,
        workflow_name="fence",
        workflow_version="1.0.0",
        workflow_fingerprint="definition",
    )


def _expire(store: ExecutionOwnershipStore, handle: ExecutionHandle) -> None:
    store.connection.execute(
        "UPDATE invocation_ownership SET expires_at = 1 WHERE invocation_id = ?",
        (handle.invocation_id,),
    )
    store.connection.commit()


def test_valid_owner_writes_remain_private_and_renewal_keeps_the_token_valid(
    datasource: str,
) -> None:
    invocations = InvocationStore(datasource)
    ownership = ExecutionOwnershipStore(datasource)
    try:
        handle = _create(invocations)
        lease = ownership.acquire(handle)
        handle._execution_lease = lease
        ownership.renew(lease, seconds=60)
        invocations.update(handle, status="waiting")
        saved = invocations.get(handle.invocation_id)
        assert saved is not None and saved.status == "waiting"
        assert saved._persisted_payload is not None
        assert saved._execution_lease is None
        assert lease.owner not in saved._persisted_payload
        assert "_execution_lease" not in saved._persisted_payload
        assert lease.owner not in json.dumps(project_a2a_task(handle))
    finally:
        invocations.close()
        ownership.close()


def test_expired_worker_cannot_write_with_unchanged_common_snapshot(datasource: str) -> None:
    invocations = InvocationStore(datasource)
    ownership = ExecutionOwnershipStore(datasource)
    try:
        handle = _create(invocations)
        handle._execution_lease = ownership.acquire(handle)
        before = handle._persisted_payload
        _expire(ownership, handle)
        with pytest.raises(InvocationStateConflict):
            invocations.update(handle, status="waiting")
        saved = invocations.get(handle.invocation_id)
        assert saved is not None and saved._persisted_payload == before
        assert handle.status == "running" and handle._persisted_payload == before
    finally:
        invocations.close()
        ownership.close()


def test_takeover_during_state_preparation_is_rejected_by_the_atomic_write(
    datasource: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    invocations = InvocationStore(datasource)
    ownership = ExecutionOwnershipStore(datasource)
    other = ExecutionOwnershipStore(datasource)
    try:
        handle = _create(invocations)
        lease = ownership.acquire(handle)
        handle._execution_lease = lease
        before = handle._persisted_payload
        real_encode = persistence._encode_handle

        def takeover(candidate: ExecutionHandle) -> str:
            encoded = real_encode(candidate)
            _expire(other, handle)
            successor = other.acquire(handle)
            assert successor.generation == lease.generation + 1
            return encoded

        with monkeypatch.context() as patch:
            patch.setattr(persistence, "_encode_handle", takeover)
            with pytest.raises(InvocationStateConflict):
                invocations.update(handle, status="completed", output={"stale": True})
        saved = invocations.get(handle.invocation_id)
        assert saved is not None and saved._persisted_payload == before
        assert handle.status == "running" and handle._persisted_payload == before
    finally:
        invocations.close()
        ownership.close()
        other.close()


def test_another_invocations_private_token_cannot_authorize_a_state_write(datasource: str) -> None:
    invocations = InvocationStore(datasource)
    ownership = ExecutionOwnershipStore(datasource)
    try:
        handle = _create(invocations)
        other = _create(invocations)
        handle._execution_lease = ownership.acquire(other)
        before = handle._persisted_payload
        with pytest.raises(InvocationStateConflict):
            invocations.update(handle, status="waiting")
        saved = invocations.get(handle.invocation_id)
        assert saved is not None and saved._persisted_payload == before
    finally:
        invocations.close()
        ownership.close()
