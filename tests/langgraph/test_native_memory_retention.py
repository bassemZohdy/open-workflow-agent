"""Actual store expiry uses database time without expiring workflow evidence."""

from __future__ import annotations

import hashlib
import importlib
import os
import uuid

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.effects import EffectIdentity
from open_workflow_agent.errors import InvocationStateError, ToolError, WorkflowExecutionError
from open_workflow_agent.memory_mutations import MemoryMutation
from open_workflow_agent.memory_retention import MemoryRecordRetention
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow, generate_default_workflow

pytest.importorskip("langgraph.store.base")
from open_workflow_agent_langgraph import LangGraphWorkflowEngine
from open_workflow_agent_langgraph import native_memory_mutations as mutations
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
    policy = MemoryRecordRetention("records", "1", 3600)
    try:
        async with open_native_memory_provider(
            services, provider_id="preferences", version="1.0.0", retention=policy
        ) as provider:
            yield services, engine, provider, "expiry_" + uuid.uuid4().hex
    finally:
        await engine.shutdown()
        services.close()


def prefix(provider, collection):
    from test_native_memory import namespace

    module = importlib.import_module(
        "langgraph.store.postgres.base"
        if provider.binding.backend == "postgresql"
        else "langgraph.store.sqlite.base"
    )
    return module._namespace_to_text(namespace("alice", collection))


async def expiry(provider, collection):
    store = provider.store
    if provider.binding.backend == "postgresql":
        async with store._cursor(pipeline=False) as cursor:
            await cursor.execute(
                "SELECT expires_at,ttl_minutes,EXTRACT(EPOCH FROM "
                "(expires_at-clock_timestamp())) AS remaining FROM store "
                "WHERE prefix=%s AND key=%s",
                (prefix(provider, collection), "record"),
            )
            row = await cursor.fetchone()
            return (
                None if row is None else (row["expires_at"], row["ttl_minutes"], row["remaining"])
            )
    async with store.conn.execute(
        "SELECT expires_at,ttl_minutes,"
        "(julianday(expires_at)-julianday('now'))*86400.0 FROM store WHERE prefix=? AND key=?",
        (prefix(provider, collection), "record"),
    ) as cursor:
        return await cursor.fetchone()


async def expire(provider, collection):
    store = provider.store
    if provider.binding.backend == "postgresql":
        async with store._cursor(pipeline=False) as cursor:
            await cursor.execute(
                "UPDATE store SET expires_at='2000-01-01T00:00:00Z' WHERE prefix=%s AND key=%s",
                (prefix(provider, collection), "record"),
            )
    else:
        await store.conn.execute(
            "UPDATE store SET expires_at='2000-01-01T00:00:00Z' WHERE prefix=? AND key=?",
            (prefix(provider, collection), "record"),
        )
        await store.conn.commit()


@pytest.mark.asyncio
async def test_expiry_hides_fresh_reads_but_replays_preserve_snapshots_and_do_not_renew(runtime):
    from test_native_memory import AGENT, invoke

    services, engine, provider, collection = runtime
    writer, reader = provider.writer(collection), provider.reader(collection)
    payload = {"value": "雪", "ttl_seconds": 0, "expires_at": "caller-controlled"}

    async def write_and_read(_payload, _context):
        await writer.put("record", payload)
        deadline = await expiry(provider, collection)
        assert deadline[0] is not None and deadline[1] is None
        assert 3500 < float(deadline[2]) <= 3601
        first = await reader.get("record")
        await expire(provider, collection)
        original_expiry = (await expiry(provider, collection))[0]
        await writer.put("record", payload)
        assert (await expiry(provider, collection))[0] == original_expiry
        return {"first": first, "replay": await reader.get("record")}

    services.catalog.register(AGENT, write_and_read)
    plan = compile_workflow(generate_default_workflow())
    first = await invoke(services, engine, plan)
    assert first.status == "completed", first.error
    assert first.output == {"first": payload, "replay": payload}

    async def read(_payload, _context):
        return await reader.get("record")

    services.catalog.register(AGENT, read)
    fresh = await invoke(services, engine, plan)
    assert fresh.status == "completed" and fresh.output is None, fresh.error
    services.effect_journal.verify_invocation(first.invocation_id, plan.fingerprint)
    # Expiry does not delete either the physical record or invocation evidence.
    assert await expiry(provider, collection) is not None
    assert services.model.calls == []


@pytest.mark.asyncio
async def test_expired_oversized_record_is_filtered_before_public_projection(runtime):
    from test_native_memory import AGENT, invoke, namespace

    services, engine, provider, collection = runtime
    await provider.store.aput(namespace("alice", collection), "record", {"huge": "x" * 100_000})
    await expire(provider, collection)

    async def read(_payload, _context):
        return await provider.reader(collection).get("record")

    services.catalog.register(AGENT, read)
    result = await invoke(services, engine, compile_workflow(generate_default_workflow()))
    assert result.status == "completed" and result.output is None, result.error


@pytest.mark.asyncio
async def test_failure_after_expiry_metadata_rolls_back_value_marker_and_deadline(
    runtime, monkeypatch
):
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
    request = MemoryMutation.put(
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
        {"value": True},
        retention=provider.retention,
    )
    original = mutations._set_record_expiry
    called = []

    async def fail(*args, **kwargs):
        await original(*args, **kwargs)
        called.append(True)
        raise RuntimeError("secret expiry failure")

    monkeypatch.setattr(mutations, "_set_record_expiry", fail)
    with pytest.raises(ToolError) as error:
        await services.memory_writes.mutate(handle, request)
    assert "secret" not in str(error.value)
    assert called == [True] and await expiry(provider, collection) is None
    assert provider.store.ttl_config is None
    assert services.effect_journal.get(request.identity).status == "pending"
    with pytest.raises(WorkflowExecutionError):
        services.effect_journal.verify_invocation(handle.invocation_id, handle.workflow_fingerprint)


@pytest.mark.asyncio
async def test_changed_live_policy_rejects_before_storage_setup(runtime, monkeypatch):
    services, _, provider, _ = runtime
    original_path = services.memory_provider_database_path

    def path(name, *, create_namespace=True):
        assert not create_namespace, "changed policy tried to create storage"
        return original_path(name, create_namespace=False)

    monkeypatch.setattr(services, "memory_provider_database_path", path)
    with pytest.raises(InvocationStateError):
        async with open_native_memory_provider(
            services,
            provider_id="preferences",
            version="1.0.0",
            retention=MemoryRecordRetention("records", "2", provider.retention.ttl_seconds),
        ):
            pytest.fail("changed live policy was accepted")


@pytest.mark.asyncio
async def test_changed_policy_on_reopen_rejects_cached_write_recovery(runtime):
    from test_native_memory import AGENT, invoke

    services, engine, provider, collection = runtime

    async def write(_payload, _context):
        await provider.writer(collection).put("record", {"value": True})
        return {"ok": True}

    services.catalog.register(AGENT, write)
    plan = compile_workflow(generate_default_workflow())
    result = await invoke(services, engine, plan)
    assert result.status == "completed", result.error
    fresh = RuntimeServices(
        services.config.model_copy(deep=True),
        model=FakeModel(),
        database_root=services.database_root,
    )
    try:
        async with open_native_memory_provider(
            fresh,
            provider_id="preferences",
            version="1.0.0",
            retention=MemoryRecordRetention("records", "2", provider.retention.ttl_seconds),
        ):
            with pytest.raises(WorkflowExecutionError):
                fresh.effect_journal.verify_invocation(result.invocation_id, plan.fingerprint)
    finally:
        fresh.close()


@pytest.mark.asyncio
async def test_silently_suppressed_expiry_cannot_commit_an_unexpired_record(runtime):
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
    request = MemoryMutation.put(
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
        {"value": True},
        retention=provider.retention,
    )
    name = "suppress_expiry_" + uuid.uuid4().hex
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
            f"CREATE TRIGGER {name} BEFORE UPDATE OF expires_at ON store "
            f"FOR EACH ROW EXECUTE FUNCTION {name}()"
        )
    else:
        await execute(
            f"CREATE TRIGGER {name} BEFORE UPDATE OF expires_at ON store "
            "BEGIN SELECT RAISE(IGNORE); END"
        )
    try:
        with pytest.raises(InvocationStateError):
            await services.memory_writes.mutate(handle, request)
        assert await expiry(provider, collection) is None
        assert services.effect_journal.get(request.identity).status == "pending"
        with pytest.raises(WorkflowExecutionError):
            services.effect_journal.verify_invocation(
                handle.invocation_id, handle.workflow_fingerprint
            )
    finally:
        await execute(f"DROP TRIGGER {name} ON store" if postgresql else f"DROP TRIGGER {name}")
        if postgresql:
            await execute(f"DROP FUNCTION {name}()")
