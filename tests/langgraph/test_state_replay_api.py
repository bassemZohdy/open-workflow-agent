"""Configured HTTP replay admits a root checkpoint into a new, lineage-bound invocation."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import yaml
from native_state_test_support import authority, configuration
from open_workflow_agent.api import create_app
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import NativeStateReplayConfig, RuntimeConfig
from open_workflow_agent.execution_authority import authority_scope
from open_workflow_agent.native_extensions import LANGGRAPH_STATE_REPLAY
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow, generate_default_workflow

pytest.importorskip("langgraph.graph")
from open_workflow_agent_langgraph import LangGraphWorkflowEngine

TERMINAL = {"completed", "faulted", "cancelled"}


def workflow(config, policy, tasks=2):
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
    names = ["first", "second", "third", "fourth"][:tasks]
    source["do"] = [{name: {"call": "agent:1.0.0@default"}} for name in names]
    return compile_workflow(source)


@pytest.fixture(params=["read_only", "reexecute"])
def policy(request):
    return request.param


@pytest.fixture
def replay_config(request):
    return getattr(request, "param", NativeStateReplayConfig())


@pytest.fixture
def task_count(request):
    return getattr(request, "param", 2)


@pytest.fixture(params=["sqlite", "postgresql"])
async def replay_runtime(request, tmp_path, monkeypatch, policy, replay_config, task_count):
    for actor in ("alice", "bob"):
        monkeypatch.setenv("OWA_NATIVE_STREAM_TEST_" + actor.upper(), "replay-state-" + actor)
    config = configuration()
    config.execution_authorization.policy.rules[0].actions.extend(
        ["state.replay", "invocations.cancel"]
    )
    config.native_state_replay = replay_config
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
    plan = workflow(config, policy, task_count)
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
    assert len(services.model.calls) == task_count
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
@pytest.mark.parametrize("bound_first", [False, True])
async def test_inflight_key_rejects_a_different_checkpoint(replay_runtime, policy, bound_first):
    services, _, handle, _ = replay_runtime
    controller = services.native_replay_controller
    entered, release = asyncio.Event(), asyncio.Event()
    real = controller.coordinator.run
    target = "00000000-0000-4000-8000-000000000000"

    async def held(*args, **kwargs):
        if bound_first:
            kwargs["_phase"]("bound", target)
        entered.set()
        await release.wait()
        if not bound_first:
            kwargs["_phase"]("bound", target)

    controller.coordinator.run = held
    first = None
    path = f"/v1/invocations/{handle.invocation_id}/state-replays"
    try:
        async with client_for(replay_runtime) as client:
            reference = "1" * 64
            first = asyncio.create_task(
                client.post(
                    path,
                    json=request_body(reference, policy),
                    headers={"Idempotency-Key": "inflight-conflict"},
                )
            )
            await asyncio.wait_for(entered.wait(), timeout=10)
            changed = await asyncio.wait_for(
                client.post(
                    path,
                    json=request_body("0" * 64, policy),
                    headers={"Idempotency-Key": "inflight-conflict"},
                ),
                timeout=5,
            )
            assert changed.status_code == 409, changed.text
            assert target not in changed.text
            release.set()
            assert (await first).status_code == 202
    finally:
        release.set()
        if first is not None:
            await asyncio.gather(first, return_exceptions=True)
        controller.coordinator.run = real


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


TIGHT = [NativeStateReplayConfig(max_replays_per_source=1)]


@pytest.mark.asyncio
@pytest.mark.parametrize("replay_config", TIGHT, indirect=True)
async def test_per_source_limit_rejects_a_new_replay_but_not_a_repeat(replay_runtime, policy):
    services, engine, handle, plan = replay_runtime
    path = f"/v1/invocations/{handle.invocation_id}/state-replays"
    async with client_for(replay_runtime) as client:
        reference = await first_task_reference(client, handle)
        first = await client.post(
            path, json=request_body(reference, policy), headers={"Idempotency-Key": "one"}
        )
        assert first.status_code == 202, first.text
        await wait_terminal(services, first.json()["invocation_id"])
        calls = len(services.model.calls)
        second = await client.post(
            path, json=request_body(reference, policy), headers={"Idempotency-Key": "two"}
        )
        assert second.status_code == 429, second.text
        # The admitted request is not a new admission, so the limit never blocks its repeat.
        repeat = await client.post(
            path, json=request_body(reference, policy), headers={"Idempotency-Key": "one"}
        )
        assert repeat.status_code == 202, repeat.text
        assert repeat.json()["invocation_id"] == first.json()["invocation_id"]
        assert len(services.model.calls) == calls


@pytest.mark.asyncio
async def test_a_replay_target_cannot_itself_be_replayed(replay_runtime, policy):
    services, engine, handle, plan = replay_runtime
    async with client_for(replay_runtime) as client:
        reference = await first_task_reference(client, handle)
        first = await client.post(
            f"/v1/invocations/{handle.invocation_id}/state-replays",
            json=request_body(reference, policy),
            headers={"Idempotency-Key": "depth-1"},
        )
        assert first.status_code == 202, first.text
        target = first.json()["invocation_id"]
        await wait_terminal(services, target)
        calls = len(services.model.calls)
        # Any well-formed reference is refused up front: the depth rule is checked before
        # any preflight work, so no target state is read or created.
        again = await client.post(
            f"/v1/invocations/{target}/state-replays",
            json=request_body(reference, policy),
            headers={"Idempotency-Key": "depth-2"},
        )
        assert again.status_code == 409, again.text
        assert len(services.model.calls) == calls


@pytest.mark.asyncio
@pytest.mark.parametrize("replay_config", TIGHT, indirect=True)
async def test_per_source_limit_holds_under_concurrent_distinct_requests(replay_runtime, policy):
    services, engine, handle, plan = replay_runtime
    path = f"/v1/invocations/{handle.invocation_id}/state-replays"
    async with client_for(replay_runtime) as client:
        reference = await first_task_reference(client, handle)
        responses = await asyncio.gather(
            *(
                client.post(
                    path,
                    json=request_body(reference, policy),
                    headers={"Idempotency-Key": f"race-{number}"},
                )
                for number in range(2)
            )
        )
        statuses = sorted(item.status_code for item in responses)
        assert statuses == [202, 429], [item.text for item in responses]
        admitted = next(item for item in responses if item.status_code == 202)
        await wait_terminal(services, admitted.json()["invocation_id"])
        # Only the admitted request ran any work.
        assert len(services.model.calls) == (3 if policy == "reexecute" else 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_configuration_only_app_invokes_and_replays_with_public_requests(
    tmp_path, monkeypatch, backend
):
    monkeypatch.setenv("OWA_NATIVE_STATE_TOKEN", "controlled-state-owner")
    examples = Path(__file__).resolve().parents[2] / "examples"
    config = RuntimeConfig.model_validate(
        yaml.safe_load((examples / "native-state-replay.yaml").read_text(encoding="utf-8"))
    )
    if backend == "postgresql":
        datasource = os.getenv("OWA_TEST_POSTGRES_URL")
        if not datasource:
            pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
        config.persistence.datasource = datasource
    mounted = tmp_path / "native-state-replay-workflow.yaml"
    mounted.write_bytes((examples / mounted.name).read_bytes())
    config.workflow.path = str(mounted)
    config.knowledge.reload.mode = "manual"
    services = RuntimeServices(
        config, model=FakeModel(), database_root=tmp_path if backend == "sqlite" else None
    )
    engine = LangGraphWorkflowEngine()
    app = create_app(config=config, services=services, engine=engine)
    try:
        async with app.router.lifespan_context(app):
            await engine._timer_dispatcher.stop()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer controlled-state-owner"},
            ) as client:
                created = await client.post("/v1/invoke", json={"input": {}})
                assert created.status_code == 200, created.text
                assert created.json()["status"] == "completed", created.text
                source = created.json()["invocation_id"]
                assert len(services.model.calls) == 2
                capabilities = (await client.get("/v1/capabilities")).json()
                assert capabilities["features"]["nativeStateReplay"]["branch"] is False
                history = await client.get(f"/v1/invocations/{source}/states")
                assert history.status_code == 200, history.text
                reference = next(
                    item["reference"]
                    for item in history.json()["items"]
                    if item.get("root_task_visits") == {"/do/0/first": 1}
                )
                replayed = await client.post(
                    f"/v1/invocations/{source}/state-replays",
                    json={"reference": reference, "acknowledge_reexecution": True},
                    headers={"Idempotency-Key": "config-only"},
                )
                assert replayed.status_code == 202, replayed.text
                target = replayed.json()["invocation_id"]
                assert target != source
                finished = await wait_terminal(services, target)
                assert finished.status == "completed"
                # Re-execution of the remaining task ran exactly one more model call.
                assert len(services.model.calls) == 3
        assert services.native_replay_controller is None
    finally:
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["reserved", "admitted", "forked", "bound", "effect"])
async def test_a_process_stop_at_each_stage_recovers_through_the_same_request(
    replay_runtime, policy, phase, tmp_path
):
    if policy == "read_only" and phase != "bound":
        pytest.skip("read-only interruption is exercised at the bound boundary")
    if phase == "effect" and policy != "reexecute":
        pytest.skip("an effect is only reached when re-execution is declared")
    services, engine, handle, plan = replay_runtime
    postgres = str(services.config.persistence.datasource).startswith("postgres")
    async with client_for(replay_runtime) as client:
        reference = await first_task_reference(client, handle)
    environment = os.environ.copy()
    if not postgres:
        environment.pop("OWA_TEST_POSTGRES_URL", None)
    worker = Path(__file__).with_name("native_replay_route_worker.py")
    process = subprocess.Popen(
        [
            sys.executable,
            str(worker),
            str(tmp_path),
            handle.invocation_id,
            "crash-key",
            reference,
            policy,
            phase,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=environment,
    )
    output = []
    try:

        async def read_phase():
            while True:
                line = await asyncio.to_thread(process.stdout.readline)
                if not line:
                    raise AssertionError("worker exited before the stage: " + "".join(output))
                output.append(line)
                if line.startswith("CRASH_PHASE "):
                    return line.strip().split()

        marker = await asyncio.wait_for(read_phase(), timeout=120)
        assert marker[1] == phase
        process.kill()
        await asyncio.to_thread(process.wait, timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
            await asyncio.to_thread(process.wait, timeout=10)
        process.stdout.close()
    # Let the dead worker's execution lease lapse, then repeat the identical request.
    await asyncio.sleep(13 if phase == "effect" else 3)
    expected = "faulted" if policy == "read_only" or phase == "effect" else "completed"
    expected_calls = 2 if expected == "faulted" else 3
    async with client_for(replay_runtime) as client:
        path = f"/v1/invocations/{handle.invocation_id}/state-replays"
        body = request_body(reference, policy)
        recovered = await client.post(path, json=body, headers={"Idempotency-Key": "crash-key"})
        assert recovered.status_code == 202, recovered.text
        target = recovered.json()["invocation_id"]
        assert target == marker[2]
        finished = await wait_terminal(services, target)
        assert finished.status == expected
        # The interrupted stage never repeats an effect it may already have performed.
        assert len(services.model.calls) == expected_calls
        repeated = await client.post(path, json=body, headers={"Idempotency-Key": "crash-key"})
        assert repeated.status_code == 202, repeated.text
        assert repeated.json()["invocation_id"] == target
        assert len(services.model.calls) == expected_calls


@pytest.mark.asyncio
async def test_a_running_replay_target_obeys_cancellation_and_never_resumes_late(replay_runtime):
    services, engine, handle, plan = replay_runtime
    if plan.native_state_replay.effect_policy != "reexecute":
        pytest.skip("a running continuation needs re-execution of an effect")
    entered = asyncio.Event()
    release = asyncio.Event()

    async def held(_prompt):
        entered.set()
        await release.wait()
        return {"response": "released"}

    path = f"/v1/invocations/{handle.invocation_id}/state-replays"
    async with client_for(replay_runtime) as client:
        reference = await first_task_reference(client, handle)
        calls = len(services.model.calls)
        services.model.response = held
        body = request_body(reference, "reexecute")
        accepted = await client.post(path, json=body, headers={"Idempotency-Key": "cancel-me"})
        assert accepted.status_code == 202, accepted.text
        target = accepted.json()["invocation_id"]
        async with asyncio.timeout(30):
            await entered.wait()
        assert services.invocations.get(target).status not in TERMINAL
        cancelled = await client.post(f"/v1/invocations/{target}/cancel")
        assert cancelled.status_code == 200, cancelled.text
        release.set()
        await asyncio.sleep(1)
        assert services.invocations.get(target).status == "cancelled"
        assert len(services.model.calls) == calls + 1
        # Repeating the replay request neither resurrects the target nor runs any work.
        repeat = await client.post(path, json=body, headers={"Idempotency-Key": "cancel-me"})
        assert repeat.status_code == 202, repeat.text
        assert repeat.json()["invocation_id"] == target
        await asyncio.sleep(1)
        assert services.invocations.get(target).status == "cancelled"
        assert len(services.model.calls) == calls + 1


@pytest.mark.asyncio
async def test_a_changed_original_policy_refuses_replay_before_creating_anything(
    replay_runtime, policy
):
    services, engine, handle, plan = replay_runtime
    path = f"/v1/invocations/{handle.invocation_id}/state-replays"
    async with client_for(replay_runtime) as client:
        reference = await first_task_reference(client, handle)
        calls = len(services.model.calls)
        before = {
            row[0]
            for row in services.invocations.connection.execute(
                "SELECT invocation_id FROM invocations"
            ).fetchall()
        }
        # Any change to the execution policy invalidates the authority the source ran under.
        services.config.execution_authorization.policy.rules[0].actions.append("extra.action")
        refused = await client.post(
            path, json=request_body(reference, policy), headers={"Idempotency-Key": "revoked"}
        )
        assert 400 <= refused.status_code < 500, refused.text
        assert refused.status_code != 202
        after = {
            row[0]
            for row in services.invocations.connection.execute(
                "SELECT invocation_id FROM invocations"
            ).fetchall()
        }
        assert after == before
        assert len(services.model.calls) == calls


@pytest.mark.asyncio
async def test_graceful_shutdown_mid_continuation_leaves_a_recoverable_target(
    replay_runtime, tmp_path
):
    services, engine, handle, plan = replay_runtime
    if plan.native_state_replay.effect_policy != "reexecute":
        pytest.skip("a running continuation needs re-execution of an effect")
    entered = asyncio.Event()
    release = asyncio.Event()

    async def held(_prompt):
        entered.set()
        await release.wait()
        return {"response": "released"}

    path = f"/v1/invocations/{handle.invocation_id}/state-replays"
    async with client_for(replay_runtime) as client:
        reference = await first_task_reference(client, handle)
        services.model.response = held
        body = request_body(reference, "reexecute")
        accepted = await client.post(path, json=body, headers={"Idempotency-Key": "shutdown"})
        assert accepted.status_code == 202, accepted.text
        target = accepted.json()["invocation_id"]
        async with asyncio.timeout(30):
            await entered.wait()
    calls = len(services.model.calls)
    status_before = services.invocations.get(target).status
    await engine.shutdown()
    release.set()
    await asyncio.sleep(0.5)
    assert status_before == "running"
    assert services.invocations.get(target).status == "running"
    assert services.native_replay_controller is None
    assert len(services.model.calls) == calls
    # A restarted runtime on the same database recovers the target through the same request.
    postgres = str(services.config.persistence.datasource).startswith("postgres")
    restarted = RuntimeServices(
        services.config.model_copy(deep=True),
        model=FakeModel(),
        database_root=None if postgres else tmp_path,
    )
    second = LangGraphWorkflowEngine()
    await second.initialize(restarted)
    await second._timer_dispatcher.stop()
    try:
        async with client_for((restarted, second)) as client:
            async with asyncio.timeout(90):
                while True:
                    recovered = await client.post(
                        path, json=body, headers={"Idempotency-Key": "shutdown"}
                    )
                    if recovered.status_code == 202:
                        break
                    await asyncio.sleep(2)
            assert recovered.json()["invocation_id"] == target
            finished = await wait_terminal(restarted, target)
            # The interrupted effect may already have happened, so it is never repeated.
            assert finished.status == "faulted"
            assert len(restarted.model.calls) == 0
    finally:
        await second.shutdown()
        restarted.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("task_count", [3], indirect=True)
async def test_revocation_during_a_running_replay_refuses_the_next_effect(replay_runtime):
    services, engine, handle, plan = replay_runtime
    if plan.native_state_replay.effect_policy != "reexecute":
        pytest.skip("a running continuation needs re-execution of an effect")
    entered = asyncio.Event()
    release = asyncio.Event()

    async def held(_prompt):
        entered.set()
        await release.wait()
        return {"response": "released"}

    path = f"/v1/invocations/{handle.invocation_id}/state-replays"
    async with client_for(replay_runtime) as client:
        reference = await first_task_reference(client, handle)
        calls = len(services.model.calls)
        services.model.response = held
        accepted = await client.post(
            path, json=request_body(reference, "reexecute"), headers={"Idempotency-Key": "revoke"}
        )
        assert accepted.status_code == 202, accepted.text
        target = accepted.json()["invocation_id"]
        async with asyncio.timeout(30):
            await entered.wait()
        # Revoke while the second task's call is in flight, then let it finish.
        services.config.execution_authorization.policy.rules[0].actions.append("extra.action")
        release.set()
        finished = await wait_terminal(services, target)
        # The in-flight call may already have happened, but the policy change fails the
        # target closed at that task and no later effect runs.
        assert finished.status == "faulted"
        assert finished.error["code"] == "execution_authorization_denied"
        assert finished.error["details"]["reason"] == "execution_policy_changed"
        assert finished.error["details"]["instance"] == "/do/1/second"
        assert len(services.model.calls) == calls + 1
        assert services.invocations.get(handle.invocation_id).status == "completed"
