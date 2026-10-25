"""Actual principal SDKs share typed A2A calls and durable common outcomes."""

from __future__ import annotations

import asyncio
import copy
import json
import os

import httpx
import pytest
from _a2a_sdk_fixture import sdk_a2a_fixture
from engine_cases import engine_cases
from open_workflow_agent.api import create_app
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
ENGINES = [case for case in engine_cases() if case[0] in {"adk", "langgraph"}]
MESSAGE = {"role": "ROLE_USER", "messageId": "original-message", "parts": [{"text": "hello"}]}
PARAMS = {
    "message/send": {"message": MESSAGE},
    "message/stream": {"message": MESSAGE},
    "tasks/get": {"id": "task-id"},
    "tasks/list": {},
    "tasks/cancel": {"id": "task-id"},
    "tasks/resubscribe": {"id": "task-id"},
    "agent/getAuthenticatedExtendedCard": {},
}
WIRE_METHODS = {
    "message/send": "SendMessage",
    "message/stream": "SendStreamingMessage",
    "tasks/get": "GetTask",
    "tasks/list": "ListTasks",
    "tasks/cancel": "CancelTask",
    "tasks/resubscribe": "SubscribeToTask",
    "agent/getAuthenticatedExtendedCard": "GetExtendedAgentCard",
}


def runtime(path, backend, engine_name, *, model=None, options=None):
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "persistence": {"datasource": datasource},
            **(options or {}),
        }
    )
    services = RuntimeServices(
        config,
        model=model or FakeModel(),
        database_root=None if datasource else path,
        recovery_profile_provider=lambda engine: {"id": "a2a-native-fixture", "version": "1"},
    )
    assert services.invocations.connection.kind == backend
    assert services.effect_journal.connection.kind == backend
    if backend == "postgresql":
        assert services.database_root is None
        assert services.engine_database_path(engine_name, create_namespace=False).startswith(
            "postgres"
        )
    return services


def native(engine, name):
    if not (engine.native.available if name == "adk" else engine.native.task_nodes_available):
        pytest.skip(f"actual {name} SDK is unavailable in this independent environment")


def definition(endpoint, method="message/send"):
    return {
        "document": {
            "dsl": "1.0.3",
            "namespace": "a2a-contract",
            "name": "wire",
            "version": "1.0.0",
        },
        "do": [
            {
                "peer": {
                    "call": "a2a",
                    "with": {
                        "server": endpoint,
                        "method": method,
                        **({"parameters": copy.deepcopy(PARAMS[method])} if PARAMS[method] else {}),
                    },
                }
            }
        ],
    }


def handle(services, engine_name, plan, *, owner_principal="original-owner"):
    return services.invocations.create(
        engine=engine_name,
        session_id="original-session",
        user_id="original-user",
        owner_principal=owner_principal,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )


def identities(services, invocation_id):
    rows = services.effect_journal.connection.execute(
        "SELECT payload FROM workflow_effect_invocations WHERE invocation_id=?",
        (invocation_id,),
    ).fetchall()
    return [
        item
        for row in rows
        if (item := EffectIdentity.from_value(json.loads(row[0]))).operation == "call:a2a"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("method", PARAMS)
async def test_native_workflow_operations_return_only_typed_sdk_results(
    tmp_path, engine_name, engine_type, backend, method
):
    engine = engine_type()
    native(engine, engine_name)
    services = runtime(tmp_path, backend, engine_name)
    try:
        async with sdk_a2a_fixture() as (transport, calls, endpoint):
            services.protocols = ProtocolServices(HttpClient(transport=transport))
            await engine.initialize(services)
            plan = compile_workflow(definition(endpoint, method))
            invocation = handle(services, engine_name, plan)
            result = await engine.invoke(await engine.compile(plan), invocation, {"original": True})
            assert result.status == "completed", result.error
            assert len(calls) == 1 and calls[0]["method"] == "POST"
            call = calls[0]
            request = call["body"]
            assert call["headers"][b"a2a-version"] == b"1.0"
            # Compare stable wire semantics across both actual engines while
            # correlating the generated JSON-RPC ID instead of fixing its value.
            assert request["method"] == WIRE_METHODS[method]
            assert request["params"] == PARAMS[method]
            assert call["headers"][b"idempotency-key"] == str(request["id"]).encode()
            if isinstance(result.output, list):
                assert result.output and "statusUpdate" in result.output[-1]
            else:
                assert isinstance(result.output, dict) and "jsonrpc" not in result.output
            keys = identities(services, invocation.invocation_id)
            assert len(keys) == 1 and keys[0].task_reference == "/do/0/peer"
            receipt = services.effect_journal.get(keys[0])
            assert receipt.status == "completed" and receipt.output == result.output
            assert receipt.operation_id == request["id"]
            assert services.model.calls == []
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_native_configured_agent_tool_calls_the_same_official_peer(
    tmp_path, engine_name, engine_type, backend
):
    engine = engine_type()
    native(engine, engine_name)
    async with sdk_a2a_fixture() as (transport, calls, endpoint):
        model = FakeModel(
            [
                {"tool_call": {"name": "peer", "arguments": {"parameters": {"message": MESSAGE}}}},
                {"response": "tool-result-used"},
            ]
        )
        services = runtime(
            tmp_path,
            backend,
            engine_name,
            model=model,
            options={
                "tools": [
                    {
                        "type": "a2a",
                        "name": "peer",
                        "endpoint": endpoint,
                        "options": {"method": "SendMessage"},
                    }
                ]
            },
        )
        services.protocols = ProtocolServices(HttpClient(transport=transport))
        services.tools.protocols = services.protocols
        try:
            await engine.initialize(services)
            assert "peer" in {getattr(tool, "name", "") for tool in engine.agent.tools}
            plan = compile_workflow()
            invocation = handle(services, engine_name, plan)
            result = await engine.invoke(await engine.compile(plan), invocation, {})
            assert result.status == "completed" and result.output == {
                "response": "tool-result-used"
            }
            assert len(calls) == 1
            call = calls[0]
            request = call["body"]
            assert request["method"] == "SendMessage"
            assert request["params"] == {"message": MESSAGE}
            assert call["headers"][b"idempotency-key"] == str(request["id"]).encode()
            # Native agent tools are direct agent calls, not workflow call
            # operations, so they do not create a workflow-effect receipt.
            assert identities(services, invocation.invocation_id) == []
            assert len(model.calls) == 2 and model.response == []
        finally:
            await engine.shutdown()
            services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_accepted_remote_task_with_lost_correlation_stays_pending_without_catch_replay(
    tmp_path, engine_name, engine_type, backend
):
    engine = engine_type()
    native(engine, engine_name)
    async with sdk_a2a_fixture(response_mutation=lambda value: {**value, "id": "foreign"}) as (
        transport,
        calls,
        endpoint,
    ):
        services = runtime(tmp_path, backend, engine_name)
        services.protocols = ProtocolServices(HttpClient(transport=transport))
        try:
            await engine.initialize(services)
            source = definition(endpoint)
            source["do"] = [
                {
                    "guard": {
                        "try": source["do"],
                        "catch": {
                            "errors": {"with": {"type": "${ true }"}},
                            "do": [{"invalid": {"set": {"fabricated": True}}}],
                        },
                    }
                }
            ]
            plan = compile_workflow(source)
            invocation = handle(services, engine_name, plan)
            result = await engine.invoke(await engine.compile(plan), invocation, {})
            assert result.status == "faulted" and len(calls) == 1
            assert (
                "unresolved" in result.error["message"].lower()
                or "recovery" in result.error["message"].lower()
            )
            business = identities(services, invocation.invocation_id)
            assert (
                len(business) == 1 and services.effect_journal.get(business[0]).status == "pending"
            )
            assert services.model.calls == []
            # Terminal reads cannot turn uncertainty into another SendMessage.
            again = await engine.resume(invocation, {}, plan)
            assert again == result and len(calls) == 1
        finally:
            await engine.shutdown()
            services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("outcome", ["value", "null", "fault"])
async def test_original_known_a2a_receipt_survives_native_restart_without_second_task(
    tmp_path, engine_name, engine_type, backend, outcome
):
    engine = engine_type()
    native(engine, engine_name)
    async with sdk_a2a_fixture() as (transport, calls, endpoint):
        services = runtime(tmp_path, backend, engine_name)
        services.protocols = ProtocolServices(HttpClient(transport=transport))
        await engine.initialize(services)
        source = definition(endpoint, "tasks/get" if outcome == "fault" else "message/send")
        params = source["do"][0]["peer"]["with"]["parameters"]
        if outcome == "fault":
            params["id"] = "missing-task"
        elif outcome == "null":
            params["message"]["parts"] = [{"data": None, "mediaType": "application/json"}]
        plan = compile_workflow(source)
        invocation = handle(services, engine_name, plan)
        reached = asyncio.Event()
        original_call = engine.executor._call

        async def after_receipt(call, payload, state):
            try:
                value = await original_call(call, payload, state)
            except OwaError:
                reached.set()
                await asyncio.Event().wait()
                raise
            reached.set()
            await asyncio.Event().wait()
            return value

        engine.executor._call = after_receipt
        running = asyncio.create_task(
            engine.invoke(await engine.compile(plan), invocation, {"original": True})
        )
        try:
            await asyncio.wait_for(reached.wait(), 15)
            business = identities(services, invocation.invocation_id)
            assert len(business) == 1 and len(calls) == 1
            receipt = services.effect_journal.get(business[0])
            assert receipt.status == ("faulted" if outcome == "fault" else "completed")
            digest = ReceiptDependency.capture(business[0], receipt).receipt_digest
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
            loaded = recovered.invocations.get(invocation.invocation_id)
            result = await asyncio.wait_for(resumed.resume(loaded, {"changed": True}, plan), 15)
            assert result.status == ("faulted" if outcome == "fault" else "completed"), result.error
            assert recovered.invocations.read_input(loaded) == {"original": True}
            assert len(calls) == 1 and recovered.model.calls == []
            assert (
                ReceiptDependency.capture(
                    business[0], recovered.effect_journal.get(business[0])
                ).receipt_digest
                == digest
            )
            if outcome == "null":
                assert result.output["task"]["artifacts"][0]["parts"][0]["data"] is None
        finally:
            await resumed.shutdown()
            recovered.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_loop_visits_keep_distinct_message_and_action_keys_across_restart(
    tmp_path, engine_name, engine_type, backend
):
    engine = engine_type()
    native(engine, engine_name)
    async with sdk_a2a_fixture() as (transport, calls, endpoint):
        services = runtime(tmp_path, backend, engine_name)
        services.protocols = ProtocolServices(HttpClient(transport=transport))
        await engine.initialize(services)
        source = definition(endpoint)
        action = source["do"][0]["peer"]
        action["with"]["parameters"]["message"].pop("messageId")
        action["with"]["parameters"]["message"]["parts"] = [{"text": "${ $number | tostring }"}]
        source["do"] = [
            {"repeat": {"for": {"each": "number", "in": [10, 20]}, "do": [{"peer": action}]}}
        ]
        plan = compile_workflow(source)
        invocation = handle(services, engine_name, plan)
        reached = asyncio.Event()
        original = engine.executor._call

        async def after_second(call, payload, state):
            value = await original(call, payload, state)
            if len(calls) == 2:
                reached.set()
                await asyncio.Event().wait()
            return value

        engine.executor._call = after_second
        running = asyncio.create_task(engine.invoke(plan, invocation, {"original": True}))
        try:
            await asyncio.wait_for(reached.wait(), 15)
            assert len(calls) == 2
            keys = [call["headers"][b"idempotency-key"].decode() for call in calls]
            messages = [call["body"]["params"]["message"] for call in calls]
            assert len(set(keys)) == 2 and keys == [call["body"]["id"] for call in calls]
            assert len({message["messageId"] for message in messages}) == 2
            assert [message["parts"][0]["text"] for message in messages] == ["10", "20"]
            business = identities(services, invocation.invocation_id)
            assert len(business) == 2
            proofs = {
                item.key: ReceiptDependency.capture(
                    item, services.effect_journal.get(item)
                ).receipt_digest
                for item in business
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
            loaded = recovered.invocations.get(invocation.invocation_id)
            result = await asyncio.wait_for(resumed.resume(loaded, {"changed": True}, plan), 15)
            assert result.status == "completed" and len(calls) == 2
            assert result.output["task"]["artifacts"][0]["parts"][0]["text"] == "20"
            for item in business:
                assert (
                    ReceiptDependency.capture(
                        item, recovered.effect_journal.get(item)
                    ).receipt_digest
                    == proofs[item.key]
                )
            assert recovered.invocations.read_input(loaded) == {"original": True}
            assert recovered.model.calls == []
        finally:
            await resumed.shutdown()
            recovered.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("expired", [False, True])
async def test_original_deadline_is_preserved_before_a2a_receiver_dispatch(
    tmp_path, monkeypatch, engine_name, engine_type, backend, expired
):
    now = [1000.0]
    _use_clock(monkeypatch, lambda: now[0])
    engine = engine_type()
    native(engine, engine_name)
    async with sdk_a2a_fixture() as (transport, calls, endpoint):
        services = runtime(tmp_path, backend, engine_name)
        services.protocols = ProtocolServices(HttpClient(transport=transport))
        await engine.initialize(services)
        source = definition(endpoint)
        action = source["do"][0]["peer"]
        action["with"]["parameters"]["message"]["parts"] = [{"data": "${ $workflow.input }"}]
        source["do"] = [
            {
                "frame": {
                    "timeout": {"after": {"seconds": 30}},
                    "do": [
                        {"pause": {"wait": {"seconds": 10}}},
                        {"peer": action},
                    ],
                }
            }
        ]
        plan = compile_workflow(source)
        invocation = handle(services, engine_name, plan)
        try:
            result = await asyncio.wait_for(engine.invoke(plan, invocation, {"original": True}), 15)
            assert result.status == "waiting" and calls == []
            timing = _policy_records(services, invocation.invocation_id)
            assert len(timing) == 2
        finally:
            await engine.shutdown()
            services.close()
        now[0] = 1031.0 if expired else 1010.0
        recovered = runtime(tmp_path, backend, engine_name)
        recovered.protocols = ProtocolServices(HttpClient(transport=transport))
        resumed = engine_type()
        budgets = []
        try:
            await resumed.initialize(recovered)
            original = resumed.executor._run_timed

            async def observe_budget(operation, seconds, message, details, **kwargs):
                if details.get("instance") == "/do/0/frame":
                    budgets.append((seconds, kwargs.get("absolute_due")))
                return await original(operation, seconds, message, details, **kwargs)

            resumed.executor._run_timed = observe_budget
            loaded = recovered.invocations.get(invocation.invocation_id)
            result = await asyncio.wait_for(resumed.resume(loaded, {"changed": True}, plan), 15)
            assert budgets and budgets[0] == (0.0 if expired else 20.0, 1030.0)
            if expired:
                assert result.status == "faulted" and result.error["details"]["status"] == 408
                assert calls == [] and identities(recovered, invocation.invocation_id) == []
            else:
                assert result.status == "completed" and len(calls) == 1
                assert result.output["task"]["artifacts"][0]["parts"][0]["data"] == {
                    "original": True
                }
            assert _policy_records(recovered, invocation.invocation_id) == timing
            assert recovered.model.calls == []
        finally:
            await resumed.shutdown()
            recovered.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("surface", ["jsonrpc", "http_json"])
async def test_known_receipt_recovery_never_refetches_mutable_agent_card(
    tmp_path, engine_name, engine_type, backend, surface
):
    engine = engine_type()
    native(engine, engine_name)
    async with sdk_a2a_fixture() as (transport, calls, endpoint):
        services = runtime(tmp_path, backend, engine_name)
        services.protocols = ProtocolServices(HttpClient(transport=transport))
        await engine.initialize(services)
        source = definition("https://ignored.test/wrong")
        source["do"][0]["peer"]["with"]["agentCard"] = {
            "endpoint": endpoint.rsplit("/", 1)[0] + "/.well-known/agent-card.json"
        }
        plan = compile_workflow(source)
        invocation = handle(services, engine_name, plan, owner_principal="anonymous")
        reached = asyncio.Event()
        original = engine.executor._call

        async def after_receipt(call, payload, state):
            value = await original(call, payload, state)
            reached.set()
            await asyncio.Event().wait()
            return value

        engine.executor._call = after_receipt
        running = asyncio.create_task(engine.invoke(plan, invocation, {"original": True}))
        try:
            await asyncio.wait_for(reached.wait(), 15)
            assert [call["method"] for call in calls] == ["GET", "POST"]
            business = identities(services, invocation.invocation_id)
            assert len(business) == 1
            receipt = services.effect_journal.get(business[0])
            assert receipt.status == "completed"
            proof = ReceiptDependency.capture(business[0], receipt).receipt_digest
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(running, 15)
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
            await engine.shutdown()
            services.close()
        recovered = runtime(
            tmp_path,
            backend,
            engine_name,
            options={"a2a": {"enabled": True, "transport": surface}},
        )
        recovered.protocols = ProtocolServices(HttpClient(transport=transport))
        resumed = engine_type()
        try:
            await resumed.initialize(recovered)
            app = create_app(config=recovered.config, services=recovered, engine=resumed)
            recovered_handle = recovered.invocations.get(invocation.invocation_id)
            result = await asyncio.wait_for(resumed.resume(recovered_handle, {}, plan), 15)
            assert result.status == "completed" and len(calls) == 2
            assert (
                ReceiptDependency.capture(
                    business[0], recovered.effect_journal.get(business[0])
                ).receipt_digest
                == proof
            )
            assert recovered.model.calls == []

            # Once known-receipt recovery has produced a terminal Task, public
            # GetTask must be a stable projection and must not consult the peer.
            network_attempts: list[str] = []

            async def reject_network(request: httpx.Request) -> httpx.Response:
                network_attempts.append(str(request.url))
                raise AssertionError("terminal A2A recovery must not contact the peer")

            recovered.protocols = ProtocolServices(
                HttpClient(transport=httpx.MockTransport(reject_network))
            )
            recovered.tools.protocols = recovered.protocols
            headers = {"A2A-Version": "1.0"}
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                headers=headers,
            ) as client:
                if surface == "jsonrpc":
                    request = {
                        "jsonrpc": "2.0",
                        "id": "known-receipt-get-task",
                        "method": "GetTask",
                        "params": {"id": invocation.invocation_id},
                    }
                    first_response = await client.post("/a2a", json=request)
                    second_response = await client.post("/a2a", json=request)
                    assert first_response.status_code == second_response.status_code == 200
                    first = first_response.json()["result"]
                    second = second_response.json()["result"]
                else:
                    path = f"/a2a/tasks/{invocation.invocation_id}"
                    first_response = await client.get(path)
                    second_response = await client.get(path)
                    assert first_response.status_code == second_response.status_code == 200
                    first = first_response.json()
                    second = second_response.json()

            assert first == second
            assert first["id"] == invocation.invocation_id
            assert first["status"]["state"] == "TASK_STATE_COMPLETED"
            assert network_attempts == [] and len(calls) == 2
            assert recovered.invocations.get(invocation.invocation_id) == recovered_handle
            assert recovered.invocations.read_input(recovered_handle) == {"original": True}
            assert len(identities(recovered, invocation.invocation_id)) == 1
            assert (
                ReceiptDependency.capture(
                    business[0], recovered.effect_journal.get(business[0])
                ).receipt_digest
                == proof
            )
        finally:
            await resumed.shutdown()
            recovered.close()
