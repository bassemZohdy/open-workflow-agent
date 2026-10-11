"""Action admission and fork progress retain execution authority through commit."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import replace
from importlib import import_module
from pathlib import Path
from typing import Any

import pytest
from open_workflow_agent.effects import EffectIdentity, EffectJournal, effect_request_digest
from open_workflow_agent.errors import InvocationStateConflict
from open_workflow_agent.forks import ForkDecision, ForkDecisionStore, ForkIdentity
from open_workflow_agent.ownership import ExecutionOwnershipStore, owned_write_transaction
from open_workflow_agent.persistence import InvocationStore
from open_workflow_agent.storage import open_storage


@pytest.fixture(params=["sqlite", "postgresql"])
def datasource(request: pytest.FixtureRequest, tmp_path: Path) -> str:
    if request.param == "postgresql":
        value = os.getenv("OWA_TEST_POSTGRES_URL")
        if not value:
            pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
        return value
    return str(tmp_path / "runtime.sqlite3")


@pytest.mark.parametrize("takeover", [False, True])
@pytest.mark.parametrize("operation", ["effect", "winner", "drain"])
def test_stale_owner_cannot_create_or_reclaim_intents_or_change_fork_progress(
    datasource: str, takeover: bool, operation: str
) -> None:
    invocations = InvocationStore(datasource)
    ownership = ExecutionOwnershipStore(datasource)
    effects, forks = EffectJournal(datasource), ForkDecisionStore(datasource)
    try:
        handle = invocations.create(
            engine="test",
            session_id=None,
            user_id=None,
            workflow_name="fence",
            workflow_version="1.0.0",
            workflow_fingerprint="definition",
        )
        lease = ownership.acquire(handle)
        effect = EffectIdentity(
            handle.invocation_id, "definition", "/do/0/action", (("root", 1),), "http"
        )
        fork = ForkIdentity(handle.invocation_id, "definition", "/do/1/race", (("root", 1),))
        digest = effect_request_digest("http", {"request": True})
        decision = ForkDecision(0, "completed", {"winner": 0}, {}, None, None, (1,))
        effects.begin(effect, digest, "dispatch-owner", execution_lease=lease)
        forks.claim(fork, decision, execution_lease=lease)
        original_effect, original_fork = effects.get(effect), forks.get(fork)
        ownership.connection.execute(
            "UPDATE invocation_ownership SET expires_at = 1 WHERE invocation_id = ?",
            (handle.invocation_id,),
        )
        ownership.connection.commit()
        successor = ownership.acquire(handle) if takeover else None
        with pytest.raises(InvocationStateConflict):
            if operation == "effect":
                effects.begin(effect, digest, "late-dispatch", execution_lease=lease)
            elif operation == "winner":
                forks.claim(fork, replace(decision, output={"stale": True}), execution_lease=lease)
            else:
                forks.mark_drained(fork, execution_lease=lease)
        with pytest.raises(InvocationStateConflict):
            if operation == "effect":
                effects.begin(
                    replace(effect, task_reference="/do/2/new"),
                    digest,
                    "late",
                    execution_lease=lease,
                )
            elif operation == "winner":
                forks.claim(
                    replace(fork, task_reference="/do/2/new"), decision, execution_lease=lease
                )
            else:
                forks.mark_drained(fork, execution_lease=lease)
        assert effects.get(effect) == original_effect and forks.get(fork) == original_fork
        assert effects.get(replace(effect, task_reference="/do/2/new")) is None
        assert forks.get(replace(fork, task_reference="/do/2/new")) is None
        # An external outcome belongs to its original dispatch owner even after
        # expiry; retaining this truth must not authorize another action.
        completed = effects.finish(effect, "dispatch-owner", output={"accepted": True})
        assert completed.status == "completed" and completed.output == {"accepted": True}
        if successor is not None:
            ownership.verify(successor)
            assert (
                effects.begin(effect, digest, "successor", execution_lease=successor) == completed
            )
            forks.mark_drained(fork, execution_lease=successor)
            assert forks.get(fork) == replace(decision, drained=True)
    finally:
        effects.close()
        forks.close()
        invocations.close()
        ownership.close()


def test_progress_transaction_locks_authority_through_commit(datasource: str) -> None:
    invocations = InvocationStore(datasource)
    ownership = ExecutionOwnershipStore(datasource)
    progress, contender = (
        open_storage(datasource, "owa_runtime"),
        open_storage(datasource, "owa_runtime"),
    )
    try:
        handle = invocations.create(
            engine="test",
            session_id=None,
            user_id=None,
            workflow_name="atomic",
            workflow_version="1.0.0",
            workflow_fingerprint="definition",
        )
        lease = ownership.acquire(handle)
        if contender.is_postgresql:
            contender.execute("SET lock_timeout = '100ms'")
            lock_error: type[Any] = import_module("psycopg.errors").LockNotAvailable
        else:
            contender.execute("PRAGMA busy_timeout = 0")
            lock_error = sqlite3.OperationalError
        with owned_write_transaction(
            progress, lease, invocation_id=handle.invocation_id, workflow_fingerprint="definition"
        ):
            with pytest.raises(lock_error):
                contender.execute(
                    "UPDATE invocation_ownership SET expires_at = 1 WHERE invocation_id = ?",
                    (handle.invocation_id,),
                )
                contender.commit()
            contender.raw.rollback()
        # The contender can change authority immediately after the commit.
        contender.execute(
            "UPDATE invocation_ownership SET expires_at = 1 WHERE invocation_id = ?",
            (handle.invocation_id,),
        )
        contender.commit()
        with pytest.raises(InvocationStateConflict):
            ownership.verify(lease)
    finally:
        progress.close()
        contender.close()
        invocations.close()
        ownership.close()


def test_another_invocations_lease_cannot_authorize_an_action(datasource: str) -> None:
    invocations = InvocationStore(datasource)
    ownership = ExecutionOwnershipStore(datasource)
    effects = EffectJournal(datasource)
    try:
        handles = [
            invocations.create(
                engine="test",
                session_id=None,
                user_id=None,
                workflow_name="identity",
                workflow_version="1.0.0",
                workflow_fingerprint="definition",
            )
            for _ in range(2)
        ]
        lease = ownership.acquire(handles[0])
        identity = EffectIdentity(
            handles[1].invocation_id, "definition", "/action", (("root", 1),), "http"
        )
        with pytest.raises(InvocationStateConflict):
            effects.begin(
                identity, effect_request_digest("http", {}), "dispatch", execution_lease=lease
            )
        assert effects.get(identity) is None
    finally:
        effects.close()
        invocations.close()
        ownership.close()
