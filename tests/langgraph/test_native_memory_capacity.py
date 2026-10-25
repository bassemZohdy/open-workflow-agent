"""Actual native namespace transactions enforce budgets and bounded physical cleanup."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import os
import threading
import uuid

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.effects import EffectIdentity
from open_workflow_agent.errors import InvocationStateError, ToolError
from open_workflow_agent.memory_capacity import TABLE as NAMESPACE_TABLE
from open_workflow_agent.memory_capacity import MemoryNamespaceBounds
from open_workflow_agent.memory_mutations import MemoryMutation
from open_workflow_agent.memory_retention import MemoryRecordRetention
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
    try:
        async with open_native_memory_provider(
            services,
            provider_id="preferences",
            version="1",
            retention=MemoryRecordRetention("records", "1", 3600),
            namespace_limits=MemoryNamespaceBounds("capacity", "1", 3, 1024, 2),
        ) as provider:
            yield services, provider, "capacity_" + uuid.uuid4().hex
    finally:
        services.close()


def mutation(runtime, key="a", value=None, *, kind="put", actor="alice", collection=None):
    services, provider, original_collection = runtime
    handle = services.invocations.create(
        engine="langgraph",
        session_id=None,
        user_id=None,
        owner_principal=actor,
        workflow_name="capacity",
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
    namespace = "owa/principal-memory/v1:" + hashlib.sha256(actor.encode()).hexdigest()
    collection = collection or original_collection
    if kind == "put":
        request = MemoryMutation.put(
            parent,
            provider.binding,
            namespace,
            collection,
            key,
            {"value": key} if value is None else value,
            retention=provider.retention,
            namespace_limits=provider.namespace_limits,
        )
    else:
        request = MemoryMutation(
            parent,
            provider.binding,
            namespace,
            collection,
            "expired" if kind == "sweep" else key,
            kind,
            None,
            retention=provider.retention,
            namespace_limits=provider.namespace_limits,
        )
    return handle, request


async def apply(runtime, **kwargs):
    handle, request = mutation(runtime, **kwargs)
    await runtime[0].memory_writes.mutate(handle, request)
    return handle, request


def sdk_module(provider):
    return importlib.import_module(
        "langgraph.store.postgres.base"
        if provider.binding.backend == "postgresql"
        else "langgraph.store.sqlite.base"
    )


def prefix(runtime, actor="alice", collection=None):
    from test_native_memory import namespace

    return sdk_module(runtime[1])._namespace_to_text(namespace(actor, collection or runtime[2]))


async def keys(runtime, *, actor="alice", collection=None):
    provider = runtime[1]
    if provider.binding.backend == "postgresql":
        async with provider.store._cursor(pipeline=False) as cursor:
            await cursor.execute(
                "SELECT key FROM store WHERE prefix=%s ORDER BY key",
                (prefix(runtime, actor, collection),),
            )
            return [row["key"] for row in await cursor.fetchall()]
    async with provider.store.conn.execute(
        "SELECT key FROM store WHERE prefix=? ORDER BY key", (prefix(runtime, actor, collection),)
    ) as cursor:
        return [row[0] for row in await cursor.fetchall()]


async def expire(runtime, *, actor="alice", collection=None):
    provider = runtime[1]
    if provider.binding.backend == "postgresql":
        async with provider.store._cursor(pipeline=False) as cursor:
            await cursor.execute(
                "UPDATE store SET expires_at='2000-01-01T00:00:00Z' WHERE prefix=%s",
                (prefix(runtime, actor, collection),),
            )
    else:
        await provider.store.conn.execute(
            "UPDATE store SET expires_at='2000-01-01T00:00:00Z' WHERE prefix=?",
            (prefix(runtime, actor, collection),),
        )
        await provider.store.conn.commit()


@pytest.mark.asyncio
async def test_record_limit_replacement_and_explicit_delete_restore_capacity(runtime):
    services, provider, collection = runtime
    for key in "abc":
        await apply(runtime, key=key)
    rejected_handle, rejected = mutation(runtime, key="d")
    with pytest.raises(InvocationStateError):
        await services.memory_writes.mutate(rejected_handle, rejected)
    assert await keys(runtime) == list("abc")
    await apply(runtime, key="a", value={"replacement": True})
    await apply(runtime, key="b", kind="delete")
    await apply(runtime, key="d")
    assert await keys(runtime) == list("acd")
    # A failed dispatched request is not silently reused after space is freed.
    with pytest.raises(InvocationStateError):
        await services.memory_writes.mutate(rejected_handle, rejected)


@pytest.mark.asyncio
async def test_value_byte_budget_rolls_back_oversized_replacement(runtime):
    from test_native_memory import namespace

    services, provider, collection = runtime
    await apply(runtime, value={"original": True})
    handle, request = mutation(runtime, value={"huge": "x" * 2000})
    with pytest.raises(InvocationStateError):
        await services.memory_writes.mutate(handle, request)
    item = await provider.store.aget(namespace("alice", collection), "a", refresh_ttl=False)
    assert item.value == {"original": True}
    assert await keys(runtime) == ["a"]


@pytest.mark.asyncio
async def test_sweep_is_bounded_scope_safe_and_immutable_on_retry(runtime):
    services, _, collection = runtime
    originals = [await apply(runtime, key=key) for key in "abc"]
    await apply(runtime, key="a", actor="bob")
    await apply(runtime, key="a", collection=collection + "_other")
    await expire(runtime)
    await expire(runtime, actor="bob")
    await expire(runtime, collection=collection + "_other")
    handle, request = await apply(runtime, kind="sweep")
    assert await keys(runtime) == ["c"]
    assert await keys(runtime, actor="bob") == ["a"]
    assert await keys(runtime, collection=collection + "_other") == ["a"]
    await services.memory_writes.mutate(handle, request)
    assert await keys(runtime) == ["c"]
    await apply(runtime, kind="sweep")
    assert await keys(runtime) == []
    for original, _ in originals:
        services.effect_journal.verify_invocation(
            original.invocation_id, original.workflow_fingerprint
        )


@pytest.mark.asyncio
async def test_concurrent_distinct_invocations_cannot_exceed_last_namespace_slot(runtime):
    services = runtime[0]
    for key in "ab":
        await apply(runtime, key=key)
    attempts = [mutation(runtime, key=key) for key in "cde"]
    results = await asyncio.gather(
        *(services.memory_writes.mutate(handle, request) for handle, request in attempts),
        return_exceptions=True,
    )
    assert sum(result is None for result in results) == 1
    assert sum(isinstance(result, InvocationStateError) for result in results) == 2
    assert len(await keys(runtime)) == 3


@pytest.mark.asyncio
async def test_cleanup_failure_after_sdk_deletes_rolls_back_batch_and_marker(runtime, monkeypatch):
    services, provider, _ = runtime
    for key in "ab":
        await apply(runtime, key=key)
    await expire(runtime)
    module = importlib.import_module(
        "langgraph.store.postgres.aio"
        if provider.binding.backend == "postgresql"
        else "langgraph.store.sqlite.aio"
    )
    base = (
        module.AsyncPostgresStore
        if provider.binding.backend == "postgresql"
        else module.AsyncSqliteStore
    )
    original = base._batch_put_ops

    async def fail(self, operations, cursor):
        await original(self, operations, cursor)
        raise RuntimeError("secret cleanup failure")

    monkeypatch.setattr(base, "_batch_put_ops", fail)
    handle, request = mutation(runtime, kind="sweep")
    with pytest.raises(ToolError) as error:
        await services.memory_writes.mutate(handle, request)
    assert "secret" not in str(error.value)
    assert await keys(runtime) == list("ab")
    assert services.effect_journal.get(request.identity).status == "pending"


@pytest.mark.asyncio
async def test_unbounded_writer_cannot_bypass_a_sealed_namespace_policy(runtime):
    from dataclasses import replace

    from open_workflow_agent_langgraph.native_memory_mutations import apply_native_mutation

    services, _, _ = runtime
    await apply(runtime)
    handle, request = mutation(runtime, key="b")
    request = replace(request, namespace_limits=None)
    with pytest.raises(InvocationStateError):
        await apply_native_mutation(
            services.memory_provider_database_path("langgraph", create_namespace=False),
            request,
            services.execution_ownership.native_fence(handle._execution_lease),
            invocations=services.invocations,
            handle=handle,
        )
    assert await keys(runtime) == ["a"]


@pytest.mark.asyncio
async def test_actual_workflow_cleanup_checks_original_policy_and_replays_one_batch(runtime):
    from test_native_memory import AGENT, invoke

    services, provider, collection = runtime
    engine = LangGraphWorkflowEngine()
    await engine.initialize(services)
    writer = provider.writer(collection)

    async def write(_payload, _context):
        for key in "abc":
            await writer.put(key, {"value": key})
        await expire(runtime)
        await writer.sweep_expired()
        await writer.sweep_expired()
        return {"remaining": len(await keys(runtime))}

    services.catalog.register(AGENT, write)
    try:
        result = await invoke(services, engine, compile_workflow(generate_default_workflow()))
        assert result.status == "completed" and result.output == {"remaining": 1}, result.error
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_cleanup_denial_precedes_intent_and_deletion(runtime):
    from test_native_memory import AGENT, invoke

    services, provider, collection = runtime
    await apply(runtime)
    await expire(runtime)
    services.config.execution_authorization.policy.rules[0].actions.remove("memory.delete")
    engine = LangGraphWorkflowEngine()
    await engine.initialize(services)

    async def cleanup(_payload, _context):
        await provider.writer(collection).sweep_expired()

    services.catalog.register(AGENT, cleanup)
    try:
        result = await invoke(services, engine, compile_workflow(generate_default_workflow()))
        assert (
            result.status == "faulted" and result.error["code"] == "execution_authorization_denied"
        )
        assert await keys(runtime) == ["a"]
        assert (
            services.effect_journal.connection.execute(
                "SELECT 1 FROM workflow_effect_invocations "
                "WHERE invocation_id=? AND payload LIKE ?",
                (result.invocation_id, "%policy:memory-mutation:v1:%"),
            ).fetchone()
            is None
        )
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_changed_content_still_rejects_after_scope_permission_is_reused(runtime):
    from test_native_memory import AGENT, invoke, namespace

    services, provider, collection = runtime
    engine = LangGraphWorkflowEngine()
    await engine.initialize(services)
    writer = provider.writer(collection)

    async def write(_payload, _context):
        await writer.put("a", {"version": 1})
        try:
            await writer.put("a", {"version": 2})
        except InvocationStateError as error:
            return {"rejected": error.details["reason"]}
        return {"unexpected": True}

    services.catalog.register(AGENT, write)
    try:
        result = await invoke(services, engine, compile_workflow(generate_default_workflow()))
        assert result.status == "completed" and result.output == {"rejected": "request_changed"}, (
            result.error
        )
        item = await provider.store.aget(namespace("alice", collection), "a", refresh_ttl=False)
        assert item.value == {"version": 1}
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_new_writer_waits_for_selected_cleanup_and_old_retry_does_not_delete_new_value(
    runtime, monkeypatch
):
    from test_native_memory import namespace

    services, provider, collection = runtime
    for key in "ab":
        await apply(runtime, key=key)
    await expire(runtime)
    module = importlib.import_module(
        "langgraph.store.postgres.aio"
        if provider.binding.backend == "postgresql"
        else "langgraph.store.sqlite.aio"
    )
    base = (
        module.AsyncPostgresStore
        if provider.binding.backend == "postgresql"
        else module.AsyncSqliteStore
    )
    original = base._batch_put_ops
    selected, release = threading.Event(), threading.Event()

    async def pause(self, operations, cursor):
        if any(operation.value is None for _, operation in operations):
            selected.set()
            assert await asyncio.to_thread(release.wait, 10)
        await original(self, operations, cursor)

    monkeypatch.setattr(base, "_batch_put_ops", pause)
    sweep_handle, sweep = mutation(runtime, kind="sweep")
    cleanup = asyncio.create_task(services.memory_writes.mutate(sweep_handle, sweep))
    timer = None
    try:
        assert await asyncio.to_thread(selected.wait, 10)
        # SQLite's attached common owner lock may block the main loop while
        # admitting the second request. The test barrier must release independently.
        timer = threading.Timer(0.2, release.set)
        timer.start()
        await asyncio.gather(cleanup, apply(runtime, key="a", value={"new": True}))
    finally:
        release.set()
        if timer is not None:
            timer.cancel()
    assert await keys(runtime) == ["a"]
    await services.memory_writes.mutate(sweep_handle, sweep)
    item = await provider.store.aget(namespace("alice", collection), "a", refresh_ttl=False)
    assert item.value == {"new": True}


@pytest.mark.asyncio
async def test_lost_namespace_seal_with_existing_data_is_not_recreated(runtime):
    services, provider, _ = runtime
    await apply(runtime)
    if provider.binding.backend == "postgresql":
        async with provider.store._cursor(pipeline=False) as cursor:
            await cursor.execute(
                f"DELETE FROM {NAMESPACE_TABLE} WHERE prefix=%s", (prefix(runtime),)
            )
    else:
        await provider.store.conn.execute(
            f"DELETE FROM {NAMESPACE_TABLE} WHERE prefix=?", (prefix(runtime),)
        )
        await provider.store.conn.commit()
    handle, request = mutation(runtime, key="b")
    with pytest.raises(InvocationStateError) as error:
        await services.memory_writes.mutate(handle, request)
    assert error.value.details["reason"] == "namespace_unmanaged"
    assert await keys(runtime) == ["a"]


@pytest.mark.asyncio
async def test_silently_suppressed_cleanup_cannot_commit_a_false_completion(runtime):
    services, provider, _ = runtime
    for key in "ab":
        await apply(runtime, key=key)
    await expire(runtime)
    name = "suppress_cleanup_" + uuid.uuid4().hex
    postgresql = provider.binding.backend == "postgresql"

    async def execute(statement):
        if postgresql:
            async with provider.store._cursor(pipeline=False) as cursor:
                await cursor.execute(statement)
        else:
            await provider.store.conn.execute(statement)
            await provider.store.conn.commit()

    if postgresql:
        await execute(
            f"CREATE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql "
            "AS 'BEGIN RETURN NULL; END;'"
        )
        await execute(
            f"CREATE TRIGGER {name} BEFORE DELETE ON store FOR EACH ROW EXECUTE FUNCTION {name}()"
        )
    else:
        await execute(
            f"CREATE TRIGGER {name} BEFORE DELETE ON store BEGIN SELECT RAISE(IGNORE); END"
        )
    handle, request = mutation(runtime, kind="sweep")
    try:
        with pytest.raises(InvocationStateError):
            await services.memory_writes.mutate(handle, request)
        assert await keys(runtime) == list("ab")
        assert services.effect_journal.get(request.identity).status == "pending"
    finally:
        await execute(f"DROP TRIGGER {name} ON store" if postgresql else f"DROP TRIGGER {name}")
        if postgresql:
            await execute(f"DROP FUNCTION {name}()")
