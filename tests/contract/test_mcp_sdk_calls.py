"""Identical native workflow MCP calls use one official SDK/common wire service."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import sqlite3
from contextlib import closing

import httpx
import mcp_types as types
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
from test_deadline_recovery import _policy_records
from test_durable_timers import _use_clock

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
        recovery_profile_provider=lambda engine: {"id": "mcp-fixture", "version": "1"},
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
            sent_parameters = calls[0]["body"]["params"]
            assert {key: value for key, value in sent_parameters.items() if key != "_meta"} == (
                METHOD_PARAMS[method]
            )
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
            tool_result = model.calls[1]["tool_results"][0]["result"]
            assert tool_result["resultType"] == "complete"
            assert tool_result["structuredContent"] == {"value": 7}
            assert "jsonrpc" not in tool_result and "id" not in tool_result
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "engine_name,engine_type", [case for case in engine_cases() if case[0] in {"adk", "langgraph"}]
)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_native_mcp_loop_keys_distinguish_receiver_commits_and_survive_restart_without_replay(
    tmp_path, engine_name, engine_type, backend
):
    engine = engine_type()
    native(engine, engine_name)
    receiver_path = tmp_path / "mcp-business.sqlite3"
    with closing(sqlite3.connect(receiver_path)) as receiver:
        receiver.execute(
            "CREATE TABLE commits(operation_key TEXT PRIMARY KEY,number INTEGER NOT NULL)"
        )
        receiver.commit()

    async def commit(context, params):
        key = context.request_id
        number = params.arguments["number"]
        assert isinstance(key, str)
        with closing(sqlite3.connect(receiver_path)) as receiver:
            with receiver:
                created = receiver.execute(
                    "INSERT INTO commits VALUES (?,?) ON CONFLICT(operation_key) DO NOTHING",
                    (key, number),
                ).rowcount
                recorded = receiver.execute(
                    "SELECT number FROM commits WHERE operation_key=?", (key,)
                ).fetchone()[0]
        return types.CallToolResult(
            content=[], structured_content={"number": recorded, "deduplicated": created == 0}
        )

    async with sdk_http_fixture(tool_handler=commit) as (transport, calls, endpoint):
        services = runtime(tmp_path, backend, engine_name)
        services.protocols = ProtocolServices(HttpClient(transport=transport))
        await engine.initialize(services)
        source = definition("tools/call", endpoint)
        action = source["do"][0]["mcp"]
        action["with"]["parameters"]["arguments"] = {"number": "${ $number }"}
        source["do"] = [
            {"repeat": {"for": {"each": "number", "in": [10, 20]}, "do": [{"mcp": action}]}}
        ]
        plan = compile_workflow(source)
        handle = services.invocations.create(
            engine=engine_name,
            session_id=None,
            user_id="original-user",
            owner_principal="original-principal",
            workflow_name=plan.name,
            workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
        )
        original_call = engine.executor._call
        reached = asyncio.Event()

        async def pause_after_second_receipt(call, payload, state):
            result = await original_call(call, payload, state)
            if len(calls) == 2:
                reached.set()
                await asyncio.Event().wait()
            return result

        engine.executor._call = pause_after_second_receipt
        running = asyncio.create_task(engine.invoke(plan, handle, {"original": True}))
        try:
            await asyncio.wait_for(reached.wait(), 15)
            assert len(calls) == 2
            keys = [call["headers"][b"idempotency-key"].decode() for call in calls]
            assert len(set(keys)) == 2
            assert keys == [call["body"]["id"] for call in calls]
            assert [call["body"]["params"]["arguments"] for call in calls] == [
                {"number": 10},
                {"number": 20},
            ]
            identities = business_identities(services, handle.invocation_id)
            assert len(identities) == 2
            assert {identity.task_reference for identity in identities} == {"/do/0/repeat/do/0/mcp"}
            receipts = [services.effect_journal.get(identity) for identity in identities]
            assert all(receipt.status == "completed" for receipt in receipts)
            assert sorted(
                receipt.output["structuredContent"]["number"] for receipt in receipts
            ) == [10, 20]
            assert all(
                not receipt.output["structuredContent"]["deduplicated"] for receipt in receipts
            )
            saved = {
                identity.key: ReceiptDependency.capture(identity, receipt).receipt_digest
                for identity, receipt in zip(identities, receipts, strict=True)
            }
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(running, 15)
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
            await engine.shutdown()
            services.close()

        recovered = runtime(tmp_path, backend, engine_name)
        recovered.protocols = ProtocolServices(HttpClient(transport=transport))
        resumed = engine_type()
        try:
            await resumed.initialize(recovered)
            persisted = recovered.invocations.get(handle.invocation_id)
            assert persisted.status == "running"
            assert persisted.user_id == "original-user"
            assert persisted.owner_principal == "original-principal"
            assert persisted.workflow_fingerprint == plan.fingerprint
            assert recovered.invocations.read_input(persisted) == {"original": True}
            result = await asyncio.wait_for(resumed.resume(persisted, {"changed": True}, plan), 15)
            assert result.status == "completed", result.error
            assert len(calls) == 2 and not recovered.model.calls
            retained = business_identities(recovered, handle.invocation_id)
            assert {identity.key for identity in retained} == set(saved)
            for identity in retained:
                receipt = recovered.effect_journal.get(identity)
                assert (
                    ReceiptDependency.capture(identity, receipt).receipt_digest
                    == saved[identity.key]
                )
                assert receipt.operation_id in keys
            with closing(sqlite3.connect(receiver_path)) as receiver:
                rows = receiver.execute(
                    "SELECT operation_key,number FROM commits ORDER BY number"
                ).fetchall()
            assert rows == list(zip(keys, [10, 20], strict=True))
        finally:
            await resumed.shutdown()
            recovered.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "engine_name,engine_type", [case for case in engine_cases() if case[0] in {"adk", "langgraph"}]
)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize(
    "expired", [False, True], ids=["remaining-original-budget", "original-expired"]
)
async def test_native_mcp_original_task_deadline_is_retained_after_waiting_restart(
    tmp_path, monkeypatch, engine_name, engine_type, backend, expired
):
    now = 1000.0
    _use_clock(monkeypatch, lambda: now)
    engine = engine_type()
    native(engine, engine_name)

    async def respond(context, params):
        return types.CallToolResult(content=[], structured_content={"input": params.arguments})

    async with sdk_http_fixture(tool_handler=respond) as (transport, calls, endpoint):
        services = runtime(tmp_path, backend, engine_name)
        services.protocols = ProtocolServices(HttpClient(transport=transport))
        await engine.initialize(services)
        assert engine._timer_dispatcher is not None
        await engine._timer_dispatcher.stop()
        source = definition("tools/call", endpoint)
        action = source["do"][0]["mcp"]
        action["with"]["parameters"]["arguments"] = {"value": "${ $workflow.input }"}
        source["do"] = [
            {
                "frame": {
                    "timeout": {"after": {"seconds": 30}},
                    "do": [{"pause": {"wait": {"seconds": 10}}}, {"mcp": action}],
                }
            }
        ]
        plan = compile_workflow(source)
        handle = services.invocations.create(
            engine=engine_name,
            session_id=None,
            user_id="deadline-user",
            owner_principal="deadline-principal",
            workflow_name=plan.name,
            workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
        )
        try:
            result = await asyncio.wait_for(engine.invoke(plan, handle, {"original": True}), 15)
            assert result.status == "waiting", result.error
            assert calls == [] and not services.model.calls
            assert handle.invocation_id not in engine._active
            assert handle.invocation_id not in engine._ownership_tasks
            assert handle._execution_lease is None
            timers = services.timers.pending_for_invocation(handle.invocation_id, plan.fingerprint)
            assert len(timers) == 1 and timers[0].due_at == 1010.0
            timing_proof = _policy_records(services, handle.invocation_id)
            assert len(timing_proof) == 2
        finally:
            await engine.shutdown()
            services.close()

        now = 1031.0 if expired else 1010.0
        recovered = runtime(tmp_path, backend, engine_name)
        recovered.protocols = ProtocolServices(HttpClient(transport=transport))
        resumed = engine_type()
        remaining = []
        try:
            await resumed.initialize(recovered)
            assert resumed._timer_dispatcher is not None
            await resumed._timer_dispatcher.stop()
            original_timed = resumed.executor._run_timed

            async def record_budget(
                operation, seconds, message, details, *, absolute_due=None, state=None
            ):
                if seconds is not None:
                    remaining.append((seconds, absolute_due))
                return await original_timed(
                    operation, seconds, message, details, absolute_due=absolute_due, state=state
                )

            resumed.executor._run_timed = record_budget
            persisted = recovered.invocations.get(handle.invocation_id)
            assert persisted.status == "waiting"
            assert persisted.user_id == "deadline-user"
            assert persisted.owner_principal == "deadline-principal"
            assert persisted.workflow_fingerprint == plan.fingerprint
            assert recovered.invocations.read_input(persisted) == {"original": True}
            retained_timer = recovered.timers.registration_for_key(timers[0].timer_key)
            assert retained_timer.identity == timers[0].identity and retained_timer.due_at == 1010.0
            result = await asyncio.wait_for(resumed.resume(persisted, {"changed": True}, plan), 15)
            assert remaining and remaining[0] == (0.0 if expired else 20.0, 1030.0)
            if expired:
                assert result.status == "faulted" and "timed out" in result.error["message"]
                assert result.error["details"]["status"] == 408
                assert calls == [] and business_identities(recovered, handle.invocation_id) == []
            else:
                assert result.status == "completed", result.error
                assert len(calls) == 1 and calls[0]["body"]["method"] == "tools/call"
                assert calls[0]["body"]["params"]["arguments"] == {"value": {"original": True}}
                assert result.output["structuredContent"] == {
                    "input": {"value": {"original": True}}
                }
                identities = business_identities(recovered, handle.invocation_id)
                assert len(identities) == 1
                assert recovered.effect_journal.get(identities[0]).status == "completed"
            assert not recovered.model.calls
            assert _policy_records(recovered, handle.invocation_id) == timing_proof
        finally:
            await resumed.shutdown()
            recovered.close()
