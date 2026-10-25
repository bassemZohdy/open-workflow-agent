"""Actual SDK transactions commit a fenced mutation and its marker together."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import threading
import uuid
from dataclasses import asdict, replace

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.effects import EffectIdentity
from open_workflow_agent.errors import InvocationStateConflict, InvocationStateError, ToolError
from open_workflow_agent.memory_mutations import (
    MAX_MUTATIONS,
    TABLE,
    MemoryMutation,
    MemoryMutationMarker,
)
from open_workflow_agent.services import RuntimeServices

pytest.importorskip("langgraph.store.base")
from open_workflow_agent_langgraph.native_memory_mutations import apply_native_mutation
from open_workflow_agent_langgraph.native_memory_provider import open_native_memory_provider


@pytest.fixture(params=["sqlite", "postgresql"])
async def mutation_runtime(request, tmp_path):
    config = RuntimeConfig(model={"provider": "fake"})
    root = tmp_path
    if request.param == "postgresql":
        datasource = os.getenv("OWA_TEST_POSTGRES_URL")
        if not datasource:
            pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
        config.persistence.datasource = datasource
        root = None
    services = RuntimeServices(config, model=FakeModel(), database_root=root)
    handle = services.invocations.create(
        engine="langgraph",
        session_id=None,
        user_id=None,
        owner_principal="alice",
        workflow_name="memory",
        workflow_version="1.0.0",
        workflow_fingerprint="definition",
    )
    handle._execution_lease = services.execution_ownership.acquire(handle, seconds=300)
    parent = EffectIdentity(
        handle.invocation_id,
        handle.workflow_fingerprint,
        "/do/write",
        (("/do/write", 1),),
        "call:agent",
    )
    namespace = "owa/principal-memory/v1:" + hashlib.sha256(b"alice").hexdigest()
    collection = "test_" + uuid.uuid4().hex
    async with open_native_memory_provider(
        services, provider_id="preferences", version="1.0.0"
    ) as provider:
        mutation = MemoryMutation.put(
            parent, provider.binding, namespace, collection, "record", {"version": 1}
        )
        try:
            yield services, handle, provider, mutation
        finally:
            services.close()


async def apply(runtime, mutation=None, handle=None):
    services, original, _provider, original_mutation = runtime
    handle = handle or original
    return await apply_native_mutation(
        services.memory_provider_database_path("langgraph", create_namespace=False),
        mutation or original_mutation,
        services.execution_ownership.native_fence(handle._execution_lease),
        invocations=services.invocations,
        handle=handle,
    )


async def value(runtime):
    _, _, provider, mutation = runtime
    namespace = ("owa_native_memory_v1", mutation.namespace.rsplit(":", 1)[1], mutation.collection)
    item = await provider.store.aget(namespace, mutation.key, refresh_ttl=False)
    return None if item is None else item.value


async def markers(runtime):
    _, handle, provider, _ = runtime
    store = provider.store
    if provider.binding.backend == "postgresql":
        async with store._cursor(pipeline=False) as cursor:
            await cursor.execute(
                f"SELECT identity,request_digest FROM {TABLE} WHERE invocation_id=%s",
                (handle.invocation_id,),
            )
            return [tuple(row.values()) for row in await cursor.fetchall()]
    async with store.conn.execute(
        f"SELECT identity,request_digest FROM {TABLE} WHERE invocation_id=?",
        (handle.invocation_id,),
    ) as cursor:
        return await cursor.fetchall()


@pytest.mark.asyncio
async def test_repeat_after_another_writer_never_restores_an_old_value(mutation_runtime):
    services, handle, provider, mutation = mutation_runtime
    first = await apply(mutation_runtime)
    assert first == MemoryMutationMarker.for_request(mutation)
    other = services.invocations.create(
        engine="langgraph",
        session_id=None,
        user_id=None,
        owner_principal="alice",
        workflow_name="memory",
        workflow_version="1.0.0",
        workflow_fingerprint="definition",
    )
    other._execution_lease = services.execution_ownership.acquire(other, seconds=300)
    newer = MemoryMutation.put(
        replace(mutation.parent, invocation_id=other.invocation_id),
        provider.binding,
        mutation.namespace,
        mutation.collection,
        mutation.key,
        {"version": 2},
    )
    await apply(mutation_runtime, newer, other)
    assert await value(mutation_runtime) == {"version": 2}
    # Reopen the SDK connection on every apply; the native marker survives it.
    assert await apply(mutation_runtime) == first
    assert await value(mutation_runtime) == {"version": 2}
    assert len(await markers(mutation_runtime)) == 1
    assert services.model.calls == []


@pytest.mark.asyncio
async def test_delete_and_missing_delete_have_atomic_markers(mutation_runtime):
    _, _, _, mutation = mutation_runtime
    await apply(mutation_runtime)
    delete = replace(mutation, kind="delete", value_json=None)
    marker = await apply(mutation_runtime, delete)
    assert await value(mutation_runtime) is None
    assert await apply(mutation_runtime, delete) == marker
    missing = replace(delete, key="missing")
    assert await apply(mutation_runtime, missing) == MemoryMutationMarker.for_request(missing)
    assert len(await markers(mutation_runtime)) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["value", "binding", "bound"])
async def test_changed_request_is_rejected_without_reapplying(mutation_runtime, change):
    _, _, _, mutation = mutation_runtime
    await apply(mutation_runtime)
    changed = {
        "value": replace(mutation, value_json='{"version":2}'),
        "binding": replace(mutation, binding=replace(mutation.binding, version="2.0.0")),
        "bound": replace(mutation, max_record_bytes=100),
    }[change]
    with pytest.raises(InvocationStateError):
        await apply(mutation_runtime, changed)
    assert await value(mutation_runtime) == {"version": 1}
    assert len(await markers(mutation_runtime)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("kind", ["put", "delete"])
async def test_stale_generation_cannot_write_or_reuse_marker(mutation_runtime, cached, kind):
    services, handle, _, mutation = mutation_runtime
    if kind == "delete":
        mutation = replace(mutation, kind="delete", value_json=None)
    if cached:
        await apply(mutation_runtime, mutation)
    connection = services.execution_ownership.connection
    connection.execute(
        "UPDATE invocation_ownership SET expires_at=1 WHERE invocation_id=?",
        (handle.invocation_id,),
    )
    connection.commit()
    services.execution_ownership.acquire(handle, seconds=300)
    with pytest.raises(InvocationStateConflict):
        await apply(mutation_runtime, mutation)
    assert len(await markers(mutation_runtime)) == int(cached)
    assert await value(mutation_runtime) == ({"version": 1} if cached and kind == "put" else None)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["exception", "expiry"])
async def test_failure_after_sdk_sql_rolls_back_both_value_and_marker(
    mutation_runtime, monkeypatch, failure
):
    _, handle, provider, _ = mutation_runtime
    base = type(provider.store)
    original = base._batch_put_ops
    written = []

    async def broken(self, operations, cursor):
        await original(self, operations, cursor)
        written.append(True)
        if failure == "exception":
            raise RuntimeError("secret provider detail")
        if provider.binding.backend == "postgresql":
            await cursor.execute(
                "UPDATE owa_runtime.invocation_ownership "
                "SET expires_at=EXTRACT(EPOCH FROM clock_timestamp())+0.05 WHERE invocation_id=%s",
                (handle.invocation_id,),
            )
        else:
            await cursor.execute(
                "UPDATE invocation_ownership "
                "SET expires_at=((julianday('now')-2440587.5)*86400.0)+0.05 WHERE invocation_id=?",
                (handle.invocation_id,),
            )
        await asyncio.sleep(0.1)

    monkeypatch.setattr(base, "_batch_put_ops", broken)
    with pytest.raises((ToolError, InvocationStateConflict)) as error:
        await apply(mutation_runtime)
    assert "secret" not in str(error.value)
    assert written == [True]
    assert await value(mutation_runtime) is None
    assert await markers(mutation_runtime) == []


@pytest.mark.asyncio
async def test_cancellation_drains_transaction_before_return_and_retry_does_not_rewrite(
    mutation_runtime, monkeypatch
):
    _, _, provider, _ = mutation_runtime
    base = type(provider.store)
    original = base._batch_put_ops
    started, release = threading.Event(), threading.Event()
    calls = []

    async def pause(self, operations, cursor):
        await original(self, operations, cursor)
        calls.append(True)
        started.set()
        assert await asyncio.to_thread(release.wait, 10)

    monkeypatch.setattr(base, "_batch_put_ops", pause)
    task = asyncio.create_task(apply(mutation_runtime))
    try:
        assert await asyncio.to_thread(started.wait, 10)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await value(mutation_runtime) == {"version": 1}
    assert len(await markers(mutation_runtime)) == 1
    await apply(mutation_runtime)
    assert calls == [True]


@pytest.mark.asyncio
async def test_concurrent_retries_and_last_quota_slot_are_serialized(mutation_runtime, monkeypatch):
    _, _, provider, mutation = mutation_runtime
    base = type(provider.store)
    original = base._batch_put_ops
    calls = []

    async def counted(self, *args):
        calls.append(True)
        await original(self, *args)

    monkeypatch.setattr(base, "_batch_put_ops", counted)
    results = await asyncio.gather(*(apply(mutation_runtime) for _ in range(3)))
    assert results == [MemoryMutationMarker.for_request(mutation)] * 3
    assert calls == [True]
    for index in range(2, MAX_MUTATIONS):
        await apply(mutation_runtime, replace(mutation, key=f"record{index}"))
    results = await asyncio.gather(
        *(apply(mutation_runtime, replace(mutation, key=key)) for key in ("last1", "last2")),
        return_exceptions=True,
    )
    assert sum(isinstance(result, MemoryMutationMarker) for result in results) == 1
    assert sum(isinstance(result, InvocationStateError) for result in results) == 1
    assert len(await markers(mutation_runtime)) == MAX_MUTATIONS
    assert len(calls) == MAX_MUTATIONS


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["principal", "namespace", "missing_lease"])
async def test_original_invocation_binding_is_required_before_sdk_write(mutation_runtime, change):
    _, handle, _, mutation = mutation_runtime
    if change == "principal":
        handle.owner_principal = "bob"
        mutation = replace(
            mutation, namespace="owa/principal-memory/v1:" + hashlib.sha256(b"bob").hexdigest()
        )
    elif change == "namespace":
        mutation = replace(
            mutation, namespace="owa/principal-memory/v1:" + hashlib.sha256(b"bob").hexdigest()
        )
    else:
        # Keep the fence argument well formed but make the common handle stale.
        from open_workflow_agent_langgraph.native_memory_mutations import apply_native_mutation

        services = mutation_runtime[0]
        fence = services.execution_ownership.native_fence(handle._execution_lease)
        handle._execution_lease = None
        with pytest.raises(InvocationStateError):
            await apply_native_mutation(
                services.memory_provider_database_path("langgraph", create_namespace=False),
                mutation,
                fence,
                invocations=services.invocations,
                handle=handle,
            )
        assert await markers(mutation_runtime) == []
        return
    with pytest.raises(InvocationStateError):
        await apply(mutation_runtime, mutation)
    assert await markers(mutation_runtime) == []


@pytest.mark.asyncio
async def test_abrupt_process_exit_after_native_commit_keeps_retry_from_rewriting(mutation_runtime):
    services, handle, _, mutation = mutation_runtime
    services.execution_ownership.release(handle._execution_lease)
    # Development test process, not a workflow executable operation.
    worker = r"""
import asyncio,json,os,sys
from open_workflow_agent.effects import EffectIdentity
from open_workflow_agent.memory_mutations import MemoryMutation
from open_workflow_agent.memory_reads import MemoryReadBinding
from open_workflow_agent.ownership import ExecutionOwnershipStore
from open_workflow_agent.persistence import InvocationStore
from open_workflow_agent_langgraph.native_memory_mutations import apply_native_mutation
async def main():
    common,native,encoded=sys.argv[1:]
    raw=json.loads(encoded)
    raw['parent']=EffectIdentity.from_value(raw['parent'])
    raw['binding']=MemoryReadBinding(**raw['binding'])
    request=MemoryMutation(**raw)
    invocations=InvocationStore(common)
    ownership=ExecutionOwnershipStore(common)
    handle=invocations.get(request.parent.invocation_id)
    handle._execution_lease=ownership.acquire(handle,seconds=300)
    await apply_native_mutation(
        native,request,ownership.native_fence(handle._execution_lease),
        invocations=invocations,handle=handle)
    os._exit(0)
if sys.platform=='win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
asyncio.run(main())
"""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            worker,
            services.execution_ownership._database,
            services.memory_provider_database_path("langgraph", create_namespace=False),
            json.dumps(asdict(mutation)),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    connection = services.execution_ownership.connection
    connection.execute(
        "UPDATE invocation_ownership SET expires_at=1 WHERE invocation_id=?",
        (handle.invocation_id,),
    )
    connection.commit()
    handle._execution_lease = services.execution_ownership.acquire(handle, seconds=300)
    assert await value(mutation_runtime) == {"version": 1}
    newer = replace(
        mutation,
        parent=replace(mutation.parent, occurrence=(("/do/write", 2),)),
        value_json='{"version":2}',
    )
    await apply(mutation_runtime, newer)
    assert await apply(mutation_runtime) == MemoryMutationMarker.for_request(mutation)
    assert await value(mutation_runtime) == {"version": 2}
    assert len(await markers(mutation_runtime)) == 2


@pytest.mark.asyncio
async def test_damaged_marker_rejects_retry_without_sdk_sql(mutation_runtime, monkeypatch):
    _, _, provider, mutation = mutation_runtime
    await apply(mutation_runtime)
    store = provider.store
    if provider.binding.backend == "postgresql":
        async with store._cursor(pipeline=False) as cursor:
            await cursor.execute(
                f"UPDATE {TABLE} SET request_digest=%s WHERE identity=%s",
                ("0" * 64, mutation.identity.key),
            )
    else:
        await store.conn.execute(
            f"UPDATE {TABLE} SET request_digest=? WHERE identity=?",
            ("0" * 64, mutation.identity.key),
        )
        await store.conn.commit()

    async def forbidden(*_):
        pytest.fail("damaged marker reached SDK mutation SQL")

    monkeypatch.setattr(type(store), "_batch_put_ops", forbidden)
    with pytest.raises(InvocationStateError):
        await apply(mutation_runtime)
    assert await value(mutation_runtime) == {"version": 1}
