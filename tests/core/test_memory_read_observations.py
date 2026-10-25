"""Private provider reads retain a bounded value under common execution ownership."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import asdict, replace

import pytest
from open_workflow_agent import memory_reads
from open_workflow_agent.effects import EffectIdentity, EffectJournal
from open_workflow_agent.errors import (
    InvocationStateConflict,
    InvocationStateError,
    WorkflowExecutionError,
)
from open_workflow_agent.memory_reads import MemoryReadBinding, MemoryReadObservations
from open_workflow_agent.ownership import ExecutionOwnershipStore
from open_workflow_agent.persistence import InvocationStore


@pytest.fixture(params=["sqlite", "postgresql"])
def records(request, tmp_path):
    database = str(tmp_path / "common.sqlite3")
    if request.param == "postgresql":
        database = os.getenv("OWA_TEST_POSTGRES_URL")
        if not database:
            pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    invocations = InvocationStore(database)
    ownership = ExecutionOwnershipStore(database)
    journal = EffectJournal(database, invocations=invocations)
    handle = invocations.create(
        engine="test",
        session_id=None,
        user_id=None,
        owner_principal="alice",
        workflow_name="memory",
        workflow_version="1.0.0",
        workflow_fingerprint="definition",
    )
    handle._execution_lease = ownership.acquire(handle)
    parent = EffectIdentity(
        handle.invocation_id,
        handle.workflow_fingerprint,
        "/do/read",
        (("/do/read", 1),),
        "call:agent",
    )
    observations = MemoryReadObservations(journal, invocations)
    options = dict(
        binding=MemoryReadBinding("preferences", "1.0.0", request.param),
        namespace="owa/principal-memory/v1:" + hashlib.sha256(b"alice").hexdigest(),
        collection="preferences",
        key="color",
        max_record_bytes=65_536,
    )
    try:
        yield observations, handle, parent, options, ownership, database
    finally:
        journal.close()
        ownership.close()
        invocations.close()


def counts(records):
    observations, handle, *_ = records
    connection = observations.connection
    own = connection.execute(
        "SELECT COUNT(*) FROM workflow_effect_invocations WHERE invocation_id=?",
        (handle.invocation_id,),
    ).fetchone()[0]
    values = 0
    if memory_reads._table_exists(connection):
        values = connection.execute(
            "SELECT COUNT(*) FROM memory_read_observations WHERE invocation_id=?",
            (handle.invocation_id,),
        ).fetchone()[0]
    return own, values


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, {"color": "雪", "nested": [1, True]}])
async def test_first_committed_value_survives_reopen_and_caller_mutation(records, value):
    observations, handle, parent, options, _, database = records
    calls = 0
    receipts = []

    async def fetch():
        nonlocal calls
        calls += 1
        return value

    async def observer(identity, receipt):
        assert observations.journal.get(identity) == receipt
        receipts.append((identity, receipt))

    first = await observations.read(handle, parent, **options, fetch=fetch, observer=observer)
    if first is not None:
        first["nested"].append("caller")
    invocations = InvocationStore(database)
    journal = EffectJournal(database, invocations=invocations)
    try:
        other = MemoryReadObservations(journal, invocations)
        restored = invocations.get(handle.invocation_id)
        restored._execution_lease = handle._execution_lease
        second = await other.read(restored, parent, **options, fetch=fetch)
        assert second == value
        assert calls == 1 and len(receipts) == 1
        assert counts(records) == (1, 1)
        journal.verify_invocation(handle.invocation_id, handle.workflow_fingerprint)
    finally:
        journal.close()
        invocations.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["id", "version", "backend", "max_record_bytes"])
async def test_binding_drift_fails_before_fetch(records, changed):
    observations, handle, parent, options, *_ = records
    calls = 0

    async def fetch():
        nonlocal calls
        calls += 1
        return {"color": "blue"}

    await observations.read(handle, parent, **options, fetch=fetch)
    if changed == "max_record_bytes":
        options[changed] = 100
    else:
        binding = options["binding"]
        options["binding"] = replace(
            binding, **{changed: "memory" if changed == "backend" else "changed"}
        )
    with pytest.raises(InvocationStateError):
        await observations.read(handle, parent, **options, fetch=fetch)
    assert calls == 1 and counts(records) == (1, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage", ["value", "digest", "request", "oversize", "index", "receipt", "observation"]
)
async def test_corruption_blocks_read_and_global_recovery_before_fetch(records, damage):
    observations, handle, parent, options, *_ = records
    calls = 0

    async def fetch():
        nonlocal calls
        calls += 1
        return {"color": "blue"}

    await observations.read(handle, parent, **options, fetch=fetch)
    connection = observations.connection
    key = connection.execute(
        "SELECT identity FROM memory_read_observations WHERE invocation_id=?",
        (handle.invocation_id,),
    ).fetchone()[0]
    if damage in {"index", "receipt", "observation"}:
        table = {
            "index": "workflow_effect_invocations",
            "receipt": "workflow_effects",
            "observation": "memory_read_observations",
        }[damage]
        connection.execute(f"DELETE FROM {table} WHERE identity=?", (key,))
    else:
        column, value = {
            "value": ("value_json", '{"color":"red"}'),
            "digest": ("value_digest", "0" * 64),
            "request": ("request_json", "{}"),
            "oversize": ("value_json", "x" * 65_537),
        }[damage]
        connection.execute(
            f"UPDATE memory_read_observations SET {column}=? WHERE identity=?", (value, key)
        )
    connection.commit()
    before = counts(records)
    with pytest.raises((InvocationStateError, WorkflowExecutionError)):
        await observations.read(handle, parent, **options, fetch=fetch)
    with pytest.raises(WorkflowExecutionError):
        observations.journal.verify_invocation(handle.invocation_id, handle.workflow_fingerprint)
    assert calls == 1 and counts(records) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("cached,stage", [(False, "before"), (True, "before"), (False, "during")])
async def test_ownership_loss_never_returns_cached_or_new_values(records, cached, stage):
    observations, handle, parent, options, ownership, _ = records
    calls = 0

    def expire():
        ownership.connection.execute(
            "UPDATE invocation_ownership SET expires_at=1 WHERE invocation_id=?",
            (handle.invocation_id,),
        )
        ownership.connection.commit()

    async def fetch():
        nonlocal calls
        calls += 1
        if stage == "during":
            expire()
            ownership.acquire(handle)
        return {"value": 1}

    if cached:
        await observations.read(handle, parent, **options, fetch=fetch)
    if stage == "before":
        expire()
        ownership.acquire(handle)
    with pytest.raises(InvocationStateConflict):
        await observations.read(handle, parent, **options, fetch=fetch)
    assert calls == int(cached or stage == "during")
    assert counts(records) == ((1, 1) if cached else (0, 0))


@pytest.mark.asyncio
async def test_concurrent_readers_first_commit_wins_without_holding_common_lock(records):
    observations, handle, parent, options, ownership, _ = records
    first_started, second_done = asyncio.Event(), asyncio.Event()

    async def slow():
        first_started.set()
        await second_done.wait()
        return {"value": "late"}

    async def fast():
        # A separate connection can renew ownership while a provider is awaited.
        ownership.renew(handle._execution_lease, seconds=60)
        return {"value": "first"}

    pending = asyncio.create_task(observations.read(handle, parent, **options, fetch=slow))
    await asyncio.wait_for(first_started.wait(), timeout=3)
    try:
        first = await observations.read(handle, parent, **options, fetch=fast)
    finally:
        second_done.set()
    assert first == await pending == {"value": "first"}
    assert counts(records) == (1, 1)


@pytest.mark.asyncio
async def test_atomic_failure_does_not_memoize_a_value(records, monkeypatch):
    observations, handle, parent, options, *_ = records
    calls = 0

    async def fetch():
        nonlocal calls
        calls += 1
        return {"attempt": calls}

    real_insert = memory_reads.insert_completed_receipt

    def broken(*args):
        real_insert(*args)
        raise RuntimeError("injected receipt failure")

    with monkeypatch.context() as patch:
        patch.setattr(memory_reads, "insert_completed_receipt", broken)
        with pytest.raises(RuntimeError, match="injected"):
            await observations.read(handle, parent, **options, fetch=fetch)
    assert counts(records) == (0, 0)
    assert await observations.read(handle, parent, **options, fetch=fetch) == {"attempt": 2}


@pytest.mark.asyncio
async def test_quota_and_logical_occurrences_are_durable(records):
    observations, handle, parent, options, *_ = records
    calls = 0

    async def fetch():
        nonlocal calls
        calls += 1
        return {"attempt": calls}

    for occurrence in range(1, memory_reads.MAX_READS + 1):
        identity = replace(parent, occurrence=((parent.task_reference, occurrence),))
        assert await observations.read(handle, identity, **options, fetch=fetch) == {
            "attempt": occurrence
        }
    assert await observations.read(handle, parent, **options, fetch=fetch) == {"attempt": 1}
    with pytest.raises(InvocationStateError):
        await observations.read(handle, parent, **{**options, "key": "another"}, fetch=fetch)
    assert calls == memory_reads.MAX_READS
    observations.journal.verify_invocation(handle.invocation_id, handle.workflow_fingerprint)


@pytest.mark.asyncio
async def test_observer_failure_keeps_first_committed_observation(records):
    observations, handle, parent, options, *_ = records
    calls = 0

    async def fetch():
        nonlocal calls
        calls += 1
        return None

    def observer(*_):
        raise RuntimeError("lost after commit")

    with pytest.raises(RuntimeError, match="lost after commit"):
        await observations.read(handle, parent, **options, fetch=fetch, observer=observer)
    assert await observations.read(handle, parent, **options, fetch=fetch) is None
    assert calls == 1 and counts(records) == (1, 1)


@pytest.mark.asyncio
async def test_abrupt_process_exit_after_commit_recovers_without_provider_read(records):
    observations, handle, parent, options, ownership, database = records
    ownership.release(handle._execution_lease)
    # Test harness process, not a workflow executable operation. The child exits
    # without cleanup after committing a value, leaving its lease behind.
    worker = r"""
import asyncio, json, os, sys
from open_workflow_agent.effects import EffectIdentity, EffectJournal
from open_workflow_agent.memory_reads import MemoryReadBinding, MemoryReadObservations
from open_workflow_agent.ownership import ExecutionOwnershipStore
from open_workflow_agent.persistence import InvocationStore
async def main():
    database, invocation_id, parent_json, options_json = sys.argv[1:]
    invocations = InvocationStore(database)
    ownership = ExecutionOwnershipStore(database)
    journal = EffectJournal(database, invocations=invocations)
    handle = invocations.get(invocation_id)
    handle._execution_lease = ownership.acquire(handle)
    options = json.loads(options_json)
    options['binding'] = MemoryReadBinding(**options['binding'])
    async def fetch():
        return {'value': 'before crash'}
    value = await MemoryReadObservations(journal, invocations).read(
        handle, EffectIdentity.from_value(json.loads(parent_json)), **options, fetch=fetch)
    assert value == {'value': 'before crash'}
    os._exit(0)
asyncio.run(main())
"""
    encoded_options = {**options, "binding": asdict(options["binding"])}
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            worker,
            database,
            handle.invocation_id,
            json.dumps(asdict(parent)),
            json.dumps(encoded_options),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    ownership.connection.execute(
        "UPDATE invocation_ownership SET expires_at=1 WHERE invocation_id=?",
        (handle.invocation_id,),
    )
    ownership.connection.commit()
    handle._execution_lease = ownership.acquire(handle)

    async def forbidden():
        pytest.fail("recovery re-read a changed provider after the committed observation")

    assert await observations.read(handle, parent, **options, fetch=forbidden) == {
        "value": "before crash"
    }
    observations.journal.verify_invocation(handle.invocation_id, handle.workflow_fingerprint)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["no_lease", "foreign_owner", "namespace"])
async def test_unowned_or_rebound_read_cannot_fetch_or_create_observations(records, invalid):
    observations, handle, parent, options, *_ = records
    if invalid == "no_lease":
        handle._execution_lease = None
    elif invalid == "foreign_owner":
        handle.owner_principal = "bob"
        options["namespace"] = "owa/principal-memory/v1:" + hashlib.sha256(b"bob").hexdigest()
    else:
        options["namespace"] = "owa/principal-memory/v1:" + hashlib.sha256(b"bob").hexdigest()

    async def forbidden():
        pytest.fail("unowned read reached provider")

    with pytest.raises(InvocationStateError):
        await observations.read(handle, parent, **options, fetch=forbidden)
    assert counts(records) == (0, 0)
