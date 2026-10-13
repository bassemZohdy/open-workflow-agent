"""Common action outcomes must survive the native task checkpoint boundary."""

from __future__ import annotations

import asyncio
import json
import os

import httpx
import pytest
from engine_cases import engine_cases
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.errors import OwaError, WorkflowExecutionError
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow

pytestmark = pytest.mark.usefixtures("explicit_workflow_delivery")


def _services(tmp_path, backend, handler):
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    config = (
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}})
        if datasource
        else RuntimeConfig()
    )
    services = RuntimeServices(
        config, model=FakeModel(), database_root=None if datasource else tmp_path
    )
    services.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(handler)))
    services.tools.protocols = services.protocols
    return services


def _plan():
    return compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "test",
                "name": "effect-recovery",
                "version": "1.0.0",
            },
            "do": [
                {
                    "action": {
                        "call": "http",
                        "with": {
                            "method": "POST",
                            "endpoint": "https://service.test/action",
                            "body": "${ . }",
                        },
                    }
                }
            ],
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("outcome", ["value", "null", "fault"])
@pytest.mark.parametrize("changed_request", [False, True])
async def test_action_receipt_survives_interruption_before_native_task_commit(
    tmp_path,
    backend,
    engine_name,
    engine_type,
    outcome,
    changed_request,
    explicit_workflow_delivery,
):
    calls = []

    async def handler(request):
        calls.append(request.headers["Idempotency-Key"])
        if outcome == "fault":
            return httpx.Response(503, json={"rejected": True})
        return httpx.Response(
            200,
            content=b"null" if outcome == "null" else b'{"accepted":true}',
            headers={"Content-Type": "application/json"},
        )

    plan = _plan()
    services = _services(tmp_path, backend, handler)
    engine = engine_type()
    await engine.initialize(services)
    handle = services.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    real_call = engine.executor._call
    reached = asyncio.Event()
    expected_error = None

    async def interrupted_call(call, payload, state):
        nonlocal expected_error
        try:
            result = await real_call(call, payload, state)
        except OwaError as exc:
            expected_error = exc.as_dict()
            reached.set()
            await asyncio.Event().wait()
            raise
        reached.set()
        await asyncio.Event().wait()
        return result

    engine.executor._call = interrupted_call
    running = asyncio.create_task(engine.invoke(plan, handle, {"request": "original"}))
    try:
        await asyncio.wait_for(reached.wait(), timeout=10)
        assert len(calls) == 1
        assert handle.status == "running"
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(running, timeout=10)
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()
        services.close()

    restarted = _services(tmp_path, backend, handler)
    resumed_engine = engine_type()
    await resumed_engine.initialize(restarted)
    if changed_request:
        resumed_call = resumed_engine.executor._call

        async def altered_call(call, payload, state):
            return await resumed_call(call, {**payload, "body": {"changed": True}}, state)

        resumed_engine.executor._call = altered_call
    persisted = restarted.invocations.get(handle.invocation_id)
    assert persisted is not None and persisted.status == "running"
    try:
        result = await asyncio.wait_for(
            resumed_engine.resume(persisted, {"resume": "different"}, plan), timeout=10
        )
        assert len(calls) == 1, "recorded actions must not dispatch again during replay"
        if changed_request:
            assert result.status == "faulted"
            assert "differs" in result.error["message"]
        elif outcome == "fault":
            assert result.status == "faulted"
            assert result.error["code"] == expected_error["code"]
            assert result.error["message"] == expected_error["message"]
        else:
            assert result.status == "completed", result.error
            assert result.output == (None if outcome == "null" else {"accepted": True})
        duplicate = await resumed_engine.resume(persisted, None, plan)
        assert duplicate.as_dict() == result.as_dict()
        assert len(calls) == 1
    finally:
        await resumed_engine.shutdown()
        restarted.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("scope", ["leaf", "retry", "competitive", "retry-competitive"])
async def test_pending_external_action_is_not_dispatched_again_on_recovery(
    tmp_path, backend, engine_name, engine_type, scope
):
    calls = []
    external_commit = asyncio.Event()

    async def ambiguous_handler(request):
        calls.append(request.headers["Idempotency-Key"])
        if len(calls) == (2 if "competitive" in scope else 1):
            external_commit.set()
        await asyncio.Event().wait()
        return httpx.Response(200, json={"accepted": True})

    plan = _plan()
    workflow = plan.source
    if "competitive" in scope:
        action = workflow["do"][0]["action"]
        workflow["do"] = [
            {"race": {"fork": {"compete": True, "branches": [{"one": action}, {"two": action}]}}}
        ]
    if "retry" in scope:
        workflow["do"] = [
            {
                "retried": {
                    "try": workflow["do"],
                    "catch": {"retry": {"limit": {"attempt": {"count": 1}}}},
                }
            }
        ]
    plan = compile_workflow(workflow)
    services = _services(tmp_path, backend, ambiguous_handler)
    prior_decisions = {
        row[0]
        for row in services.fork_decisions.connection.execute(
            "SELECT identity FROM competitive_fork_decisions"
        ).fetchall()
    }
    engine = engine_type()
    await engine.initialize(services)
    handle = services.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    running = asyncio.create_task(engine.invoke(plan, handle, {}))
    try:
        await asyncio.wait_for(external_commit.wait(), timeout=10)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(running, timeout=10)
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()
        services.close()

    async def replay_handler(request):
        calls.append(request.headers["Idempotency-Key"])
        return httpx.Response(200, json={"accepted": True})

    restarted = _services(tmp_path, backend, replay_handler)
    resumed_engine = engine_type()
    await resumed_engine.initialize(restarted)
    persisted = restarted.invocations.get(handle.invocation_id)
    assert persisted is not None and persisted.status == "running"
    try:
        result = await asyncio.wait_for(resumed_engine.resume(persisted, {}, plan), timeout=10)
        assert len(calls) == (2 if "competitive" in scope else 1), (
            "an ambiguous external commit requires reconciliation"
        )
        assert result.status == "faulted"
        assert result.error["code"] == "workflow_execution_error"
        assert "unresolved" in result.error["message"]
        assert {
            row[0]
            for row in restarted.fork_decisions.connection.execute(
                "SELECT identity FROM competitive_fork_decisions"
            ).fetchall()
        } == prior_decisions, "uncertain actions cannot establish a fork winner"
    finally:
        await resumed_engine.shutdown()
        restarted.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_repeated_reusable_call_receipts_preserve_each_occurrence_on_recovery(
    tmp_path, backend, engine_name, engine_type
):
    calls = []

    async def handler(request):
        body = json.loads(request.content)
        calls.append(body["number"])
        return httpx.Response(200, json={"accepted": body["number"]})

    workflow = _plan().source
    workflow["use"] = {
        "functions": {
            "shared": {
                "call": "http",
                "with": {
                    "method": "POST",
                    "endpoint": "https://service.test/action",
                    "body": "${ . }",
                },
            }
        }
    }
    workflow["do"] = [
        {
            "repeat": {
                "for": {"each": "number", "in": [10, 20, 30]},
                "do": [{"invoke": {"call": "shared", "with": {"number": "${ $number }"}}}],
            }
        }
    ]
    plan = compile_workflow(workflow)
    services = _services(tmp_path, backend, handler)
    engine = engine_type()
    await engine.initialize(services)
    handle = services.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    real_call = engine.executor._call
    second_receipt = asyncio.Event()

    async def interrupted_call(call, payload, state):
        result = await real_call(call, payload, state)
        if len(calls) == 2:
            second_receipt.set()
            await asyncio.Event().wait()
        return result

    engine.executor._call = interrupted_call
    running = asyncio.create_task(engine.invoke(plan, handle, {}))
    try:
        await asyncio.wait_for(second_receipt.wait(), timeout=10)
        assert calls == [10, 20]
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(running, timeout=10)
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()
        services.close()

    restarted = _services(tmp_path, backend, handler)
    resumed_engine = engine_type()
    await resumed_engine.initialize(restarted)
    persisted = restarted.invocations.get(handle.invocation_id)
    assert persisted is not None and persisted.status == "running"
    try:
        result = await asyncio.wait_for(resumed_engine.resume(persisted, None, plan), timeout=10)
        assert result.status == "completed", result.error
        assert result.output == {"accepted": 30}
        assert calls == [10, 20, 30]
    finally:
        await resumed_engine.shutdown()
        restarted.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_action_receipt_write_failure_does_not_enter_workflow_retry(
    tmp_path, backend, engine_name, engine_type
):
    calls = []

    async def handler(request):
        calls.append(request.headers["Idempotency-Key"])
        return httpx.Response(200, json={"accepted": True})

    workflow = _plan().source
    workflow["do"] = [
        {
            "retried": {
                "try": workflow["do"],
                "catch": {"retry": {"limit": {"attempt": {"count": 1}}}},
            }
        }
    ]
    plan = compile_workflow(workflow)
    services = _services(tmp_path, backend, handler)
    engine = engine_type()
    await engine.initialize(services)
    handle = services.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    attempted_identities = []

    def failed_receipt(identity, owner, **outcome):
        attempted_identities.append(identity)
        raise WorkflowExecutionError("simulated store failure")

    services.effect_journal.finish = failed_receipt
    try:
        result = await asyncio.wait_for(engine.invoke(plan, handle, {}), timeout=10)
        assert result.status == "faulted"
        assert "reconciled" in result.error["message"]
        assert len(calls) == 1
        assert len(attempted_identities) == 1
        receipt = services.effect_journal.get(attempted_identities[0])
        assert receipt is not None and receipt.status == "pending"
    finally:
        await engine.shutdown()
        services.close()
