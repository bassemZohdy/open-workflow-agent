"""Actual native original-authority isolation and independent replay evidence."""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from _version_recovery_worker import native_snapshot
from engine_cases import engine_cases
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.effects import EffectIdentity
from open_workflow_agent.errors import OwaError
from open_workflow_agent.execution_authority import (
    ExecutionAuthority,
    authority_scope,
    current_authority,
)
from open_workflow_agent.execution_authorization import authority_identity
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.scheduling import WorkflowScheduler
from open_workflow_agent.security import static_principal
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow

pytestmark = pytest.mark.usefixtures("explicit_workflow_delivery")
ENGINES = [case for case in engine_cases() if case[0] in {"adk", "langgraph"}]
CATALOG = "agent:1.0.0@default"
ENDPOINT = "https://authority.test/action"
RAW = {"original": None, "nested": [True, False]}


def configuration(backend, *, denied=None):
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("actual PostgreSQL datasource required")
    actions = [
        "catalog.call",
        "model.complete",
        "tool.invoke",
        "memory.add",
        "memory.search",
        "memory.delete",
        "protocol.call",
        "workflow.run",
        "event.emit",
        "event.listen",
    ]
    if denied in actions:
        actions.remove(denied)
    return RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "persistence": {"datasource": datasource},
            "security": {
                "profiles": {
                    "actor-" + actor: {
                        "type": "bearer",
                        "principal": actor,
                        "roles": ["worker", "role-" + actor],
                        "scopes": ["execute"],
                        "audience": ["runtime"],
                        "token": {"from_env": "OWA_AUTHORITY_UNUSED_" + actor},
                    }
                    for actor in ("alice", "bob")
                }
            },
            "execution_authorization": {
                "enabled": True,
                "id": "shared-native",
                "version": "1",
                "policy": {
                    "rules": [
                        {
                            "actions": actions,
                            "resources": ["*"],
                            "roles": ["worker"],
                            "scopes": ["execute"],
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


def runtime(path, backend, *, config=None, dispatch=None):
    services = RuntimeServices(
        config or configuration(backend),
        model=FakeModel(),
        database_root=None if backend == "postgresql" else path,
        recovery_profile_provider=lambda engine: {"id": "authority-fixture", "version": "1"},
    )
    assert (
        services.invocations.connection.kind == services.effect_journal.connection.kind == backend
    )
    if backend == "postgresql":
        assert services.database_root is None
        for engine in ("adk", "langgraph"):
            assert services.engine_database_path(engine, create_namespace=False).startswith(
                "postgres"
            )
    if dispatch is not None:
        services.protocols = ProtocolServices(
            HttpClient(transport=httpx.MockTransport(dispatch)),
            security=services.config.security,
            authorize_destination=services.authorize_protocol_destination,
            authorize_credential=services.authorize_protocol_credential,
        )
        services.tools.protocols = services.protocols
    return services


def definition(tasks, name=None, **extra):
    return {
        "document": {
            "dsl": "1.0.3",
            "namespace": "authority",
            "name": name or uuid4().hex,
            "version": "1.0.0",
        },
        "do": tasks,
        **extra,
    }


def create(services, engine_name, plan, actor="alice", *, session=None):
    return services.invocations.create(
        engine=engine_name,
        session_id=session or uuid4().hex,
        user_id="same-correlation-user",
        owner_principal=actor,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )


def actual_native(engine, name):
    if not (engine.native.available if name == "adk" else engine.native.task_nodes_available):
        pytest.skip("actual independently installed native SDK required")


def identities(services, handle):
    return [
        EffectIdentity.from_value(json.loads(row[0]))
        for row in services.effect_journal.connection.execute(
            "SELECT payload FROM workflow_effect_invocations "
            "WHERE invocation_id=? ORDER BY identity",
            (handle.invocation_id,),
        ).fetchall()
    ]


def snapshot(services, handle, backend):
    tables = {
        "invocations": "invocation_id",
        "invocation_inputs": "invocation_id",
        "invocation_ownership": "invocation_id",
        "workflow_effect_invocations": "invocation_id",
        "execution_authorities": "invocation_id",
        "execution_authorization_decisions": "invocation_id",
        "execution_authorization_outcomes": "invocation_id",
        "execution_authorization_intents": "invocation_id",
        "recovery_invocation_definitions": "invocation_id",
    }
    common = {
        table: [
            tuple(row)
            for row in services.effect_journal.connection.execute(
                "SELECT * FROM " + table + " WHERE " + column + "=? ORDER BY 1",
                (handle.invocation_id,),
            ).fetchall()
        ]
        for table, column in tables.items()
    }
    common["effects"] = [
        tuple(row)
        for row in services.effect_journal.connection.execute(
            "SELECT e.* FROM workflow_effects e JOIN workflow_effect_invocations i "
            "ON e.identity=i.identity "
            "WHERE i.invocation_id=? ORDER BY e.identity",
            (handle.invocation_id,),
        ).fetchall()
    ]
    return common, native_snapshot(services, handle, backend)


async def invoke_as(engine, services, plan, handle, actor="alice"):
    with authority_scope(authority(services, actor)):
        return await engine.invoke(plan, handle, RAW.copy())


async def suspend(engine, services, engine_name):
    plan = compile_workflow(
        definition(
            [
                {"first": {"call": CATALOG, "with": {"input": "${ . }"}}},
                {"pause": {"wait": {"seconds": 3600}}},
                {
                    "last": {
                        "call": "http",
                        "with": {
                            "method": "POST",
                            "endpoint": ENDPOINT,
                            "body": "${ $workflow.input }",
                        },
                    }
                },
            ]
        )
    )
    handle = create(services, engine_name, plan)
    result = await invoke_as(engine, services, plan, handle)
    assert result.status == "waiting", result.error
    assert handle.invocation_id not in engine._active and handle._execution_lease is None
    return plan, handle


def corrupt(services, handle, case):
    db = services.effect_journal.connection
    if case == "authority":
        db.execute(
            "DELETE FROM execution_authorities WHERE invocation_id=?", (handle.invocation_id,)
        )
    elif case == "origin":
        original = authority_identity(handle)
        db.execute("DELETE FROM workflow_effects WHERE identity=?", (original.key,))
        db.execute("DELETE FROM workflow_effect_invocations WHERE identity=?", (original.key,))
    elif case == "decision":
        key = db.execute(
            "SELECT decision_key FROM execution_authorization_decisions WHERE invocation_id=? "
            "ORDER BY decision_key LIMIT 1",
            (handle.invocation_id,),
        ).fetchone()[0]
        db.execute("DELETE FROM execution_authorization_decisions WHERE decision_key=?", (key,))
        db.execute("DELETE FROM workflow_effects WHERE identity=?", (key,))
        db.execute("DELETE FROM workflow_effect_invocations WHERE identity=?", (key,))
    elif case == "seal":
        business = next(
            identity
            for identity in identities(services, handle)
            if identity.operation == "call:" + CATALOG
        )
        key = replace(business, operation="policy:execution-authorization-seal:v1").key
        db.execute(
            "DELETE FROM execution_authorization_outcomes WHERE business_key=?", (business.key,)
        )
        db.execute("DELETE FROM workflow_effects WHERE identity=?", (key,))
        db.execute("DELETE FROM workflow_effect_invocations WHERE identity=?", (key,))
    elif case == "claims":
        services.config.security.profiles["actor-alice"].roles = ["worker", "revoked"]
    elif case == "policy":
        services.config.execution_authorization.version = "2"
    elif case == "pending":
        business = next(
            identity
            for identity in identities(services, handle)
            if identity.operation == "call:http"
        )
        assert services.effect_journal.get(business).status == "pending"
        db.execute("DELETE FROM workflow_effects WHERE identity=?", (business.key,))
        db.execute("DELETE FROM workflow_effect_invocations WHERE identity=?", (business.key,))
    else:
        raise AssertionError(case)
    db.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_same_correlation_concurrent_principals_have_usable_native_and_memory_isolation(
    tmp_path, engine_name, engine_type, backend
):
    services = runtime(tmp_path, backend)
    engine = engine_type()
    actual_native(engine, engine_name)
    await engine.initialize(services)
    release, entered = asyncio.Event(), {actor: asyncio.Event() for actor in ("alice", "bob")}
    label = uuid4().hex

    async def function(payload, context):
        claims = context.metadata["authority"]["principal"]
        actor = claims["identity"]
        assert current_authority().principal.identity == actor
        assert not any(key.startswith("_") for key in context.metadata)
        assert not hasattr(context.services, "effect_journal")
        added = await context.services.invoke_agent_tool(
            "add_memory", {"text": label + " " + actor}
        )
        entered[actor].set()
        await release.wait()
        rows = await context.services.invoke_agent_tool("search_memory", {"query": label})
        assert [row["id"] for row in rows] == [added["id"]]
        response = await context.model.complete({"actor": actor})
        return {
            "actor": actor,
            "roles": list(claims["roles"]),
            "memory": rows[0]["text"],
            "response": response,
            "session": context.metadata["session_id"],
        }

    services.catalog.register(CATALOG, function)
    plan = compile_workflow(definition([{"action": {"call": CATALOG}}]))
    session = uuid4().hex
    handles = [
        create(services, engine_name, plan, actor, session=session) for actor in ("alice", "bob")
    ]
    tasks = [
        asyncio.create_task(invoke_as(engine, services, plan, handle, actor))
        for handle, actor in zip(handles, ("alice", "bob"), strict=True)
    ]
    try:
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered.values())), 40)
        assert current_authority() is None
        release.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks), 40)
        for result, handle, actor in zip(results, handles, ("alice", "bob"), strict=True):
            assert result.status == "completed", result.error
            assert (
                result.output["actor"] == actor and result.output["memory"] == label + " " + actor
            )
            assert result.output["session"] == result.session_id == handle.session_id == session
            assert set(result.output["roles"]) == {"worker", "role-" + actor}
            original = services.execution_authorizations.verify_invocation(handle)
            assert original.principal == authority(services, actor).principal
            for identity in identities(services, handle):
                services.effect_journal.get(identity)
            assert native_snapshot(services, handle, backend)["present"]
        assert len(services.model.calls) == 2
    finally:
        release.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("denied", ["model.complete", "tool.invoke", "protocol.call"])
async def test_actual_action_denial_precedes_provider_tool_or_transport(
    tmp_path, engine_name, engine_type, backend, denied
):
    calls = []

    async def dispatch(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={"unexpected": True})

    services = runtime(
        tmp_path, backend, config=configuration(backend, denied=denied), dispatch=dispatch
    )
    engine = engine_type()
    actual_native(engine, engine_name)
    await engine.initialize(services)

    async def tool_call(payload, context):
        return await context.services.invoke_agent_tool("add_memory", {"text": uuid4().hex})

    services.catalog.register(CATALOG, tool_call)
    action = {"call": "llm:1.0.0@default", "with": {"prompt": "denied"}}
    if denied == "tool.invoke":
        action = {"call": CATALOG}
    elif denied == "protocol.call":
        action = {"call": "http", "with": {"method": "POST", "endpoint": ENDPOINT}}
    plan = compile_workflow(definition([{"denied": action}]))
    handle = create(services, engine_name, plan)
    try:
        result = await invoke_as(engine, services, plan, handle)
        assert (
            result.status == "faulted" and result.error["code"] == "execution_authorization_denied"
        )
        assert calls == [] and services.model.calls == []
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("case", ["authority", "origin", "decision", "seal", "claims", "policy"])
async def test_independent_authority_damage_rejects_before_owner_native_or_business_mutation(
    tmp_path, engine_name, engine_type, backend, case
):
    services = runtime(tmp_path, backend)
    engine = engine_type()
    actual_native(engine, engine_name)
    await engine.initialize(services)
    await engine._timer_dispatcher.stop()
    try:
        plan, handle = await suspend(engine, services, engine_name)
        assert len(services.model.calls) == 1
        corrupt(services, handle, case)
        before = snapshot(services, handle, backend)
        with authority_scope(authority(services, "bob")), pytest.raises(OwaError):
            await engine.resume(handle, {"substitute": True}, plan)
        assert snapshot(services, handle, backend) == before
        assert len(services.model.calls) == 1
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("detached", [False, True], ids=["synchronous", "detached"])
async def test_child_retains_original_authority_without_dispatcher_caller_substitution(
    tmp_path, engine_name, engine_type, backend, detached
):
    services = runtime(tmp_path, backend)
    engine = engine_type()
    actual_native(engine, engine_name)
    await engine.initialize(services)
    seen = []

    async def function(payload, context):
        seen.append(context.metadata["authority"]["principal"]["identity"])
        return {"actor": seen[-1]}

    services.catalog.register(CATALOG, function)
    child = services.workflow_catalog.register(
        compile_workflow(
            definition(
                [{"actor": {"call": CATALOG}}],
                name="child-" + uuid4().hex,
            )
        )
    )
    run = {"workflow": {"namespace": "authority", "name": child.name, "version": child.version}}
    if detached:
        run["await"] = False
    plan = compile_workflow(definition([{"child": {"run": run}}]))
    handle = create(services, engine_name, plan)
    try:
        result = await invoke_as(engine, services, plan, handle)
        assert result.status == "completed", result.error
        async with asyncio.timeout(40):
            while not seen:
                with authority_scope(authority(services, "bob")):
                    await engine._child_dispatcher.tick()
                await asyncio.sleep(0.01)
        children = services.effect_journal.connection.execute(
            "SELECT child_id FROM child_workflow_bindings WHERE parent_id=?",
            (handle.invocation_id,),
        ).fetchall()
        assert len(children) == 1 and seen == ["alice"]
        child_handle = services.invocations.get(children[0][0])
        async with asyncio.timeout(40):
            while services.invocations.get(child_handle.invocation_id).status != "completed":
                await asyncio.sleep(0.01)
        assert services.execution_authorizations.verify_invocation(child_handle) == authority(
            services, "alice"
        )
        assert (
            child_handle.owner_principal == "alice"
            and native_snapshot(services, child_handle, backend)["present"]
        )
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_schedule_slot_captures_original_claims_and_ignores_dispatcher_principal(
    tmp_path, engine_name, engine_type, backend
):
    services = runtime(tmp_path, backend)
    engine = engine_type()
    actual_native(engine, engine_name)
    await engine.initialize(services)
    seen = []

    async def function(payload, context):
        seen.append(context.metadata["authority"]["principal"]["identity"])
        return payload

    services.catalog.register(CATALOG, function)
    plan = compile_workflow(
        definition([{"actor": {"call": CATALOG}}], schedule={"after": {"milliseconds": 1}})
    )
    now = time.time()
    scheduler = WorkflowScheduler(services, engine, clock=lambda: now + 1)
    try:
        with authority_scope(authority(services, "alice")):
            schedule = services.schedules.create(
                plan, RAW, now=now, owner_principal="alice", event_scope="alice"
            )
        with authority_scope(authority(services, "bob")):
            assert await scheduler.tick(now=now + 1) == 1
            async with asyncio.timeout(40):
                while scheduler._workers:
                    await asyncio.sleep(0.01)
        assert seen == ["alice"] and not scheduler._errors
        slots = services.schedules.connection.execute(
            "SELECT invocation_id FROM schedule_slots WHERE schedule_id=?",
            (schedule.schedule_id,),
        ).fetchall()
        assert len(slots) == 1
        handle = services.invocations.get(slots[0][0])
        assert handle.status == "completed" and services.invocations.read_input(handle) == RAW
        assert services.execution_authorizations.verify_invocation(handle) == authority(
            services, "alice"
        )
        assert native_snapshot(services, handle, backend)["present"]
    finally:
        await scheduler.stop()
        await engine.shutdown()
        services.close()
