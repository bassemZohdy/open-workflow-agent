from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from open_workflow_agent.effects import EffectJournal, effect_request_digest
from open_workflow_agent.errors import InvocationStateConflict, WorkflowExecutionError
from open_workflow_agent.ownership import ExecutionOwnershipStore
from open_workflow_agent.persistence import InvocationStore
from open_workflow_agent.timing import root_clock_identity


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_policy_decision_is_atomic_immutable_and_factory_runs_once(
    tmp_path: Path, monkeypatch: Any, backend: str
) -> None:
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    journal = EffectJournal(datasource or tmp_path / "runtime.sqlite3")
    identity = root_clock_identity(str(uuid4()), "fingerprint")
    digest = effect_request_digest(identity.operation, {"profile": "owa/timing/v1"})
    calls = 0

    def create() -> Any:
        nonlocal calls
        calls += 1
        return {"profile": "owa/timing/v1", "started_at": 1000.0}

    execute = journal.connection.execute

    def fail_second_write(connection: Any, sql: str, *args: Any, **kwargs: Any) -> Any:
        if "INSERT INTO workflow_effects(" in sql:
            raise RuntimeError("injected decision write failure")
        return execute(sql, *args, **kwargs)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(type(journal.connection), "execute", fail_second_write)
            with pytest.raises(WorkflowExecutionError, match="decision could not be stored"):
                journal.record_value(identity, digest, create)
        assert journal.get(identity) is None
        assert (
            journal.connection.execute(
                "SELECT identity FROM workflow_effect_invocations WHERE identity = ?",
                (identity.key,),
            ).fetchall()
            == []
        )
        assert calls == 1
        first = journal.record_value(identity, digest, create)
        second = journal.record_value(identity, digest, create)
        assert calls == 2
        assert first == second and first.status == "completed"
        assert first.output == {"profile": "owa/timing/v1", "started_at": 1000.0}
        journal.verify_invocation(identity.invocation_id, identity.workflow_fingerprint)
        with pytest.raises(WorkflowExecutionError, match="decision could not be stored"):
            journal.record_value(
                identity, effect_request_digest(identity.operation, {"changed": True}), create
            )
        assert calls == 2
    finally:
        journal.close()


def test_stale_owner_cannot_create_or_resample_a_policy_clock(tmp_path: Path) -> None:
    path = tmp_path / "runtime.sqlite3"
    store = InvocationStore(path)
    ownership = ExecutionOwnershipStore(path)
    journal = EffectJournal(path)
    handle = store.create(
        engine="langgraph",
        session_id=None,
        user_id=None,
        workflow_name="timing",
        workflow_version="1.0.0",
        workflow_fingerprint="fingerprint",
    )
    lease = ownership.acquire(handle)
    identity = root_clock_identity(handle.invocation_id, handle.workflow_fingerprint)
    digest = effect_request_digest(identity.operation, {"profile": "owa/timing/v1"})
    calls = 0

    def create() -> Any:
        nonlocal calls
        calls += 1
        return {"profile": "owa/timing/v1", "started_at": 1000.0}

    try:
        journal.record_value(identity, digest, create, execution_lease=lease)
        ownership.release(lease)
        next_lease = ownership.acquire(handle)
        with pytest.raises(InvocationStateConflict):
            journal.record_value(identity, digest, create, execution_lease=lease)
        assert calls == 1
        journal.record_value(identity, digest, create, execution_lease=next_lease)
        assert calls == 1
        ownership.release(next_lease)
    finally:
        journal.close()
        ownership.close()
        store.close()
