"""Configured HTTP replay admits a root checkpoint into a new, lineage-bound invocation."""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import httpx
import pytest
from native_state_test_support import authority, configuration
from open_workflow_agent.api import create_app
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import NativeStateReplayConfig
from open_workflow_agent.execution_authority import authority_scope
from open_workflow_agent.native_extensions import LANGGRAPH_STATE_REPLAY
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow, generate_default_workflow

pytest.importorskip("langgraph")
from open_workflow_agent_langgraph import LangGraphWorkflowEngine

TERMINAL = {"completed", "faulted", "cancelled"}


def workflow(config, policy):
    source = generate_default_workflow()
    source["document"]["name"] = "state-replay-" + uuid4().hex
    source["document"]["metadata"] = {
        "owa": {
            "nativeExtensions": [
                {"name": "langgraph.state.read", "version": "1.0.0"},
                {"name": "langgraph.state.replay", "version": "1.0.0"},
            ],
            "nativeExtensionData": {
                LANGGRAPH_STATE_REPLAY: {
                    "authorizationPolicyRef": config.execution_authorization.id,
                    "replayEffectPolicy": policy,
                }
            },
        }
    }
    source["do"] = [
        {"first": {"call": "agent:1.0.0@default"}},
        {"second": {"call": "agent:1.0.0@default"}},
    ]
    return compile_workflow(source)


@pytest.fixture(params=["read_only", "reexecute"])
def policy(request):
    return request.param


@pytest.fixture(params=["sqlite", "postgresql"])
async def replay_runtime(request, tmp_path, monkeypatch, policy):
    for actor in ("alice", "bob"):
        monkeypatch.setenv("OWA_NATIVE_STREAM_TEST_" + actor.upper(), "replay-state-" + actor)
    config = configuration()
    config.execution_authorization.policy.rules[0].actions.append("state.replay")
    config.native_state_replay = NativeStateReplayConfig()
    if request.param == "postgresql":
        datasource = os.getenv("OWA_TEST_POSTGRES_URL")
        if not datasource:
            pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
        config.persistence.datasource = datasource
    services = RuntimeServices(
        config,
        model=FakeModel(),
        database_root=None if request.param == "postgresql" else tmp_path,
    )
    engine = LangGraphWorkflowEngine()
    await engine.initialize(services)
    await engine._timer_dispatcher.stop()
    plan = workflow(config, policy)
    await engine.compile(plan)
    handle = services.invocations.create(
        engine="langgraph",
        session_id="shared",
        user_id="shared",
        owner_principal="alice",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    with authority_scope(authority(services, "alice")):
        result = await engine.invoke(plan, handle, {})
    assert result.status == "completed", result.error
    assert len(services.model.calls) == 2
    try:
        yield services, engine, handle, plan
    finally:
        await engine.shutdown()
        services.close()


def client_for(runtime, actor="alice"):
    services, engine, *_ = runtime
    config = services.config.model_copy(deep=True)
    config.server.api_security_profile = "actor-" + actor
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=create_app(config=config, services=services, engine=engine)
        ),
        base_url="http://test",
        headers={"Authorization": "Bearer replay-state-" + actor},
    )


async def first_task_reference(client, handle):
    history = await client.get(f"/v1/invocations/{handle.invocation_id}/states")
    assert history.status_code == 200, history.text
    return next(
        item["reference"]
        for item in history.json()["items"]
        if item.get("root_task_visits") == {"/do/0/first": 1}
    )


async def wait_terminal(services, invocation_id, timeout=60):
    async with asyncio.timeout(timeout):
        while True:
            current = services.invocations.get(invocation_id)
            if current is not None and current.status in TERMINAL:
                return current
            await asyncio.sleep(0.1)


def request_body(reference, policy):
    return {"reference": reference, "acknowledge_reexecution": policy == "reexecute"}


@pytest.mark.asyncio
async def test_replay_creates_a_new_invocation_and_enforces_the_declared_effect_policy(
    replay_runtime, policy
):
    services, engine, handle, plan = replay_runtime
    path = f"/v1/invocations/{handle.invocation_id}/state-replays"
    async with client_for(replay_runtime) as client:
        capabilities = (await client.get("/v1/capabilities")).json()
        assert LANGGRAPH_STATE_REPLAY in capabilities["nativeExtensions"]
        advertised = capabilities["features"]["nativeStateReplay"]
        assert advertised["branch"] is False and advertised["timeTravel"] is False
        reference = await first_task_reference(client, handle)
        response = await client.post(
            path, json=request_body(reference, policy), headers={"Idempotency-Key": "replay-1"}
        )
        assert response.status_code == 202, response.text
        body = response.json()
        target = body["invocation_id"]
        assert target != handle.invocation_id
        assert body["source_invocation_id"] == handle.invocation_id
        assert body["effect_policy"] == policy and body["state"] == "accepted"
        # No native identifiers or internal plan leak through the response.
        assert set(body) == {"invocation_id", "source_invocation_id", "effect_policy", "state"}
        finished = await wait_terminal(services, target)
        expected = "completed" if policy == "reexecute" else "faulted"
        assert finished.status == expected
        assert len(services.model.calls) == (3 if policy == "reexecute" else 2)
        # An identical request returns the same target and never repeats work.
        repeated = await client.post(
            path, json=request_body(reference, policy), headers={"Idempotency-Key": "replay-1"}
        )
        assert repeated.status_code == 202, repeated.text
        assert repeated.json()["invocation_id"] == target
        assert len(services.model.calls) == (3 if policy == "reexecute" else 2)
        # The source invocation is untouched.
        assert services.invocations.get(handle.invocation_id).status == "completed"


@pytest.mark.asyncio
async def test_replay_rejects_mismatched_acknowledgement_and_other_principals(
    replay_runtime, policy
):
    services, engine, handle, plan = replay_runtime
    path = f"/v1/invocations/{handle.invocation_id}/state-replays"
    async with client_for(replay_runtime) as alice, client_for(replay_runtime, "bob") as bob:
        reference = await first_task_reference(alice, handle)
        wrong = {"reference": reference, "acknowledge_reexecution": policy != "reexecute"}
        mismatch = await alice.post(path, json=wrong, headers={"Idempotency-Key": "mismatch"})
        assert mismatch.status_code == 409, mismatch.text
        foreign = await bob.post(
            path, json=request_body(reference, policy), headers={"Idempotency-Key": "foreign"}
        )
        assert foreign.status_code == 404, foreign.text
        missing = await alice.post(path, json=request_body(reference, policy))
        assert missing.status_code == 422, missing.text
        malformed = await alice.post(
            path,
            json={"reference": "not-a-reference", "acknowledge_reexecution": False},
            headers={"Idempotency-Key": "malformed"},
        )
        assert malformed.status_code == 422, malformed.text
        extra = await alice.post(
            path,
            json={**request_body(reference, policy), "unexpected": 1},
            headers={"Idempotency-Key": "extra"},
        )
        assert extra.status_code == 422, extra.text
        query = await alice.post(
            path + "?debug=1",
            json=request_body(reference, policy),
            headers={"Idempotency-Key": "query"},
        )
        assert query.status_code == 422, query.text
    # No rejected request created a replay target or ran a model call.
    assert len(services.model.calls) == 2


@pytest.mark.asyncio
async def test_concurrent_requests_with_one_key_resolve_to_one_target_without_duplicate_work(
    replay_runtime, policy
):
    services, engine, handle, plan = replay_runtime
    path = f"/v1/invocations/{handle.invocation_id}/state-replays"
    async with client_for(replay_runtime) as client:
        reference = await first_task_reference(client, handle)
        responses = await asyncio.gather(
            *(
                client.post(
                    path,
                    json=request_body(reference, policy),
                    headers={"Idempotency-Key": "same-key"},
                )
                for _ in range(3)
            )
        )
        assert [item.status_code for item in responses] == [202, 202, 202], [
            item.text for item in responses
        ]
        targets = {item.json()["invocation_id"] for item in responses}
        assert len(targets) == 1
        await wait_terminal(services, targets.pop())
        assert len(services.model.calls) == (3 if policy == "reexecute" else 2)


@pytest.mark.asyncio
async def test_active_replay_budget_rejects_the_next_distinct_request(replay_runtime, policy):
    services, engine, handle, plan = replay_runtime
    controller = services.native_replay_controller
    release = asyncio.Event()
    real = controller.coordinator.run

    async def held(*args, **kwargs):
        kwargs["_phase"]("reserved", "00000000-0000-4000-8000-000000000000")
        kwargs["_phase"]("bound", "00000000-0000-4000-8000-000000000000")
        await release.wait()

    controller.coordinator.run = held
    path = f"/v1/invocations/{handle.invocation_id}/state-replays"
    try:
        async with client_for(replay_runtime) as client:
            reference = await first_task_reference(client, handle)
            budget = services.config.native_state_replay.max_active_replays
            for number in range(budget):
                accepted = await client.post(
                    path,
                    json=request_body(reference, policy),
                    headers={"Idempotency-Key": f"held-{number}"},
                )
                assert accepted.status_code == 202, accepted.text
            exhausted = await client.post(
                path,
                json=request_body(reference, policy),
                headers={"Idempotency-Key": "one-too-many"},
            )
            assert exhausted.status_code == 429, exhausted.text
            # A repeat of an already admitted request is not a new admission.
            repeat = await client.post(
                path,
                json=request_body(reference, policy),
                headers={"Idempotency-Key": "held-0"},
            )
            assert repeat.status_code == 202, repeat.text
    finally:
        release.set()
        controller.coordinator.run = real
        await asyncio.sleep(0.2)


@pytest.mark.asyncio
async def test_a_different_request_under_a_used_key_is_rejected(replay_runtime, policy):
    services, engine, handle, plan = replay_runtime
    path = f"/v1/invocations/{handle.invocation_id}/state-replays"
    async with client_for(replay_runtime) as client:
        history = await client.get(f"/v1/invocations/{handle.invocation_id}/states")
        references = [item["reference"] for item in history.json()["items"]]
        reference = await first_task_reference(client, handle)
        other = next(item for item in references if item != reference)
        first = await client.post(
            path, json=request_body(reference, policy), headers={"Idempotency-Key": "reused"}
        )
        assert first.status_code == 202, first.text
        await wait_terminal(services, first.json()["invocation_id"])
        calls = len(services.model.calls)
        changed = await client.post(
            path, json=request_body(other, policy), headers={"Idempotency-Key": "reused"}
        )
        assert 400 <= changed.status_code < 500, changed.text
        assert changed.status_code != 202
        assert len(services.model.calls) == calls
        assert first.json()["invocation_id"] not in changed.text
