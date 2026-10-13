"""Identical native workflow MCP calls use one official SDK/common wire service."""

from __future__ import annotations

import asyncio
import copy
import json
import os

import httpx
import pytest
from _mcp_sdk_fixture import METHOD_PARAMS, sdk_http_fixture
from engine_cases import engine_cases
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.effects import EffectIdentity, ReceiptDependency
from open_workflow_agent.errors import OwaError
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow

pytestmark = pytest.mark.usefixtures("explicit_workflow_delivery")


def native(engine, name):
    available = engine.native.available if name == "adk" else engine.native.task_nodes_available
    if not available:
        pytest.skip(f"actual {name} SDK is unavailable in this independent environment")


def config(backend):
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    return RuntimeConfig.model_validate(
        {"model": {"provider": "fake"}, "persistence": {"datasource": datasource}}
    )


def definition(method, endpoint):
    return {
        "document": {
            "dsl": "1.0.3",
            "namespace": "mcp-contract",
            "name": "wire",
            "version": "1.0.0",
        },
        "do": [
            {
                "mcp": {
                    "call": "mcp",
                    "with": {
                        "protocolVersion": "2026-07-28",
                        "method": method,
                        "parameters": copy.deepcopy(METHOD_PARAMS[method]),
                        "transport": {"http": {"endpoint": endpoint}},
                    },
                }
            }
        ],
    }


def business_identities(services, invocation_id):
    rows = services.effect_journal.connection.execute(
        "SELECT payload FROM workflow_effect_invocations WHERE invocation_id=?",
        (invocation_id,),
    ).fetchall()
    identities = [EffectIdentity.from_value(json.loads(row[0])) for row in rows]
    return [identity for identity in identities if identity.operation == "call:mcp"]


def runtime(tmp_path, backend, engine_name, *, model=None, configuration=None):
    services = RuntimeServices(
        configuration or config(backend),
        model=model or FakeModel(),
        database_root=None if backend == "postgresql" else tmp_path,
        recovery_profile_provider=lambda: {"id": "mcp-fixture", "version": "1"},
    )
    assert services.invocations.connection.kind == backend
    assert services.effect_journal.connection.kind == backend
    if backend == "postgresql":
        assert services.database_root is None and services.datasource is not None
        assert services.engine_database_path(engine_name, create_namespace=False).startswith(
            "postgres"
        )
    else:
        assert services.database_root == tmp_path
    return services


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "engine_name,engine_type", [case for case in engine_cases() if case[0] in {"adk", "langgraph"}]
)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("method", METHOD_PARAMS)
async def test_each_native_workflow_method_preserves_typed_result_reference_and_one_dispatch(
    tmp_path, engine_name, engine_type, backend, method
):
    engine = engine_type()
    native(engine, engine_name)
    services = runtime(tmp_path, backend, engine_name)
    try:
        async with sdk_http_fixture() as (transport, calls, endpoint):
            services.protocols = ProtocolServices(HttpClient(transport=transport))
            await engine.initialize(services)
            plan = compile_workflow(definition(method, endpoint))
            handle = services.invocations.create(
                engine=engine_name,
                session_id=None,
                user_id="mcp-user",
                owner_principal="mcp-owner",
                workflow_name=plan.name,
                workflow_version=plan.version,
                workflow_fingerprint=plan.fingerprint,
            )
            result = await engine.invoke(await engine.compile(plan), handle, {"original": True})
            assert result.status == "completed" and result.output["resultType"] == "complete"
            assert "jsonrpc" not in result.output and "id" not in result.output
            assert len(calls) == 1 and calls[0]["body"]["method"] == method
            business = business_identities(services, handle.invocation_id)
            assert len(business) == 1 and business[0].task_reference == "/do/0/mcp"
            receipt = services.effect_journal.get(business[0])
            assert receipt.status == "completed" and receipt.output == result.output
            assert services.model.calls == []
            assert await engine._native_initial_history_exists(handle)
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "engine_name,engine_type", [case for case in engine_cases() if case[0] in {"adk", "langgraph"}]
)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_receiver_commit_lost_mcp_response_retains_pending_without_retry_or_model(
    tmp_path, engine_name, engine_type, backend
):
    engine = engine_type()
    native(engine, engine_name)
    commits = []

    async def receiver(request):
        body = json.loads(request.content)
        commits.append(body["id"])
        # The remote action commits, then returns an uncorrelated response.
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": "wrong-owner",
                "result": {"resultType": "complete", "content": []},
            },
        )

    services = runtime(tmp_path, backend, engine_name)
    services.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(receiver)))
    try:
        await engine.initialize(services)
        source = definition("tools/call", "https://receiver.test/mcp")
        source["do"] = [
            {
                "guard": {
                    "try": source["do"],
                    "catch": {
                        "errors": {"with": {"type": "${ true }"}},
                        "do": [{"badRetry": {"set": {"fabricated": True}}}],
                    },
                }
            }
        ]
        plan = compile_workflow(source)
        handle = services.invocations.create(
            engine=engine_name,
            session_id=None,
            user_id=None,
            workflow_name=plan.name,
            workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
        )
        result = await engine.invoke(await engine.compile(plan), handle, {})
        assert result.status == "faulted" and len(commits) == 1
        assert (
            "unresolved" in result.error["message"].lower()
            or "recovery" in result.error["message"].lower()
        )
        entries = business_identities(services, handle.invocation_id)
        assert len(entries) == 1 and services.effect_journal.get(entries[0]).status == "pending"
        assert services.model.calls == []
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "engine_name,engine_type", [case for case in engine_cases() if case[0] in {"adk", "langgraph"}]
)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_configured_agent_tool_uses_same_native_sdk_http_service(
    tmp_path, engine_name, engine_type, backend
):
    engine = engine_type()
    native(engine, engine_name)
    model = FakeModel(
        [
            {"tool_call": {"name": "lookup", "arguments": {"arguments": {"value": 7}}}},
            {"response": "tool-result-used"},
        ]
    )
    async with sdk_http_fixture() as (transport, calls, endpoint):
        value = config(backend).model_dump(mode="json", exclude_unset=True)
        value["tools"] = [
            {"type": "mcp", "name": "lookup", "endpoint": endpoint, "options": {"name": "lookup"}}
        ]
        services = runtime(
            tmp_path,
            backend,
            engine_name,
            model=model,
            configuration=RuntimeConfig.model_validate(value),
        )
        services.protocols = ProtocolServices(HttpClient(transport=transport))
        services.tools.protocols = services.protocols
        try:
            await engine.initialize(services)
            assert "lookup" in {getattr(tool, "name", "") for tool in engine.agent.tools}
            plan = compile_workflow()
            handle = services.invocations.create(
                engine=engine_name,
                session_id=None,
                user_id="mcp-user",
                workflow_name=plan.name,
                workflow_version=plan.version,
                workflow_fingerprint=plan.fingerprint,
            )
            result = await engine.invoke(await engine.compile(plan), handle, {"question": "lookup"})
            assert result.status == "completed" and result.output["response"] == "tool-result-used"
            assert len(calls) == 1 and len(model.calls) == 2
            assert calls[0]["body"]["method"] == "tools/call"
            assert calls[0]["body"]["params"]["arguments"] == {"value": 7}
        finally:
            await engine.shutdown()
            services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "engine_name,engine_type", [case for case in engine_cases() if case[0] in {"adk", "langgraph"}]
)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("outcome", ["value", "null", "fault"])
async def test_native_restart_reuses_known_mcp_receipt_without_discovery_or_replay(
    tmp_path, engine_name, engine_type, backend, outcome
):
    calls = []

    async def receiver(request):
        body = json.loads(request.content)
        calls.append(body)
        result = {
            "resultType": "complete",
            "content": [],
            "structuredContent": None if outcome == "null" else {"value": 7},
            "isError": outcome == "fault",
        }
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    engine = engine_type()
    native(engine, engine_name)
    services = runtime(tmp_path, backend, engine_name)
    services.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(receiver)))
    await engine.initialize(services)
    plan = compile_workflow(definition("tools/call", "https://receiver.test/mcp"))
    handle = services.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id="original-user",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    original_call = engine.executor._call
    reached = asyncio.Event()

    async def pause_after_common_receipt(call, payload, state):
        try:
            result = await original_call(call, payload, state)
        except OwaError:
            reached.set()
            await asyncio.Event().wait()
            raise
        reached.set()
        await asyncio.Event().wait()
        return result

    engine.executor._call = pause_after_common_receipt
    running = asyncio.create_task(
        engine.invoke(await engine.compile(plan), handle, {"original": True})
    )
    try:
        await asyncio.wait_for(reached.wait(), timeout=10)
        assert len(calls) == 1 and handle.status == "running"
        identities = business_identities(services, handle.invocation_id)
        assert len(identities) == 1
        saved = services.effect_journal.get(identities[0])
        assert saved.status == ("faulted" if outcome == "fault" else "completed")
        saved_hash = ReceiptDependency.capture(identities[0], saved).receipt_digest
        saved_key = saved.operation_id
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(running, timeout=10)
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()
        services.close()

    recovered = runtime(tmp_path, backend, engine_name)
    recovered.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(receiver)))
    resumed = engine_type()
    try:
        await resumed.initialize(recovered)
        persisted = recovered.invocations.get(handle.invocation_id)
        assert persisted is not None and persisted.status == "running"
        result = await asyncio.wait_for(
            resumed.resume(persisted, {"changed": True}, plan), timeout=10
        )
        assert result.status == ("faulted" if outcome == "fault" else "completed")
        if outcome != "fault":
            assert result.output["structuredContent"] == (
                None if outcome == "null" else {"value": 7}
            )
        retained = recovered.effect_journal.get(identities[0])
        assert ReceiptDependency.capture(identities[0], retained).receipt_digest == saved_hash
        assert retained.operation_id == saved_key
        assert len(calls) == 1 and calls[0]["method"] == "tools/call"
        assert recovered.model.calls == []
    finally:
        await resumed.shutdown()
        recovered.close()
