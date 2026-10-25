"""The private native memory reader uses original common access decisions."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.errors import ConfigurationError, ToolError, UnsupportedWorkflowFeature
from open_workflow_agent.execution_authority import ExecutionAuthority, authority_scope
from open_workflow_agent.security import static_principal
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow, generate_default_workflow

pytest.importorskip("langgraph.store.memory")
from langgraph.store.base import Item
from langgraph.store.memory import InMemoryStore
from open_workflow_agent_langgraph import LangGraphWorkflowEngine
from open_workflow_agent_langgraph.native_memory import (
    NativeMemoryReadBounds,
    NativeMemoryReader,
    _public_value,
)

AGENT = "agent:1.0.0@default"


def configuration(*, denied=None):
    actions = ["catalog.call", "memory.search"]
    if denied in actions:
        actions.remove(denied)
    return RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "security": {
                "profiles": {
                    "actor-" + actor: {
                        "type": "bearer",
                        "principal": actor,
                        "roles": ["reader"],
                        "scopes": ["memory"],
                        "audience": ["runtime"],
                        "token": {"from_env": "OWA_NATIVE_MEMORY_UNUSED_" + actor.upper()},
                    }
                    for actor in ("alice", "bob")
                }
            },
            "execution_authorization": {
                "enabled": True,
                "id": "native-memory-tests",
                "version": "1",
                "policy": {
                    "rules": [
                        {
                            "actions": actions,
                            "resources": [AGENT, "memory:self"],
                            "roles": ["reader"],
                            "scopes": ["memory"],
                            "audience": ["runtime"],
                        }
                    ]
                },
            },
        }
    )


def authority(services, actor):
    profile = "actor-" + actor
    return ExecutionAuthority.capture(
        static_principal(services.config.security.profile(profile)), security_profile=profile
    )


def namespace(actor: str, collection: str = "preferences") -> tuple[str, ...]:
    return ("owa_native_memory_v1", hashlib.sha256(actor.encode()).hexdigest(), collection)


@pytest.fixture(params=["memory", "sqlite"])
async def native_store(request, tmp_path):
    if request.param == "memory":
        yield InMemoryStore()
    else:
        module = pytest.importorskip("langgraph.store.sqlite.aio")
        async with module.AsyncSqliteStore.from_conn_string(
            str(tmp_path / "native-memory.sqlite3"),
            ttl={"default_ttl": 10, "refresh_on_read": True},
        ) as store:
            await store.setup()
            yield store


async def runtime(tmp_path, store, *, denied=None, enabled=True, bounds=None):
    config = configuration(denied=denied)
    config.memory.enabled = enabled
    services = RuntimeServices(config, model=FakeModel(), database_root=tmp_path)
    engine = LangGraphWorkflowEngine()
    await engine.initialize(services)
    # These tests own a native store, not a durable checkpoint profile.
    engine.native.database_path = None
    reader = NativeMemoryReader(services, store, collection="preferences", bounds=bounds)
    plan = compile_workflow(generate_default_workflow())
    return services, engine, reader, plan


async def invoke(services, engine, plan, actor="alice"):
    handle = services.invocations.create(
        engine="langgraph",
        session_id="same-untrusted-session",
        user_id="same-correlation",
        owner_principal=actor,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    with authority_scope(authority(services, actor)):
        return await engine.invoke(
            plan, handle, {"namespace": "another-principal", "user_id": "same-correlation"}
        )


@pytest.mark.asyncio
async def test_actual_store_concurrent_principals_keep_values_private_and_return_copies(
    tmp_path, native_store
):
    for actor in ("alice", "bob"):
        await native_store.aput(namespace(actor), "record", {"owner": actor, "nested": ["雪"]})
    before = None
    if hasattr(native_store, "conn"):
        async with native_store.conn.execute("SELECT * FROM store ORDER BY 1,2") as cursor:
            before = await cursor.fetchall()
    services, engine, reader, plan = await runtime(tmp_path, native_store)

    async def read(_payload, context):
        actor = context.metadata["authority"]["principal"]["identity"]
        # Even a different ambient authority cannot replace persisted authority.
        other = "bob" if actor == "alice" else "alice"
        with authority_scope(authority(services, other)):
            return await reader.get("record")

    services.catalog.register(AGENT, read)
    try:
        results = await asyncio.gather(
            *(invoke(services, engine, plan, actor) for actor in ("alice", "bob"))
        )
        assert all(result.status == "completed" for result in results), [r.error for r in results]
        assert [result.output for result in results] == [
            {"owner": actor, "nested": ["雪"]} for actor in ("alice", "bob")
        ]
        results[0].output["nested"].append("caller mutation")
        assert (await native_store.aget(namespace("alice"), "record", refresh_ttl=False)).value == {
            "owner": "alice",
            "nested": ["雪"],
        }
        if before is not None:
            async with native_store.conn.execute("SELECT * FROM store ORDER BY 1,2") as cursor:
                assert await cursor.fetchall() == before
        assert services.model.calls == []
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("denied,enabled", [("memory.search", True), (None, False)])
async def test_denied_or_disabled_access_never_calls_the_store(
    tmp_path, native_store, monkeypatch, denied, enabled
):
    calls = []
    original = type(native_store).aget

    async def get(self, *args, **kwargs):
        calls.append((args, kwargs))
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(type(native_store), "aget", get)
    services, engine, reader, plan = await runtime(
        tmp_path, native_store, denied=denied, enabled=enabled
    )

    async def read(_payload, _context):
        return await reader.get("record")

    services.catalog.register(AGENT, read)
    try:
        result = await invoke(services, engine, plan)
        assert result.status == "faulted"
        assert result.error["code"] == (
            "execution_authorization_denied" if enabled else "configuration_error"
        )
        assert calls == []
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["backend", "timeout"])
async def test_backend_error_is_sanitized_and_timeout_cancels_the_awaiting_read(
    tmp_path, native_store, monkeypatch, failure
):
    finished = asyncio.Event()

    async def get(self, *args, **kwargs):
        try:
            if failure == "backend":
                raise RuntimeError("private-store-password and internal-table")
            await asyncio.Event().wait()
        finally:
            finished.set()

    monkeypatch.setattr(type(native_store), "aget", get)
    services, engine, reader, plan = await runtime(
        tmp_path, native_store, bounds=NativeMemoryReadBounds(timeout_seconds=0.05)
    )

    async def read(_payload, _context):
        return await reader.get("record")

    services.catalog.register(AGENT, read)
    try:
        result = await invoke(services, engine, plan)
        assert result.status == "faulted" and result.error["code"] == "tool_error"
        assert result.error["details"]["reason"] == (
            "store_failure" if failure == "backend" else "timeout"
        )
        assert "private-store-password" not in str(result.error)
        assert "internal-table" not in str(result.error)
        assert finished.is_set()
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
async def test_no_original_authority_and_unimplemented_required_feature_stay_closed(services):
    store = InMemoryStore()
    reader = NativeMemoryReader(services, store, collection="preferences")
    with pytest.raises(ConfigurationError, match="original invocation|disabled"):
        await reader.get("record")
    source = generate_default_workflow()
    source["document"]["metadata"] = {
        "owa": {"nativeExtensions": [{"name": "langgraph.memory", "version": "1.0.0"}]}
    }
    engine = LangGraphWorkflowEngine()
    await engine.initialize(services)
    engine.native.database_path = None
    try:
        assert "langgraph.memory@1.0.0" not in engine.capabilities().native_extensions
        before = list(services.invocations.connection.raw.iterdump())
        with pytest.raises(UnsupportedWorkflowFeature):
            await engine.compile(compile_workflow(source))
        assert list(services.invocations.connection.raw.iterdump()) == before
        assert services.model.calls == []
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_legacy_correlation_labels_cannot_select_a_principal_namespace(tmp_path, monkeypatch):
    store = InMemoryStore()

    async def get(self, *args, **kwargs):
        raise AssertionError("legacy authority reached native memory")

    monkeypatch.setattr(type(store), "aget", get)
    config = RuntimeConfig.model_validate(
        {"memory": {"enabled": True}, "model": {"provider": "fake"}}
    )
    services = RuntimeServices(config, model=FakeModel(), database_root=tmp_path)
    engine = LangGraphWorkflowEngine()
    await engine.initialize(services)
    engine.native.database_path = None
    reader = NativeMemoryReader(services, store, collection="preferences")
    entered = False

    async def read(_payload, _context):
        nonlocal entered
        entered = True
        return await reader.get("record")

    services.catalog.register(AGENT, read)
    plan = compile_workflow(generate_default_workflow())
    handle = services.invocations.create(
        engine="langgraph",
        session_id="alice",
        user_id="alice",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    try:
        result = await engine.invoke(plan, handle, {"principal": "alice", "namespace": "alice"})
        assert entered and result.status == "faulted"
        assert result.error["code"] == "configuration_error"
        assert "original invocation authority" in result.error["message"]
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
async def test_caller_cancellation_remains_cancellation(tmp_path, native_store, monkeypatch):
    entered, finished = asyncio.Event(), asyncio.Event()

    async def get(self, *args, **kwargs):
        try:
            entered.set()
            await asyncio.Event().wait()
        finally:
            finished.set()

    monkeypatch.setattr(type(native_store), "aget", get)
    services, engine, reader, plan = await runtime(tmp_path, native_store)

    async def read(_payload, _context):
        task = asyncio.create_task(reader.get("record"))
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            return {"cancelled": True}
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    services.catalog.register(AGENT, read)
    try:
        result = await invoke(services, engine, plan)
        assert result.status == "completed" and result.output == {"cancelled": True}, result.error
        assert finished.is_set()
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.parametrize("key", ["", "../record", "a.b", "x" * 129, True, 1])
@pytest.mark.asyncio
async def test_invalid_key_rejects_without_store_access(services, monkeypatch, key):
    store = InMemoryStore()

    async def get(self, *args, **kwargs):
        raise AssertionError("invalid input reached the store")

    monkeypatch.setattr(type(store), "aget", get)
    reader = NativeMemoryReader(services, store, collection="preferences")
    with pytest.raises(ToolError, match="key is invalid"):
        await reader.get(key)


@pytest.mark.parametrize(
    "case", ["namespace", "key", "shape", "bytes", "nan", "object", "integer", "depth"]
)
def test_invalid_native_record_never_exposes_native_item_or_unbounded_data(case):
    now = datetime.now(UTC)
    item = Item(
        value={"x": "雪"},
        key="record",
        namespace=namespace("alice"),
        created_at=now,
        updated_at=now,
    )
    if case == "namespace":
        item.namespace = namespace("bob")
    elif case == "key":
        item.key = "other"
    elif case == "depth":
        nested = {}
        for _ in range(34):
            nested = {"child": nested}
        item.value = nested
    else:
        item.value = {
            "shape": [],
            "bytes": {"x": "雪" * 19},
            "nan": {"x": float("nan")},
            "object": {"x": object()},
            "integer": {"x": 2**53},
        }[case]
    with pytest.raises(ToolError, match="native memory record"):
        _public_value(
            item, namespace("alice"), "record", NativeMemoryReadBounds(max_record_bytes=64)
        )


def test_missing_and_finite_values_are_public_json_copies():
    bounds = NativeMemoryReadBounds(max_record_bytes=64)
    assert _public_value(None, namespace("alice"), "record", bounds) is None
    now = datetime.now(UTC)
    item = Item(
        value={"x": ["雪"]},
        key="record",
        namespace=namespace("alice"),
        created_at=now,
        updated_at=now,
    )
    result = _public_value(item, namespace("alice"), "record", bounds)
    result["x"].append("changed")
    assert item.value == {"x": ["雪"]}


@pytest.mark.parametrize(
    "values",
    [
        {"max_record_bytes": True},
        {"max_record_bytes": 0},
        {"max_record_bytes": 65_537},
        {"timeout_seconds": True},
        {"timeout_seconds": 0},
        {"timeout_seconds": float("nan")},
        {"timeout_seconds": float("inf")},
        {"timeout_seconds": 31},
        pytest.param({"timeout_seconds": 10**2000}, id="huge-integer"),
    ],
)
def test_invalid_bounds_reject_before_any_provider_is_used(values):
    with pytest.raises(ValueError, match="native memory"):
        NativeMemoryReadBounds(**values)
