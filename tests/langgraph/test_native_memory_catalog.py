"""Configuration and standard OWS calls use actual native memory stores."""

from __future__ import annotations

import json
import logging
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
from open_workflow_agent.config import NativeMemoryBindingConfig
from open_workflow_agent.effects import EffectIdentity
from open_workflow_agent.errors import OwaError
from open_workflow_agent.execution_authority import authority_scope
from open_workflow_agent.execution_authorization import decision_identity
from open_workflow_agent.memory_resolution import resolution_identity
from open_workflow_agent.memory_writes import TABLE as COMMON_MUTATIONS
from open_workflow_agent.security import AuthorizationRule
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow

pytest.importorskip("langgraph.store.base")
from open_workflow_agent_langgraph import LangGraphWorkflowEngine
from test_native_memory import authority, configuration


@pytest.fixture(params=["sqlite", "postgresql"])
async def public_memory(request, tmp_path, monkeypatch):
    config = configuration()
    for actor in ("alice", "bob"):
        monkeypatch.setenv(
            "OWA_NATIVE_MEMORY_UNUSED_" + actor.upper(), "controlled-memory-" + actor
        )
    config.server.api_security_profile = "actor-alice"
    config.memory.native = [
        NativeMemoryBindingConfig(id="notes", collection="public_" + uuid4().hex)
    ]
    config.execution_authorization.policy.rules[0].actions.extend(
        ["memory.add", "memory.delete", "memory.inspect", "memory.resolve", "invocations.resume"]
    )
    config.execution_authorization.policy.rules[0].resources.extend(
        f"memory.{operation}:1.0.0@notes"
        for operation in ("get", "put", "search", "delete", "sweep")
    )
    config.execution_authorization.policy.rules.append(
        AuthorizationRule(
            actions=["memory.inspect", "memory.resolve", "invocations.resume"],
            resources=["*"],
            roles=["reader"],
            scopes=["memory"],
            audience=["runtime"],
        )
    )
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
        yield services, engine
    finally:
        await engine.shutdown()
        services.close()


def workflow(operation, arguments, *, declaration=True):
    document = {"dsl": "1.0.3", "namespace": "memory", "name": "public", "version": "1.0.0"}
    if declaration:
        document["metadata"] = {
            "owa": {
                "nativeExtensions": [
                    {"name": "langgraph.memory", "version": "1.0.0", "required": True}
                ]
            }
        }
    return {
        "document": document,
        "do": [{"action": {"call": f"memory.{operation}:1.0.0@notes", "with": arguments}}],
    }


async def invoke(runtime, operation, arguments, actor="alice", *, declaration=True):
    services, engine = runtime
    plan = compile_workflow(
        workflow(operation, arguments, declaration=declaration),
        enabled_function_calls=services.workflow_function_calls,
    )
    handle = services.invocations.create(
        engine="langgraph",
        session_id="same",
        user_id="same",
        owner_principal=actor,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    with authority_scope(authority(services, actor)):
        return await engine.invoke(plan, handle, {})


@pytest.mark.asyncio
async def test_configured_public_calls_persist_and_keep_principals_separate(public_memory):
    services, engine = public_memory
    assert "langgraph.memory@1.0.0" in engine.capabilities().native_extensions
    assert "memory.search:1.0.0@notes" in engine.capabilities().functions
    result = await invoke(public_memory, "put", {"key": "record", "value": {"color": "blue"}})
    assert result.status == "completed", result.error
    assert result.output == {"ok": True}
    result = await invoke(public_memory, "get", {"key": "record"})
    assert result.status == "completed", result.error
    assert result.output == {"value": {"color": "blue"}}
    result = await invoke(public_memory, "search", {"filter": {"color": "blue"}})
    assert result.status == "completed", result.error
    assert result.output == {"items": [{"key": "record", "value": {"color": "blue"}}]}
    bob = await invoke(public_memory, "search", {}, "bob")
    assert bob.status == "completed", bob.error
    assert bob.output == {"items": []}
    result = await invoke(public_memory, "delete", {"key": "record"})
    assert result.status == "completed", result.error
    result = await invoke(public_memory, "get", {"key": "record"})
    assert result.output == {"value": None}
    result = await invoke(public_memory, "sweep", {})
    assert result.status == "completed", result.error
    assert services.model.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filter,expected",
    [
        ({"v": True}, ["bool"]),
        ({"v": 1}, ["number"]),
        ({"v": None}, ["null"]),
        ({"v": "1"}, ["text"]),
    ],
)
async def test_search_has_strict_scalar_filters_and_deterministic_keys(
    public_memory, filter, expected
):
    for key, value in (("bool", True), ("number", 1), ("null", None), ("text", "1")):
        result = await invoke(public_memory, "put", {"key": key, "value": {"v": value}})
        assert result.status == "completed", result.error
    result = await invoke(public_memory, "put", {"key": "missing", "value": {}})
    assert result.status == "completed", result.error
    result = await invoke(public_memory, "search", {"filter": filter})
    assert result.status == "completed", result.error
    assert [item["key"] for item in result.output["items"]] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments,declaration", [({"key": "x"}, False), ({"key": "x", "namespace": "bob"}, True)]
)
async def test_undeclared_and_scope_selecting_calls_fail_before_admission(
    public_memory, arguments, declaration
):
    services, engine = public_memory
    before = services.invocations.connection.execute("SELECT COUNT(*) FROM invocations").fetchone()[
        0
    ]
    plan = compile_workflow(
        workflow("get", arguments, declaration=declaration),
        enabled_function_calls=services.workflow_function_calls,
    )
    with pytest.raises(OwaError):
        await engine.compile(plan)
    assert (
        services.invocations.connection.execute("SELECT COUNT(*) FROM invocations").fetchone()[0]
        == before
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("committed", [False, True])
async def test_authenticated_resolution_and_actual_workflow_resume_without_redispatch(
    public_memory, committed
):
    services, engine = public_memory
    provider = engine.native_memory.providers["notes"]
    backend = services.memory_writes._backends[provider.binding]
    calls = []

    async def uncertain(request, handle):
        calls.append(True)
        if committed:
            await backend.apply(request, handle)
        raise RuntimeError("secret lost acknowledgement")

    services.memory_writes.register(replace(backend, apply=uncertain))
    result = await invoke(public_memory, "put", {"key": "record", "value": {"version": 1}})
    assert result.status == "waiting", result.error
    app = create_app(config=services.config, services=services, engine=engine)
    headers = {"Authorization": "Bearer controlled-memory-alice"}
    path = f"/v1/invocations/{result.invocation_id}/memory-writes"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test", headers=headers
    ) as client:
        metadata = await client.get(path)
        assert metadata.status_code == 200, metadata.text
        writes = metadata.json()["writes"]
        assert len(writes) == 1 and writes[0]["state"] == "dispatched"
        assert "value" not in writes[0]
        target = path + "/" + writes[0]["operation_id"] + "/resolve"
        decision = "complete" if committed else "abandon_unknown"
        bad = "abandon_unknown" if committed else "complete"
        response = await client.post(
            target,
            headers={"Idempotency-Key": "resolve-1"},
            json={
                "request_digest": writes[0]["request_digest"],
                "decision": bad,
                "acknowledge_unknown": committed,
            },
        )
        assert response.status_code == 409, response.text
        payload = {
            "request_digest": writes[0]["request_digest"],
            "decision": decision,
            "acknowledge_unknown": not committed,
        }
        response = await client.post(target, headers={"Idempotency-Key": "resolve-1"}, json=payload)
        assert response.status_code == 200, response.text
        repeated = await client.post(target, headers={"Idempotency-Key": "resolve-1"}, json=payload)
        assert repeated.status_code == 200 and repeated.json() == response.json()
        resumed = await client.post(
            f"/v1/invocations/{result.invocation_id}/resume", json={"input": None}
        )
        assert resumed.status_code == 200, resumed.text
        final = resumed.json()
        assert final["status"] == ("completed" if committed else "faulted"), final
        if not committed:
            assert final["error"]["code"] == "memory_error"
            assert final["error"]["details"]["outcome"] == "unknown"
        terminal = await client.post(target, headers={"Idempotency-Key": "resolve-1"}, json=payload)
        assert terminal.status_code == 200, terminal.text
        assert terminal.json() == response.json()
    assert calls == [True]
    handle = services.invocations.get(result.invocation_id)
    mutation = services.memory_writes.requests(handle)[0][0]
    permission = decision_identity(
        resolution_identity(mutation.identity), "memory.resolve", "memory:self"
    )
    services.effect_journal.connection.execute(
        "DELETE FROM workflow_effects WHERE identity=?", (permission.key,)
    )
    services.effect_journal.connection.commit()
    with pytest.raises(OwaError):
        services.effect_journal.get(mutation.identity)


@pytest.mark.asyncio
async def test_public_catalog_recovers_process_exit_without_repeating_native_write(
    public_memory, tmp_path
):
    services, engine = public_memory
    source = workflow("put", {"key": "record", "value": {"version": 1}})
    plan = compile_workflow(source, enabled_function_calls=services.workflow_function_calls)
    handle = services.invocations.create(
        engine="langgraph",
        session_id="same",
        user_id="same",
        owner_principal="alice",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    parameters = tmp_path / "worker.json"
    parameters.write_text(
        json.dumps(
            {
                # Sandbox is unused here. Its platform defaults are constructed
                # afresh rather than revalidated as explicit deployment paths.
                "config": services.config.model_dump(mode="json", exclude={"sandbox"}),
                "root": str(services.database_root) if services.database_root else None,
                "invocation": handle.invocation_id,
                "workflow": source,
            }
        )
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("native_memory_catalog_worker.py")),
            str(parameters),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    services.invocations.refresh(handle)
    requests = services.memory_writes.requests(handle)
    assert len(requests) == 1 and requests[0][1] == "dispatched"
    services.execution_ownership.connection.execute(
        "UPDATE invocation_ownership SET expires_at=1 WHERE invocation_id=?",
        (handle.invocation_id,),
    )
    services.execution_ownership.connection.commit()
    provider = engine.native_memory.providers["notes"]
    backend = services.memory_writes._backends[provider.binding]

    async def forbidden_repeat(_request, _handle):
        raise AssertionError("recovery repeated an already committed native write")

    services.memory_writes.register(replace(backend, apply=forbidden_repeat))
    with authority_scope(authority(services, "alice")):
        result = await engine.resume(handle, None, plan)
    assert result.status == "completed", result.error
    assert result.output == {"ok": True}
    assert services.memory_writes.requests(handle)[0][1] == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["get", "search", "put"])
async def test_completed_public_receipt_requires_leaf_even_without_native_checkpoint_link(
    public_memory, operation
):
    services, _engine = public_memory
    payload = (
        {"key": "record", "value": {"v": 1}}
        if operation == "put"
        else {"key": "record"}
        if operation == "get"
        else {}
    )
    result = await invoke(public_memory, operation, payload)
    assert result.status == "completed", result.error
    connection = services.effect_journal.connection
    rows = connection.execute(
        "SELECT payload FROM workflow_effect_invocations WHERE invocation_id=?",
        (result.invocation_id,),
    ).fetchall()
    identities = [EffectIdentity.from_value(json.loads(row[0])) for row in rows]
    parent = next(item for item in identities if item.operation.startswith("call:memory."))
    prefix = "policy:memory-mutation:v1:" if operation == "put" else "policy:memory-read:v1:"
    child = next(item for item in identities if item.operation.startswith(prefix))
    assert services.effect_journal.get(parent).status == "completed"
    # Remove every common leaf row, simulating loss before a checkpoint could
    # retain a receipt link. The completed enclosing call still must fail closed.
    table = COMMON_MUTATIONS if operation == "put" else "memory_read_observations"
    for selected in (table, "workflow_effects", "workflow_effect_invocations"):
        connection.execute(f"DELETE FROM {selected} WHERE identity=?", (child.key,))
    connection.commit()
    with pytest.raises(OwaError):
        services.effect_journal.get(parent)


@pytest.mark.asyncio
async def test_recovery_controls_require_authentication_and_original_owner(public_memory):
    services, engine = public_memory
    result = await invoke(public_memory, "put", {"key": "record", "value": {"private": "value"}})
    assert result.status == "completed", result.error
    path = f"/v1/invocations/{result.invocation_id}/memory-writes"
    alice_app = create_app(config=services.config, services=services, engine=engine)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(alice_app), base_url="http://test"
    ) as client:
        assert (await client.get(path)).status_code == 401
        assert (
            await client.get(path, headers={"Authorization": "Bearer invalid"})
        ).status_code == 403
    bob_config = services.config.model_copy(deep=True)
    bob_config.server.api_security_profile = "actor-bob"
    bob_app = create_app(config=bob_config, services=services, engine=engine)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(bob_app),
        base_url="http://test",
        headers={"Authorization": "Bearer controlled-memory-bob"},
    ) as client:
        denied = await client.get(path)
        assert denied.status_code == 404, denied.text
        assert "private" not in denied.text


@pytest.mark.asyncio
async def test_search_omits_expired_and_child_namespaces_before_limit_and_does_not_log_filter(
    public_memory, caplog
):
    from test_native_memory import namespace
    from test_native_memory_retention import expire

    services, engine = public_memory
    for key in ("record", "z-live"):
        result = await invoke(
            public_memory, "put", {"key": key, "value": {"secret": "filter-canary-731"}}
        )
        assert result.status == "completed", result.error
    provider = engine.native_memory.providers["notes"]
    collection = services.config.memory.native[0].collection
    await expire(provider, collection)
    await provider.store.aput(
        (*namespace("alice", collection), "child"), "a-child", {"secret": "filter-canary-731"}
    )
    caplog.set_level(logging.DEBUG)
    result = await invoke(
        public_memory, "search", {"filter": {"secret": "filter-canary-731"}, "limit": 1}
    )
    assert result.status == "completed", result.error
    assert result.output == {"items": [{"key": "z-live", "value": {"secret": "filter-canary-731"}}]}
    assert "filter-canary-731" not in caplog.text
    # A later mutation changes fresh reads, while terminal replay retains the
    # original observed result without consulting the current query contents.
    later = await invoke(public_memory, "put", {"key": "z-live", "value": {"secret": "new"}})
    assert later.status == "completed", later.error
    handle = services.invocations.get(result.invocation_id)
    plan = compile_workflow(
        workflow("search", {"filter": {"secret": "filter-canary-731"}, "limit": 1}),
        enabled_function_calls=services.workflow_function_calls,
    )
    with authority_scope(authority(services, "alice")):
        replayed = await engine.resume(handle, None, plan)
    assert replayed.status == "completed" and replayed.output == result.output


@pytest.mark.asyncio
async def test_public_binding_can_close_and_reopen_without_stale_handlers(public_memory):
    from open_workflow_agent_langgraph.native_memory_catalog import NativeMemoryCatalog

    services, engine = public_memory
    result = await invoke(public_memory, "put", {"key": "record", "value": {"persisted": True}})
    assert result.status == "completed", result.error
    await engine.native_memory.close()
    engine.native_memory = None
    assert not services.catalog.has("memory.get:1.0.0@notes")
    assert "langgraph.memory@1.0.0" not in engine.capabilities().native_extensions
    installed = NativeMemoryCatalog(services)
    await installed.open()
    engine.native_memory = installed
    result = await invoke(public_memory, "get", {"key": "record"})
    assert result.status == "completed", result.error
    assert result.output == {"value": {"persisted": True}}


@pytest.mark.asyncio
async def test_replacing_public_handler_invalidates_capability_before_dispatch(public_memory):
    services, engine = public_memory
    calls = []

    async def untrusted(_payload, _context):
        calls.append(True)
        return {"value": "untrusted"}

    services.catalog.register("memory.get:1.0.0@notes", untrusted)
    plan = compile_workflow(
        workflow("get", {"key": "record"}), enabled_function_calls=services.workflow_function_calls
    )
    with pytest.raises(OwaError):
        await engine.compile(plan)
    assert not calls


@pytest.mark.asyncio
async def test_published_configuration_only_workflow_example_executes(public_memory):
    import yaml

    services, engine = public_memory
    source = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / "examples/native-memory-workflow.yaml").read_text()
    )
    plan = compile_workflow(source, enabled_function_calls=services.workflow_function_calls)
    handle = services.invocations.create(
        engine="langgraph",
        session_id="example",
        user_id="example",
        owner_principal="alice",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    with authority_scope(authority(services, "alice")):
        result = await engine.invoke(plan, handle, {})
    assert result.status == "completed", result.error
    assert result.output == {
        "items": [{"key": "favorite-color", "value": {"kind": "preference", "color": "blue"}}]
    }
    assert services.model.calls == []
