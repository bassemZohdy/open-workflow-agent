"""Versioned semantic calls use real fenced SQLite/pgvector SDK indexes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from open_workflow_agent.api import create_app
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import (
    NativeMemoryBindingConfig,
    NativeMemorySemanticConfig,
    RuntimeConfig,
)
from open_workflow_agent.errors import OwaError
from open_workflow_agent.execution_authority import authority_scope
from open_workflow_agent.security import AuthorizationRule
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow

pytest.importorskip("langgraph.store.base")
from native_semantic_test_model import LocalTestModel
from open_workflow_agent_langgraph import (
    LangGraphWorkflowEngine,
    native_memory_embeddings,
    native_memory_mutations,
)
from test_native_memory import authority, configuration


def semantic_configuration():
    config = configuration()
    config.memory.native = [
        NativeMemoryBindingConfig(
            id="notes",
            collection="semantic_" + uuid4().hex,
            semantic=NativeMemorySemanticConfig(),
        )
    ]
    config.execution_authorization.policy.rules[0].actions.extend(["memory.add", "memory.delete"])
    config.execution_authorization.policy.rules[0].actions.extend(
        ["memory.inspect", "memory.resolve", "invocations.resume"]
    )
    config.server.api_security_profile = "actor-alice"
    config.execution_authorization.policy.rules.append(
        AuthorizationRule(
            actions=["memory.inspect", "memory.resolve", "invocations.resume"],
            resources=["*"],
            roles=["reader"],
            scopes=["memory"],
            audience=["runtime"],
        )
    )
    config.execution_authorization.policy.rules[0].resources.extend(
        [f"memory.{op}:1.0.0@notes" for op in ("get", "put", "search", "delete", "sweep")]
        + ["memory.search:1.1.0@notes"]
    )
    return config


@pytest.fixture(params=["sqlite", "postgresql"])
async def semantic_memory(request, tmp_path, monkeypatch):
    config = semantic_configuration()
    for actor in ("alice", "bob"):
        monkeypatch.setenv(
            "OWA_NATIVE_MEMORY_UNUSED_" + actor.upper(), "controlled-semantic-" + actor
        )
    root = tmp_path
    if request.param == "postgresql":
        datasource = os.getenv("OWA_TEST_VECTOR_POSTGRES_URL")
        if not datasource:
            pytest.skip("OWA_TEST_VECTOR_POSTGRES_URL is not configured")
        config.persistence.datasource = datasource
        root = None
    model = LocalTestModel()
    original_write = native_memory_mutations.run_native_storage_write

    async def traced(write):
        try:
            return await original_write(write)
        except Exception as exc:
            model.failures.append((type(exc).__name__, str(exc)[:160]))
            raise

    monkeypatch.setattr(native_memory_mutations, "run_native_storage_write", traced)
    monkeypatch.setattr(native_memory_embeddings, "local_embedding_provider", lambda: model)
    services = RuntimeServices(config, model=FakeModel(), database_root=root)
    engine = LangGraphWorkflowEngine()
    try:
        await engine.initialize(services)
    except Exception as exc:
        reasons = []
        current = exc
        while current is not None and len(reasons) < 5:
            reasons.append((type(current).__name__, str(current)[:240]))
            current = current.__context__
        services.close()
        raise AssertionError(reasons) from None
    await engine._timer_dispatcher.stop()
    try:
        yield services, engine, model
    finally:
        await engine.shutdown()
        services.close()


def definition(operation, arguments, *, semantic=True):
    extensions = [{"name": "langgraph.memory", "version": "1.0.0", "required": True}]
    if semantic:
        extensions.append(
            {"name": "langgraph.memory.semantic", "version": "1.0.0", "required": True}
        )
    return {
        "document": {
            "dsl": "1.0.3",
            "namespace": "memory",
            "name": "semantic",
            "version": "1.0.0",
            "metadata": {"owa": {"nativeExtensions": extensions}},
        },
        "do": [
            {
                "action": {
                    "call": f"memory.{operation}:"
                    + ("1.1.0" if operation == "search" else "1.0.0")
                    + "@notes",
                    "with": arguments,
                }
            }
        ],
    }


async def invoke(runtime, operation, arguments, actor="alice"):
    services, engine, _ = runtime
    plan = compile_workflow(
        definition(operation, arguments), enabled_function_calls=services.workflow_function_calls
    )
    handle = services.invocations.create(
        engine="langgraph",
        session_id=uuid4().hex,
        user_id="same",
        owner_principal=actor,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    with authority_scope(authority(services, actor)):
        return await engine.invoke(plan, handle, {})


async def vector_count(provider, collection, key):
    from test_native_memory_retention import prefix

    postgres = provider.binding.backend == "postgresql"
    async with provider.store._cursor(
        **({"pipeline": False} if postgres else {"transaction": False})
    ) as cursor:
        placeholder = "%s" if postgres else "?"
        await cursor.execute(
            "SELECT COUNT(*) AS count FROM store_vectors "
            f"WHERE prefix={placeholder} AND key={placeholder}",
            (prefix(provider, collection), key),
        )
        row = await cursor.fetchone()
        return row["count"] if postgres else row[0]


@pytest.mark.asyncio
async def test_native_semantic_search_ranks_actual_indexes_and_isolates_principals(semantic_memory):
    services, engine, model = semantic_memory
    assert "langgraph.memory.semantic@1.0.0" in engine.capabilities().native_extensions
    for key, text in (("a-tea", "tea"), ("c-coffee", "coffee"), ("b-espresso", "espresso")):
        result = await invoke(
            semantic_memory, "put", {"key": key, "value": {"text": text, "kind": "drink"}}
        )
        assert result.status == "completed", (result.error, model.failures)
    model.calls.clear()
    result = await invoke(
        semantic_memory, "search", {"query": "coffee", "limit": 2, "filter": {"kind": "drink"}}
    )
    assert result.status == "completed", result.error
    assert [item["key"] for item in result.output["items"]] == ["b-espresso", "c-coffee"]
    assert all(abs(item["score"] - 1) < 1e-5 for item in result.output["items"])
    assert model.calls == ["coffee"]
    bob = await invoke(semantic_memory, "search", {"query": "coffee"}, "bob")
    assert bob.status == "completed" and bob.output == {"items": []}, bob.error
    result = await invoke(semantic_memory, "delete", {"key": "b-espresso"})
    assert result.status == "completed", result.error
    assert (
        await vector_count(
            engine.native_memory.providers["notes"],
            services.config.memory.native[0].collection,
            "b-espresso",
        )
        == 0
    )
    result = await invoke(semantic_memory, "search", {"query": "coffee", "limit": 1})
    assert result.status == "completed", result.error
    assert result.output["items"][0]["key"] == "c-coffee"
    assert services.model.calls == []


@pytest.mark.asyncio
async def test_semantic_declaration_and_argument_bounds_precede_invocation(semantic_memory):
    services, engine, model = semantic_memory
    before = services.invocations.connection.execute("SELECT COUNT(*) FROM invocations").fetchone()[
        0
    ]
    model.calls.clear()
    for source in (
        definition("search", {"query": "coffee"}, semantic=False),
        definition("search", {"query": "coffee", "namespace": "bob"}),
    ):
        plan = compile_workflow(source, enabled_function_calls=services.workflow_function_calls)
        with pytest.raises(OwaError):
            await engine.compile(plan)
    assert not model.calls
    assert (
        services.invocations.connection.execute("SELECT COUNT(*) FROM invocations").fetchone()[0]
        == before
    )


@pytest.mark.asyncio
async def test_semantic_filters_expiry_namespace_and_replay_use_original_observation(
    semantic_memory,
):
    from test_native_memory import namespace
    from test_native_memory_retention import expire

    services, engine, model = semantic_memory
    records = {
        "record": {"text": "coffee", "flag": True, "nullable": None},
        "z-valid": {"text": "coffee", "flag": True, "nullable": None},
        "a-number": {"text": "coffee", "flag": 1, "nullable": None},
        "b-missing": {"text": "coffee", "flag": True},
    }
    for key, value in records.items():
        result = await invoke(semantic_memory, "put", {"key": key, "value": value})
        assert result.status == "completed", (result.error, model.failures)
    provider = engine.native_memory.providers["notes"]
    collection = services.config.memory.native[0].collection
    await expire(provider, collection)
    await provider.store.aput(
        (*namespace("alice", collection), "child"), "aa-child", records["z-valid"]
    )
    arguments = {"query": "coffee", "limit": 1, "filter": {"flag": True, "nullable": None}}
    original = await invoke(semantic_memory, "search", arguments)
    assert original.status == "completed", original.error
    assert [item["key"] for item in original.output["items"]] == ["z-valid"]
    model.calls.clear()
    swept = await invoke(semantic_memory, "sweep", {})
    assert swept.status == "completed", swept.error
    assert await vector_count(provider, collection, "record") == 0
    assert not model.calls
    newer = await invoke(
        semantic_memory,
        "put",
        {"key": "z-valid", "value": {"text": "tea", "flag": True, "nullable": None}},
    )
    assert newer.status == "completed", newer.error
    plan = compile_workflow(
        definition("search", arguments), enabled_function_calls=services.workflow_function_calls
    )
    handle = services.invocations.get(original.invocation_id)
    model.calls.clear()
    with authority_scope(authority(services, "alice")):
        replayed = await engine.resume(handle, None, plan)
    assert replayed.status == "completed" and replayed.output == original.output
    assert not model.calls


@pytest.mark.asyncio
async def test_semantic_reopen_retains_data_and_rejects_changed_model_identity(semantic_memory):
    from open_workflow_agent_langgraph.native_memory_catalog import NativeMemoryCatalog
    from open_workflow_agent_langgraph.native_memory_semantic_storage import verify_semantic_storage

    services, engine, model = semantic_memory
    result = await invoke(semantic_memory, "put", {"key": "record", "value": {"text": "coffee"}})
    assert result.status == "completed", result.error
    await engine.native_memory.close()
    engine.native_memory = None
    installed = NativeMemoryCatalog(services)
    await installed.open()
    engine.native_memory = installed
    found = await invoke(semantic_memory, "search", {"query": "coffee", "limit": 1})
    assert found.status == "completed", found.error
    assert found.output["items"][0]["key"] == "record"
    provider = installed.providers["notes"]
    await installed.close()
    engine.native_memory = None
    model.artifact_sha256 = "b" * 64
    with pytest.raises(OwaError) as changed:
        await NativeMemoryCatalog(services).open()
    assert changed.value.details.get("reason") == "provider_changed"
    verify_semantic_storage(
        services.memory_provider_database_path("langgraph", create_namespace=False),
        provider.binding,
        services.config.memory.native[0].collection,
        provider.semantic,
    )


@pytest.mark.asyncio
async def test_semantic_process_exit_recovers_without_overwriting_newer_value_or_vector(
    semantic_memory, tmp_path
):
    services, engine, _model = semantic_memory
    source = definition("put", {"key": "record", "value": {"text": "coffee"}})
    plan = compile_workflow(source, enabled_function_calls=services.workflow_function_calls)
    handle = services.invocations.create(
        engine="langgraph",
        session_id=uuid4().hex,
        user_id="same",
        owner_principal="alice",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    parameters = tmp_path / "semantic-worker.json"
    parameters.write_text(
        json.dumps(
            {
                "config": services.config.model_dump(mode="json", exclude={"sandbox"}),
                "root": str(services.database_root) if services.database_root else None,
                "invocation": handle.invocation_id,
                "workflow": source,
            }
        )
    )
    child = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("native_memory_semantic_worker.py")),
            str(parameters),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert child.returncode == 0, child.stderr
    services.invocations.refresh(handle)
    assert services.memory_writes.requests(handle)[0][1] == "dispatched"
    services.execution_ownership.connection.execute(
        "UPDATE invocation_ownership SET expires_at=1 WHERE invocation_id=?",
        (handle.invocation_id,),
    )
    services.execution_ownership.connection.commit()
    newer = await invoke(semantic_memory, "put", {"key": "record", "value": {"text": "tea"}})
    assert newer.status == "completed", newer.error
    provider = engine.native_memory.providers["notes"]
    backend = services.memory_writes._backends[provider.binding]

    async def forbidden(_request, _handle):
        raise AssertionError("recovery repeated a committed semantic write")

    services.memory_writes.register(replace(backend, apply=forbidden))
    with authority_scope(authority(services, "alice")):
        recovered = await engine.resume(handle, None, plan)
    assert recovered.status == "completed", recovered.error
    assert services.memory_writes.requests(handle)[0][1] == "completed"
    result = await invoke(semantic_memory, "search", {"query": "coffee", "limit": 1})
    assert result.status == "completed", result.error
    assert result.output["items"][0]["value"] == {"text": "tea"}
    assert abs(result.output["items"][0]["score"]) < 1e-5


@pytest.mark.asyncio
@pytest.mark.parametrize("committed", [False, True])
async def test_semantic_write_resolution_uses_http_authority_and_never_repeats_native_apply(
    semantic_memory, committed
):
    services, engine, _model = semantic_memory
    provider = engine.native_memory.providers["notes"]
    backend = services.memory_writes._backends[provider.binding]
    calls = []

    async def uncertain(request, handle):
        calls.append(True)
        if committed:
            await backend.apply(request, handle)
        raise RuntimeError("controlled missing acknowledgement")

    services.memory_writes.register(replace(backend, apply=uncertain))
    result = await invoke(semantic_memory, "put", {"key": "record", "value": {"text": "coffee"}})
    assert result.status == "waiting", result.error
    app = create_app(config=services.config, services=services, engine=engine)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app),
        base_url="http://test",
        headers={"Authorization": "Bearer controlled-semantic-alice"},
    ) as client:
        path = f"/v1/invocations/{result.invocation_id}/memory-writes"
        metadata = await client.get(path)
        assert metadata.status_code == 200, metadata.text
        write = metadata.json()["writes"][0]
        assert "value" not in write and "index" not in write
        resolved = await client.post(
            path + "/" + write["operation_id"] + "/resolve",
            headers={"Idempotency-Key": "semantic-resolution"},
            json={
                "request_digest": write["request_digest"],
                "decision": "complete" if committed else "abandon_unknown",
                "acknowledge_unknown": not committed,
            },
        )
        assert resolved.status_code == 200, resolved.text
        resumed = await client.post(
            f"/v1/invocations/{result.invocation_id}/resume", json={"input": None}
        )
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["status"] == ("completed" if committed else "faulted")
        if not committed:
            assert resumed.json()["error"]["details"]["outcome"] == "unknown"
    assert calls == [True]


@pytest.mark.asyncio
async def test_missing_or_oversized_semantic_metadata_blocks_capability_and_cached_reuse(
    semantic_memory,
):
    services, engine, model = semantic_memory
    result = await invoke(semantic_memory, "put", {"key": "record", "value": {"text": "coffee"}})
    assert result.status == "completed", result.error
    provider = engine.native_memory.providers["notes"]
    postgresql = provider.binding.backend == "postgresql"
    async with provider.store._cursor(
        **({"pipeline": False} if postgresql else {"transaction": False})
    ) as cursor:
        await cursor.execute(
            "UPDATE owa_memory_semantic_profiles_v1 SET payload="
            + ("%s" if postgresql else "?")
            + " WHERE collection="
            + ("%s" if postgresql else "?"),
            ("x" * 5000, services.config.memory.native[0].collection),
        )
    model.calls.clear()
    with pytest.raises(OwaError):
        engine.capabilities()
    assert not model.calls


@pytest.mark.asyncio
async def test_activation_does_not_reinterpret_existing_unindexed_records(semantic_memory):
    from open_workflow_agent_langgraph.native_memory_catalog import NativeMemoryCatalog
    from test_native_memory import namespace

    services, engine, _model = semantic_memory
    installed = engine.native_memory
    collection = services.config.memory.native[0].collection
    provider = installed.providers["notes"]
    await provider.store.aput(
        namespace("alice", collection), "legacy", {"text": "coffee"}, index=False
    )
    postgresql = provider.binding.backend == "postgresql"
    async with provider.store._cursor(
        **({"pipeline": False} if postgresql else {"transaction": False})
    ) as cursor:
        await cursor.execute(
            "DELETE FROM owa_memory_semantic_profiles_v1 WHERE collection="
            + ("%s" if postgresql else "?"),
            (collection,),
        )
    await installed.close()
    engine.native_memory = None
    with pytest.raises(OwaError) as failure:
        await NativeMemoryCatalog(services).open()
    chain = failure.value.__context__
    assert chain is not None and "new empty collection" in str(chain)


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv("OWA_TEST_PACKAGED_LOCAL_MODEL") != "1",
    reason="explicit packaged offline model acceptance",
)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_real_packaged_model_executes_configuration_only_api_example(
    tmp_path, monkeypatch, backend
):
    import yaml

    examples = Path(__file__).resolve().parents[2] / "examples"
    config = RuntimeConfig.model_validate(
        yaml.safe_load((examples / "semantic-memory.yaml").read_text())
    )
    config.workflow.path = str(examples / "semantic-memory-workflow.yaml")
    config.memory.native[0].collection = "packaged_" + uuid4().hex
    config.memory.native[0].semantic.timeout_seconds = 30.0
    root = tmp_path
    if backend == "postgresql":
        datasource = os.getenv("OWA_TEST_VECTOR_POSTGRES_URL")
        if not datasource:
            pytest.skip("explicit pgvector acceptance datasource is not configured")
        config.persistence.datasource = datasource
        root = None
    monkeypatch.setenv("OWA_SEMANTIC_MEMORY_TOKEN", "controlled-packaged-semantic-owner")
    services = RuntimeServices(config, model=FakeModel(), database_root=root)
    engine = LangGraphWorkflowEngine()
    app = create_app(config=config, services=services, engine=engine)
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app),
                base_url="http://test",
                headers={"Authorization": "Bearer controlled-packaged-semantic-owner"},
            ) as client:
                response = await client.post("/v1/invoke", json={"input": {}})
                assert response.status_code == 200, response.text
                result = response.json()
                assert result["status"] == "completed", result
                assert result["output"]["items"][0]["key"] == "espresso"
            policy = engine.native_memory.providers["semantic-notes"].semantic
            assert len(policy.artifact_sha256) == 64
            assert services.model.calls == []
    finally:
        services.close()
