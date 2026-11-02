"""Opt-in original-owner branch API over the native lineage coordinator."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from native_state_test_support import authority, configuration
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import NativeStateBranchConfig, NativeStateUpdateConfig
from open_workflow_agent.execution_authority import authority_scope
from open_workflow_agent.native_extensions import LANGGRAPH_STATE_BRANCH
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow, generate_default_workflow
from test_state_replay_api import client_for, wait_terminal

pytest.importorskip("langgraph.graph")
from open_workflow_agent_langgraph import LangGraphWorkflowEngine


@pytest.fixture
async def branch_runtime(request, tmp_path, monkeypatch):
    backend, policy, *options = request.param
    for actor in ("alice", "bob"):
        monkeypatch.setenv("OWA_NATIVE_STREAM_TEST_" + actor.upper(), "replay-state-" + actor)
    config = configuration(timeout_seconds=30)
    config.execution_authorization.policy.rules[0].actions.extend(
        ["state.branch", "state.update", "invocations.cancel"]
    )
    config.native_state_updates = NativeStateUpdateConfig()
    config.native_state_branch = NativeStateBranchConfig(**(options[0] if options else {}))
    if backend == "postgresql":
        datasource = os.getenv("OWA_TEST_POSTGRES_URL")
        if not datasource:
            pytest.skip("PostgreSQL datasource required")
        config.persistence.datasource = datasource
    services = RuntimeServices(
        config, model=FakeModel(), database_root=None if backend == "postgresql" else tmp_path
    )
    engine = LangGraphWorkflowEngine()
    await engine.initialize(services)
    await engine._timer_dispatcher.stop()
    source = generate_default_workflow()
    source["document"]["name"] = "state-branch-" + uuid4().hex
    source["do"] = [
        {"first": {"set": {"value": 1}}},
        {"second": {"set": {"value": "${ .value + 1 }"}}},
        {"third": {"call": "agent:1.0.0@default"}},
    ]
    source["document"]["metadata"] = {
        "owa": {
            "nativeExtensions": [
                {"name": "langgraph.state.read", "version": "1.0.0"},
                {"name": "langgraph.state.update", "version": "1.0.0"},
                {"name": "langgraph.state.branch", "version": "1.0.0"},
            ],
            "nativeExtensionData": {
                "langgraph.state.update@1.0.0": {
                    "allowedTaskRefs": ["/do/0", "/do/1"],
                    "updateSchema": {
                        "type": "object",
                        "properties": {"value": {"type": "integer"}},
                        "required": ["value"],
                        "additionalProperties": False,
                    },
                    "authorizationPolicyRef": config.execution_authorization.id,
                },
                LANGGRAPH_STATE_BRANCH: {
                    "authorizationPolicyRef": config.execution_authorization.id,
                    "branchEffectPolicy": policy,
                },
            },
        }
    }
    plan = compile_workflow(source)
    await engine.compile(plan)
    handle = services.invocations.create(
        engine="langgraph",
        session_id=None,
        user_id=None,
        owner_principal="alice",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    with authority_scope(authority(services, "alice")):
        result = await engine.invoke(plan, handle, {})
    assert result.status == "completed", result.error
    try:
        yield services, engine, handle, plan, policy
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "branch_runtime",
    [
        ("sqlite", "read_only"),
        ("sqlite", "reexecute"),
        ("postgresql", "read_only"),
        ("postgresql", "reexecute"),
    ],
    indirect=True,
)
async def test_branch_api_enforces_authority_idempotency_and_effect_policy(branch_runtime):
    services, engine, handle, _, policy = branch_runtime
    endpoint = f"/v1/invocations/{handle.invocation_id}/state-branches"
    async with client_for(branch_runtime) as alice, client_for(branch_runtime, "bob") as bob:
        capabilities = (await alice.get("/v1/capabilities")).json()
        assert LANGGRAPH_STATE_BRANCH in capabilities["nativeExtensions"]
        assert capabilities["features"]["nativeStateBranch"]["scope"] == "root"
        history = await alice.get(f"/v1/invocations/{handle.invocation_id}/states")
        assert history.status_code == 200, history.text
        reference = next(
            item["reference"]
            for item in history.json()["items"]
            if item.get("root_task_visits") == {"/do/0/first": 1, "/do/1/second": 1}
        )
        body = {
            "reference": reference,
            "task_reference": "/do/1/second",
            "values": {"value": 7},
            "acknowledge_reexecution": policy == "reexecute",
        }
        mismatch = await alice.post(
            endpoint,
            json=body | {"acknowledge_reexecution": policy != "reexecute"},
            headers={"Idempotency-Key": "mismatch"},
        )
        assert mismatch.status_code == 409, mismatch.text
        assert (await alice.post(endpoint, json=body)).status_code == 422
        assert (
            await alice.post(
                endpoint + "?unexpected=1", json=body, headers={"Idempotency-Key": "query"}
            )
        ).status_code == 422
        foreign = await bob.post(endpoint, json=body, headers={"Idempotency-Key": "branch"})
        assert foreign.status_code == 404, foreign.text
        invalid = await alice.post(
            endpoint,
            json=body | {"values": {"value": "bad"}},
            headers={"Idempotency-Key": "invalid"},
        )
        assert 400 <= invalid.status_code < 500, invalid.text
        earlier = await alice.post(
            endpoint,
            json=body | {"task_reference": "/do/0/first"},
            headers={"Idempotency-Key": "earlier"},
        )
        assert 400 <= earlier.status_code < 500, earlier.text
        responses = await asyncio.gather(
            *(
                alice.post(endpoint, json=body, headers={"Idempotency-Key": "branch"})
                for _ in range(2)
            )
        )
        assert [response.status_code for response in responses] == [202, 202], [
            r.text for r in responses
        ]
        assert responses[0].json() == responses[1].json()
        target = responses[0].json()["invocation_id"]
        assert target != handle.invocation_id
        assert set(responses[0].json()) == {
            "invocation_id",
            "source_invocation_id",
            "effect_policy",
            "state",
        }
        finished = await wait_terminal(services, target)
        assert finished.status == ("completed" if policy == "reexecute" else "faulted")
        calls = len(services.model.calls)
        assert calls == (2 if policy == "reexecute" else 1)
        repeat = await alice.post(endpoint, json=body, headers={"Idempotency-Key": "branch"})
        assert repeat.status_code == 202 and repeat.json()["invocation_id"] == target
        changed = await alice.post(
            endpoint, json=body | {"values": {"value": 8}}, headers={"Idempotency-Key": "branch"}
        )
        assert changed.status_code != 202 and 400 <= changed.status_code < 500
        assert len(services.model.calls) == calls
        assert services.invocations.get(handle.invocation_id).status == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "branch_runtime",
    [
        ("sqlite", "read_only"),
        ("sqlite", "reexecute"),
        ("postgresql", "read_only"),
        ("postgresql", "reexecute"),
    ],
    indirect=True,
)
@pytest.mark.parametrize("bound_first", [False, True])
async def test_inflight_branch_key_rejects_changed_data(branch_runtime, bound_first):
    services, _, handle, _, policy = branch_runtime
    controller = services.native_branch_controller
    entered, release = asyncio.Event(), asyncio.Event()
    real = controller.coordinator.run_branch
    target = "00000000-0000-4000-8000-000000000000"

    async def held(*args, **kwargs):
        if bound_first:
            kwargs["_phase"]("bound", target)
        entered.set()
        await release.wait()
        if not bound_first:
            kwargs["_phase"]("bound", target)

    controller.coordinator.run_branch = held
    first = None
    endpoint = f"/v1/invocations/{handle.invocation_id}/state-branches"
    body = {
        "reference": "1" * 64,
        "task_reference": "/do/1/second",
        "values": {"value": 7},
        "acknowledge_reexecution": policy == "reexecute",
    }
    try:
        async with client_for(branch_runtime) as client:
            first = asyncio.create_task(
                client.post(endpoint, json=body, headers={"Idempotency-Key": "inflight"})
            )
            await asyncio.wait_for(entered.wait(), timeout=10)
            changed = await asyncio.wait_for(
                client.post(
                    endpoint,
                    json=body | {"values": {"value": 8}},
                    headers={"Idempotency-Key": "inflight"},
                ),
                timeout=30,
            )
            assert changed.status_code == 409, changed.text
            assert target not in changed.text
            release.set()
            assert (await first).status_code == 202
    finally:
        release.set()
        if first is not None:
            await asyncio.gather(first, return_exceptions=True)
        controller.coordinator.run_branch = real


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "branch_runtime",
    [
        ("sqlite", "reexecute"),
        ("postgresql", "reexecute"),
    ],
    indirect=True,
)
async def test_running_branch_obeys_cancellation_without_late_work(branch_runtime):
    services, _, handle, _, _ = branch_runtime
    entered, release = asyncio.Event(), asyncio.Event()

    async def held(_prompt):
        entered.set()
        await release.wait()
        return {"response": "released"}

    endpoint = f"/v1/invocations/{handle.invocation_id}/state-branches"
    async with client_for(branch_runtime) as client:
        history = await client.get(f"/v1/invocations/{handle.invocation_id}/states")
        assert history.status_code == 200, history.text
        reference = next(
            item["reference"]
            for item in history.json()["items"]
            if item.get("root_task_visits") == {"/do/0/first": 1, "/do/1/second": 1}
        )
        body = {
            "reference": reference,
            "task_reference": "/do/1/second",
            "values": {"value": 7},
            "acknowledge_reexecution": True,
        }
        calls = len(services.model.calls)
        services.model.response = held
        try:
            accepted = await client.post(endpoint, json=body, headers={"Idempotency-Key": "cancel"})
            assert accepted.status_code == 202, accepted.text
            target = accepted.json()["invocation_id"]
            await asyncio.wait_for(entered.wait(), timeout=30)
            cancelled = await client.post(f"/v1/invocations/{target}/cancel")
            assert cancelled.status_code == 200, cancelled.text
            release.set()
            await asyncio.sleep(1)
            assert services.invocations.get(target).status == "cancelled"
            repeat = await client.post(endpoint, json=body, headers={"Idempotency-Key": "cancel"})
            assert repeat.status_code == 202 and repeat.json()["invocation_id"] == target
            assert services.invocations.get(target).status == "cancelled"
            assert len(services.model.calls) == calls + 1
        finally:
            release.set()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "branch_runtime",
    [
        ("sqlite", "read_only", {"max_branches_per_source": 1}),
        ("postgresql", "read_only", {"max_branches_per_source": 1}),
    ],
    indirect=True,
)
async def test_per_source_budget_and_lineage_depth_are_enforced(branch_runtime):
    services, _, handle, _, _ = branch_runtime
    endpoint = f"/v1/invocations/{handle.invocation_id}/state-branches"
    async with client_for(branch_runtime) as client:
        history = await client.get(f"/v1/invocations/{handle.invocation_id}/states")
        assert history.status_code == 200, history.text
        reference = next(
            item["reference"]
            for item in history.json()["items"]
            if item.get("root_task_visits") == {"/do/0/first": 1, "/do/1/second": 1}
        )
        body = {"reference": reference, "task_reference": "/do/1/second", "values": {"value": 7}}
        first = await client.post(endpoint, json=body, headers={"Idempotency-Key": "one"})
        assert first.status_code == 202, first.text
        target = first.json()["invocation_id"]
        await wait_terminal(services, target)
        limited = await client.post(endpoint, json=body, headers={"Idempotency-Key": "two"})
        assert limited.status_code == 429, limited.text
        repeat = await client.post(endpoint, json=body, headers={"Idempotency-Key": "one"})
        assert repeat.status_code == 202 and repeat.json()["invocation_id"] == target
        child = await client.post(
            f"/v1/invocations/{target}/state-branches",
            json=body,
            headers={"Idempotency-Key": "child"},
        )
        assert child.status_code == 409, child.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "branch_runtime",
    [
        ("sqlite", "read_only", {"max_branches_per_source": 1}),
        ("postgresql", "read_only", {"max_branches_per_source": 1}),
    ],
    indirect=True,
)
async def test_concurrent_distinct_requests_cannot_exceed_source_budget(branch_runtime):
    services, _, handle, _, _ = branch_runtime
    endpoint = f"/v1/invocations/{handle.invocation_id}/state-branches"
    async with client_for(branch_runtime) as client:
        history = await client.get(f"/v1/invocations/{handle.invocation_id}/states")
        assert history.status_code == 200, history.text
        reference = next(
            item["reference"]
            for item in history.json()["items"]
            if item.get("root_task_visits") == {"/do/0/first": 1, "/do/1/second": 1}
        )
        body = {"reference": reference, "task_reference": "/do/1/second", "values": {"value": 7}}
        responses = await asyncio.gather(
            *(
                client.post(endpoint, json=body, headers={"Idempotency-Key": key})
                for key in ("a", "b")
            )
        )
        assert sorted(item.status_code for item in responses) == [202, 429], [
            r.text for r in responses
        ]
        await wait_terminal(
            services,
            next(item.json()["invocation_id"] for item in responses if item.status_code == 202),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "branch_runtime",
    [
        ("sqlite", "read_only", {"max_active_branches": 1}),
        ("postgresql", "read_only", {"max_active_branches": 1}),
    ],
    indirect=True,
)
async def test_active_budget_rejects_distinct_requests_but_allows_repeat(branch_runtime):
    services, _, handle, _, _ = branch_runtime
    controller = services.native_branch_controller
    release = asyncio.Event()
    real = controller.coordinator.run_branch
    target = "00000000-0000-4000-8000-000000000000"

    async def held(*args, **kwargs):
        kwargs["_phase"]("bound", target)
        await release.wait()

    controller.coordinator.run_branch = held
    endpoint = f"/v1/invocations/{handle.invocation_id}/state-branches"
    body = {"reference": "1" * 64, "task_reference": "/do/1/second", "values": {"value": 7}}
    try:
        async with client_for(branch_runtime) as client:
            first = await client.post(endpoint, json=body, headers={"Idempotency-Key": "one"})
            assert first.status_code == 202, first.text
            limited = await client.post(endpoint, json=body, headers={"Idempotency-Key": "two"})
            assert limited.status_code == 429, limited.text
            repeat = await client.post(endpoint, json=body, headers={"Idempotency-Key": "one"})
            assert repeat.status_code == 202 and repeat.json()["invocation_id"] == target
    finally:
        release.set()
        controller.coordinator.run_branch = real
        await asyncio.sleep(0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "branch_runtime",
    [
        ("sqlite", "read_only"),
        ("postgresql", "read_only"),
    ],
    indirect=True,
)
async def test_changed_authority_or_branch_installation_refuses_new_work(branch_runtime):
    services, _, handle, _, _ = branch_runtime
    endpoint = f"/v1/invocations/{handle.invocation_id}/state-branches"
    body = {"reference": "1" * 64, "task_reference": "/do/1/second", "values": {"value": 7}}
    before = {
        row[0]
        for row in services.invocations.connection.execute(
            "SELECT invocation_id FROM invocations"
        ).fetchall()
    }
    actions = services.config.execution_authorization.policy.rules[0].actions
    branch = services.config.native_state_branch
    async with client_for(branch_runtime) as client:
        try:
            actions.append("changed.action")
            denied = await client.post(endpoint, json=body, headers={"Idempotency-Key": "revoked"})
            assert 400 <= denied.status_code < 500 and denied.status_code != 202
        finally:
            actions.remove("changed.action")
        try:
            branch.max_branches_per_source += 1
            changed = await client.post(endpoint, json=body, headers={"Idempotency-Key": "changed"})
            assert changed.status_code != 202
        finally:
            branch.max_branches_per_source -= 1
    after = {
        row[0]
        for row in services.invocations.connection.execute(
            "SELECT invocation_id FROM invocations"
        ).fetchall()
    }
    assert after == before


_STOPS = [
    (backend, policy, {}, phase)
    for backend in ("sqlite", "postgresql")
    for policy, phase in [
        ("reexecute", "reserved"),
        ("reexecute", "admitted"),
        ("reexecute", "forked"),
        ("reexecute", "bound"),
        ("reexecute", "effect"),
        ("read_only", "bound"),
    ]
]


@pytest.mark.asyncio
@pytest.mark.parametrize("branch_runtime", _STOPS, indirect=True)
async def test_public_branch_recovers_after_process_stop(branch_runtime, request, tmp_path):
    services, _, handle, _, policy = branch_runtime
    backend, _, _, phase = request.node.callspec.params["branch_runtime"]
    async with client_for(branch_runtime) as client:
        history = await client.get(f"/v1/invocations/{handle.invocation_id}/states")
        assert history.status_code == 200, history.text
        reference = next(
            item["reference"]
            for item in history.json()["items"]
            if item.get("root_task_visits") == {"/do/0/first": 1, "/do/1/second": 1}
        )
    environment = os.environ.copy()
    if backend == "sqlite":
        environment.pop("OWA_TEST_POSTGRES_URL", None)
    process = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).with_name("native_branch_route_worker.py")),
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
                    raise AssertionError("worker exited before stop: " + "".join(output))
                output.append(line)
                if line.startswith("BRANCH_ROUTE_PHASE "):
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
    await asyncio.sleep(21 if phase == "effect" else 13)
    before = len(services.model.calls)
    body = {
        "reference": reference,
        "task_reference": "/do/1/second",
        "values": {"value": 7},
        "acknowledge_reexecution": policy == "reexecute",
    }
    endpoint = f"/v1/invocations/{handle.invocation_id}/state-branches"
    async with client_for(branch_runtime) as client:
        recovered = await client.post(endpoint, json=body, headers={"Idempotency-Key": "crash-key"})
        assert recovered.status_code == 202, recovered.text
        target = recovered.json()["invocation_id"]
        assert target == marker[2]
        finished = await wait_terminal(services, target)
        assert finished.status == (
            "faulted" if policy == "read_only" or phase == "effect" else "completed"
        )
        expected = before + (1 if policy == "reexecute" and phase != "effect" else 0)
        assert len(services.model.calls) == expected
        repeated = await client.post(endpoint, json=body, headers={"Idempotency-Key": "crash-key"})
        assert repeated.status_code == 202 and repeated.json()["invocation_id"] == target
        assert len(services.model.calls) == expected
