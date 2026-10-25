"""Private workflow writes retain common receipts and verify native evidence."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import uuid
from dataclasses import replace

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.effects import EffectIdentity
from open_workflow_agent.errors import InvocationStateError, OwaError
from open_workflow_agent.execution_authority import authority_scope
from open_workflow_agent.memory_mutations import TABLE as NATIVE_TABLE
from open_workflow_agent.memory_mutations import MemoryMutation
from open_workflow_agent.memory_writes import TABLE as COMMON_TABLE
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow, generate_default_workflow

pytest.importorskip("langgraph.store.base")
from open_workflow_agent_langgraph import LangGraphWorkflowEngine
from open_workflow_agent_langgraph.native_memory_provider import open_native_memory_provider


@pytest.fixture(params=["sqlite", "postgresql"])
async def runtime(request, tmp_path):
    from test_native_memory import configuration

    config = configuration()
    config.execution_authorization.policy.rules[0].actions.extend(["memory.add", "memory.delete"])
    root = tmp_path
    if request.param == "postgresql":
        datasource = os.getenv("OWA_TEST_POSTGRES_URL")
        if not datasource:
            pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
        config.persistence.datasource = datasource
        root = None
    services = RuntimeServices(config, model=FakeModel(), database_root=root)
    engine = LangGraphWorkflowEngine()
    await engine.initialize(services)
    await engine._timer_dispatcher.stop()
    try:
        async with open_native_memory_provider(
            services, provider_id="preferences", version="1.0.0"
        ) as provider:
            yield services, engine, provider, "writes_" + uuid.uuid4().hex
    finally:
        await engine.shutdown()
        services.close()


async def marker_count(provider, handle):
    store = provider.store
    if provider.binding.backend == "postgresql":
        async with store._cursor(pipeline=False) as cursor:
            await cursor.execute(
                f"SELECT COUNT(*) AS count FROM {NATIVE_TABLE} WHERE invocation_id=%s",
                (handle.invocation_id,),
            )
            return (await cursor.fetchone())["count"]
    async with store.conn.execute(
        f"SELECT COUNT(*) FROM {NATIVE_TABLE} WHERE invocation_id=?", (handle.invocation_id,)
    ) as cursor:
        return (await cursor.fetchone())[0]


@pytest.mark.asyncio
async def test_actual_workflow_put_delete_and_repeat_have_common_completion_receipts(runtime):
    from test_native_memory import AGENT, invoke, namespace

    services, engine, provider, collection = runtime
    writer = provider.writer(collection)

    async def write(_payload, _context):
        await writer.put("record", {"version": 1})
        await writer.put("record", {"version": 1})
        await writer.delete("record")
        await writer.delete("record")
        return {"ok": True}

    services.catalog.register(AGENT, write)
    plan = compile_workflow(generate_default_workflow())
    result = await invoke(services, engine, plan)
    assert result.status == "completed", result.error
    assert result.output == {"ok": True}
    assert (
        await provider.store.aget(namespace("alice", collection), "record", refresh_ttl=False)
        is None
    )
    assert await marker_count(provider, result) == 2
    phases = services.effect_journal.connection.execute(
        f"SELECT phase FROM {COMMON_TABLE} WHERE invocation_id=?", (result.invocation_id,)
    ).fetchall()
    assert [row[0] for row in phases] == ["completed", "completed"]
    services.effect_journal.verify_invocation(result.invocation_id, plan.fingerprint)
    assert services.model.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["put", "delete"])
async def test_denial_precedes_common_intent_and_native_mutation(runtime, operation):
    from test_native_memory import AGENT, invoke

    services, engine, provider, collection = runtime
    denied = "memory.add" if operation == "put" else "memory.delete"
    services.config.execution_authorization.policy.rules[0].actions.remove(denied)
    writer = provider.writer(collection)

    async def write(_payload, _context):
        if operation == "put":
            await writer.put("record", {"version": 1})
        else:
            await writer.delete("record")
        return {"unexpected": True}

    services.catalog.register(AGENT, write)
    result = await invoke(services, engine, compile_workflow(generate_default_workflow()))
    assert result.status == "faulted"
    assert result.error["code"] == "execution_authorization_denied"
    assert await marker_count(provider, result) == 0
    assert (
        services.effect_journal.connection.execute(
            "SELECT 1 FROM workflow_effect_invocations WHERE invocation_id=? AND payload LIKE ?",
            (result.invocation_id, "%policy:memory-mutation:v1:%"),
        ).fetchone()
        is None
    )


@pytest.mark.asyncio
async def test_missing_native_marker_blocks_native_checkpoint_resume_before_function_replay(
    runtime,
):
    from test_native_memory import AGENT, authority, invoke

    services, engine, provider, collection = runtime
    writer = provider.writer(collection)
    calls = []

    async def write(_payload, _context):
        calls.append(True)
        await writer.put("record", {"version": 1})
        return {"ok": True}

    services.catalog.register(AGENT, write)
    definition = generate_default_workflow()
    definition["do"].append({"pause": {"wait": {"seconds": 3600}}})
    plan = compile_workflow(definition)
    result = await invoke(services, engine, plan)
    assert result.status == "waiting", result.error
    if provider.binding.backend == "postgresql":
        async with provider.store._cursor(pipeline=False) as cursor:
            await cursor.execute(
                f"DELETE FROM {NATIVE_TABLE} WHERE invocation_id=%s", (result.invocation_id,)
            )
    else:
        await provider.store.conn.execute(
            f"DELETE FROM {NATIVE_TABLE} WHERE invocation_id=?", (result.invocation_id,)
        )
        await provider.store.conn.commit()
    with authority_scope(authority(services, "alice")), pytest.raises(OwaError):
        await engine.resume(
            services.invocations.get(result.invocation_id), {"try": "changed"}, plan
        )
    assert calls == [True]


@pytest.mark.asyncio
async def test_actual_native_commit_without_common_ack_reconciles_after_provider_reopen(runtime):
    import hashlib

    services, _, provider, collection = runtime
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
    mutation = MemoryMutation.put(
        parent,
        provider.binding,
        "owa/principal-memory/v1:" + hashlib.sha256(b"alice").hexdigest(),
        collection,
        "record",
        {"version": 1},
    )
    original = services.memory_writes._backends[provider.binding]
    calls = []

    async def lost_ack(request, current):
        calls.append(True)
        await original.apply(request, current)
        raise RuntimeError("secret acknowledgement failure")

    services.memory_writes.register(replace(original, apply=lost_ack))
    with pytest.raises(InvocationStateError):
        await services.memory_writes.mutate(handle, mutation)
    async with open_native_memory_provider(services, provider_id="preferences", version="1.0.0"):
        await services.memory_writes.mutate(handle, mutation)
    assert calls == [True]
    assert services.effect_journal.get(mutation.identity).status == "completed"
    assert await marker_count(provider, handle) == 1


@pytest.mark.asyncio
async def test_concurrent_writers_keep_original_principals_despite_ambient_substitution(runtime):
    from test_native_memory import AGENT, authority, invoke, namespace

    services, engine, provider, collection = runtime
    writer = provider.writer(collection)

    async def write(_payload, context):
        actor = context.metadata["authority"]["principal"]["identity"]
        with authority_scope(authority(services, "bob" if actor == "alice" else "alice")):
            await writer.put("record", {"owner": actor})
        return {"owner": actor}

    services.catalog.register(AGENT, write)
    plan = compile_workflow(generate_default_workflow())
    results = await asyncio.gather(
        *(invoke(services, engine, plan, actor) for actor in ("alice", "bob"))
    )
    assert all(result.status == "completed" for result in results), [
        result.error for result in results
    ]
    for actor in ("alice", "bob"):
        item = await provider.store.aget(namespace(actor, collection), "record", refresh_ttl=False)
        assert item is not None and item.value == {"owner": actor}


@pytest.mark.asyncio
async def test_abrupt_exit_between_native_commit_and_common_completion_recovers_without_rewrite(
    runtime,
):
    import hashlib

    services, _, provider, collection = runtime
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
    services.execution_ownership.release(handle._execution_lease)
    parent = EffectIdentity(
        handle.invocation_id,
        handle.workflow_fingerprint,
        "/do/write",
        (("/do/write", 1),),
        "call:agent",
    )
    mutation = MemoryMutation.put(
        parent,
        provider.binding,
        "owa/principal-memory/v1:" + hashlib.sha256(b"alice").hexdigest(),
        collection,
        "record",
        {"version": 1},
    )
    # Test harness only: exit without cleanup after the native transaction,
    # before the coordinator can store its common completion.
    worker = r"""
import asyncio,json,os,sys
from open_workflow_agent.effects import EffectJournal
from open_workflow_agent.memory_mutations import MemoryMutation
from open_workflow_agent.memory_writes import MemoryWrites,MemoryMutationBackend
from open_workflow_agent.ownership import ExecutionOwnershipStore
from open_workflow_agent.persistence import InvocationStore
from open_workflow_agent_langgraph.native_memory_mutations import apply_native_mutation
from open_workflow_agent_langgraph.native_memory_writes import (
    inspect_native_marker,mutation_storage_identity)
async def main():
    common,native,encoded=sys.argv[1:]
    request=MemoryMutation.from_descriptor(json.loads(encoded))
    invocations=InvocationStore(common)
    ownership=ExecutionOwnershipStore(common)
    journal=EffectJournal(common,invocations=invocations)
    coordinator=MemoryWrites(journal,invocations)
    handle=invocations.get(request.parent.invocation_id)
    handle._execution_lease=ownership.acquire(handle,seconds=300)
    async def crash_after_commit(request,current):
        await apply_native_mutation(native,request,
            ownership.native_fence(current._execution_lease),
            invocations=invocations,handle=current)
        os._exit(0)
    coordinator.register(MemoryMutationBackend(request.binding,
        mutation_storage_identity(native),crash_after_commit,
        lambda request:inspect_native_marker(native,request)))
    await coordinator.mutate(handle,request)
    raise AssertionError('child did not exit at the native commit boundary')
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
            json.dumps(mutation.descriptor),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert services.effect_journal.get(mutation.identity).status == "pending"
    services.execution_ownership.connection.execute(
        "UPDATE invocation_ownership SET expires_at=1 WHERE invocation_id=?",
        (handle.invocation_id,),
    )
    services.execution_ownership.connection.commit()
    handle._execution_lease = services.execution_ownership.acquire(handle, seconds=300)
    newer = replace(
        mutation, parent=replace(parent, occurrence=(("/do/write", 2),)), value_json='{"version":2}'
    )
    await services.memory_writes.mutate(handle, newer)
    await services.memory_writes.mutate(handle, mutation)
    assert services.effect_journal.get(mutation.identity).status == "completed"
    from test_native_memory import namespace

    item = await provider.store.aget(namespace("alice", collection), "record", refresh_ttl=False)
    assert item is not None and item.value == {"version": 2}
    assert await marker_count(provider, handle) == 2
