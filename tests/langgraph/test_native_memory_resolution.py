"""Explicit decisions retain actual native evidence across provider/process exit."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import replace

import pytest
from open_workflow_agent.effects import EffectIdentity
from open_workflow_agent.errors import InvocationStateError, MemoryError
from open_workflow_agent.memory_mutations import MemoryMutation
from open_workflow_agent.memory_resolution import MemoryWriteResolution

pytest.importorskip("langgraph.store.base")
from open_workflow_agent_langgraph.native_memory_provider import open_native_memory_provider
from test_native_memory_write_recovery import runtime as _runtime_fixture

runtime = _runtime_fixture


async def dispatch(runtime, committed):
    services, _engine, provider, collection = runtime
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
    mutation = MemoryMutation.put(
        EffectIdentity(
            handle.invocation_id,
            handle.workflow_fingerprint,
            "/do/write",
            (("/do/write", 1),),
            "call:agent",
        ),
        provider.binding,
        "owa/principal-memory/v1:" + hashlib.sha256(b"alice").hexdigest(),
        collection,
        "record",
        {"version": 1},
    )
    original = services.memory_writes._backends[provider.binding]
    calls = []

    async def uncertain(request, current):
        calls.append(True)
        if committed:
            await original.apply(request, current)
        raise RuntimeError("secret lost acknowledgement")

    services.memory_writes.register(replace(original, apply=uncertain))
    with pytest.raises(InvocationStateError):
        await services.memory_writes.mutate(handle, mutation)
    return handle, mutation, calls


@pytest.mark.asyncio
@pytest.mark.parametrize("committed", [False, True])
async def test_reopened_actual_provider_resolves_without_rewriting(runtime, committed):
    from test_native_memory import namespace

    services, _engine, provider, collection = runtime
    handle, mutation, calls = await dispatch(runtime, committed)
    services.execution_ownership.release(handle._execution_lease)
    handle._execution_lease = services.execution_ownership.acquire(handle, seconds=300)
    decision = MemoryWriteResolution(
        "resolution-1", "alice", "complete" if committed else "abandon_unknown", not committed
    )
    async with open_native_memory_provider(services, provider_id="preferences", version="1.0.0"):
        audit = await services.memory_writes.resolve(handle, mutation, decision)
        assert await services.memory_writes.resolve(handle, mutation, decision) == audit
        services.effect_journal.verify_invocation(handle.invocation_id, handle.workflow_fingerprint)
        if committed:
            # Later data survives both resolution retry and the old logical write.
            newer = replace(
                mutation,
                parent=replace(mutation.parent, occurrence=(("/do/write", 2),)),
                value_json='{"version":2}',
            )
            await services.memory_writes.mutate(handle, newer)
            await services.memory_writes.mutate(handle, mutation)
        else:
            with pytest.raises(MemoryError):
                await services.memory_writes.mutate(handle, mutation)
    item = await provider.store.aget(namespace("alice", collection), "record", refresh_ttl=False)
    assert (None if item is None else item.value) == ({"version": 2} if committed else None)
    assert calls == [True]
    assert services.model.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("committed", [False, True])
async def test_process_exit_after_resolution_commit_preserves_decision_and_native_data(
    runtime, committed
):
    services, _engine, _provider, _collection = runtime
    handle, mutation, calls = await dispatch(runtime, committed)
    services.execution_ownership.release(handle._execution_lease)
    # Development harness only: exit after the common decision/outcome commit,
    # without native checkpointing or normal provider/service cleanup.
    worker = r"""
import asyncio,json,os,sys
from open_workflow_agent.effects import EffectJournal
from open_workflow_agent.memory_mutations import MemoryMutation
from open_workflow_agent.memory_resolution import MemoryWriteResolution
from open_workflow_agent.memory_writes import MemoryWrites,MemoryMutationBackend
from open_workflow_agent.ownership import ExecutionOwnershipStore
from open_workflow_agent.persistence import InvocationStore
from open_workflow_agent_langgraph.native_memory_writes import (
    inspect_native_marker,mutation_storage_identity)
async def main():
    common,native,encoded,decision=sys.argv[1:]
    request=MemoryMutation.from_descriptor(json.loads(encoded))
    invocations=InvocationStore(common)
    ownership=ExecutionOwnershipStore(common)
    journal=EffectJournal(common,invocations=invocations)
    coordinator=MemoryWrites(journal,invocations)
    handle=invocations.get(request.parent.invocation_id)
    handle._execution_lease=ownership.acquire(handle,seconds=300)
    async def forbidden(*args):
        raise AssertionError('resolution attempted SDK redispatch')
    coordinator.register(MemoryMutationBackend(request.binding,mutation_storage_identity(native),
        forbidden,lambda request:inspect_native_marker(native,request)))
    resolution=MemoryWriteResolution.from_descriptor(json.loads(decision))
    await coordinator.resolve(handle,request,resolution)
    os._exit(0)
if sys.platform=='win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
asyncio.run(main())
"""
    decision = MemoryWriteResolution(
        "resolution-1", "alice", "complete" if committed else "abandon_unknown", not committed
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            worker,
            services.execution_ownership._database,
            services.memory_provider_database_path("langgraph", create_namespace=False),
            json.dumps(mutation.descriptor),
            json.dumps(decision.descriptor),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    services.execution_ownership.connection.execute(
        "UPDATE invocation_ownership SET expires_at=1 WHERE invocation_id=?",
        (handle.invocation_id,),
    )
    services.execution_ownership.connection.commit()
    handle._execution_lease = services.execution_ownership.acquire(handle, seconds=300)
    async with open_native_memory_provider(services, provider_id="preferences", version="1.0.0"):
        services.effect_journal.verify_invocation(handle.invocation_id, handle.workflow_fingerprint)
        await services.memory_writes.resolve(handle, mutation, decision)
        if committed:
            await services.memory_writes.mutate(handle, mutation)
        else:
            with pytest.raises(MemoryError):
                await services.memory_writes.mutate(handle, mutation)
    assert calls == [True]
