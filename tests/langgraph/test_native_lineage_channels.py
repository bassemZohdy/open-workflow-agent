"""Lineage must preserve declared channel values without repeating reducer updates."""

import os
from uuid import uuid4

import pytest
from native_state_test_support import authority, configuration
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import (
    NativeStateBranchConfig,
    NativeStateReplayConfig,
    NativeStateUpdateConfig,
)
from open_workflow_agent.execution_authority import authority_scope
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow, generate_default_workflow
from test_state_replay_api import client_for, wait_terminal

pytest.importorskip("langgraph.graph")
from open_workflow_agent_langgraph import LangGraphWorkflowEngine


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("kind", ["replay", "branch"])
@pytest.mark.parametrize("policy", ["read_only", "reexecute"])
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("command", [False, True])
async def test_channel_values_survive_lineage_and_continue_reducing(
    tmp_path, monkeypatch, backend, kind, policy, nested, command
):
    if command and (kind != "branch" or policy != "reexecute"):
        pytest.skip("command/channel combinations use the reexecuting branch case")
    if nested and policy == "read_only":
        pytest.skip("nested channel combinations use the reexecution case")
    for actor in ("alice", "bob"):
        monkeypatch.setenv("OWA_NATIVE_STREAM_TEST_" + actor.upper(), "replay-state-" + actor)
    config = configuration(timeout_seconds=30)
    config.execution_authorization.policy.rules[0].actions.extend(["state." + kind, "state.update"])
    if kind == "branch":
        config.native_state_updates = NativeStateUpdateConfig()
        config.native_state_branch = NativeStateBranchConfig()
    else:
        config.native_state_replay = NativeStateReplayConfig()
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
    source["document"]["name"] = "channel-lineage-" + uuid4().hex
    source["do"] = [
        {"first": {"set": '${ ["first"] }'}},
        {"second": {"set": '${ ["second"] }'}},
        {"third": {"set": '${ ["third"] }'}},
        {"read_items": {"set": "${ $owa_native_channels.items }"}},
        {"after": {"call": "agent:1.0.0@default"}},
    ]
    offset = 1 if nested else 0
    if nested:
        source["do"].insert(0, {"nested": {"do": [{"inside": {"set": {"nested": True}}}]}})
    owa = {
        "nativeExtensions": [
            {"name": "langgraph.state.read", "version": "1.0.0"},
            {"name": "langgraph.state." + kind, "version": "1.0.0"},
            {"name": "langgraph.channels", "version": "1.0.0"},
        ],
        "nativeExtensionData": {
            "langgraph.state." + kind + "@1.0.0": {
                "authorizationPolicyRef": config.execution_authorization.id,
                kind + "EffectPolicy": policy,
            },
            "langgraph.channels@1.0.0": {
                "channels": [
                    {
                        "id": "items",
                        "reducer": "append",
                        "initial": ["seed"],
                        "writes": [{"task": f"/do/{offset + index}"} for index in range(3)],
                    },
                    {
                        "id": "last",
                        "reducer": "last_value",
                        "initial": [],
                        "writes": [{"task": f"/do/{offset + index}"} for index in range(3)],
                    },
                ],
                "reads": [
                    {
                        "task": f"/do/{offset + 3}",
                        "as": "owa_native_channels",
                        "channels": ["items"],
                    }
                ],
            },
        },
    }
    if kind == "branch":
        owa["nativeExtensions"].append({"name": "langgraph.state.update", "version": "1.0.0"})
        owa["nativeExtensionData"]["langgraph.state.update@1.0.0"] = {
            "allowedTaskRefs": [f"/do/{offset + 1}"],
            "updateSchema": {
                "type": "object",
                "properties": {"edited": {"type": "boolean"}},
                "required": ["edited"],
                "additionalProperties": False,
            },
            "authorizationPolicyRef": config.execution_authorization.id,
        }
    if command:
        owa["nativeExtensions"].append({"name": "langgraph.command", "version": "1.0.0"})
    source["document"]["metadata"] = {"owa": owa}
    plan = compile_workflow(source)
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
    try:
        with authority_scope(authority(services, "alice")):
            assert (await engine.invoke(plan, handle, {})).status == "completed"
        async with client_for((services, engine)) as client:
            history = await client.get(f"/v1/invocations/{handle.invocation_id}/states")
            assert history.status_code == 200, history.text
            visits = {f"/do/{offset}/first": 1, f"/do/{offset + 1}/second": 1}
            if nested:
                visits["/do/0/nested"] = 1
            selected = next(
                item for item in history.json()["items"] if item["root_task_visits"] == visits
            )
            assert selected["channels"] == {
                "items": ["seed", "first", "second"],
                "last": ["second"],
            }
            body = {
                "reference": selected["reference"],
                "acknowledge_reexecution": policy == "reexecute",
            }
            if kind == "branch":
                body.update(
                    {"task_reference": f"/do/{offset + 1}/second", "values": {"edited": True}}
                )
            route = "state-replays" if kind == "replay" else "state-branches"
            response = await client.post(
                f"/v1/invocations/{handle.invocation_id}/{route}",
                json=body,
                headers={"Idempotency-Key": "channel-" + kind},
            )
            assert response.status_code == 202, response.text
            target = await wait_terminal(services, response.json()["invocation_id"])
            assert target.status == ("faulted" if policy == "read_only" else "completed")
            assert len(services.model.calls) == (1 if policy == "read_only" else 2)
            target_history = await client.get(f"/v1/invocations/{target.invocation_id}/states")
            assert target_history.status_code == 200, target_history.text
            items = target_history.json()["items"]
            assert any(item["channels"] == selected["channels"] for item in items)
            if kind == "branch":
                assert any(
                    item["data"] == {"edited": True} and item["channels"] == selected["channels"]
                    for item in items
                )
            expected = ["seed", "first", "second", "third"]
            assert any(
                item["data"] == expected
                and item["channels"] == {"items": expected, "last": ["third"]}
                for item in items
            )
            assert (
                await client.post(
                    f"/v1/invocations/{handle.invocation_id}/{route}",
                    json=body,
                    headers={"Idempotency-Key": "channel-" + kind},
                )
            ).json()["invocation_id"] == target.invocation_id
            if kind == "branch" and policy == "reexecute" and not nested:
                reopened = RuntimeServices(
                    config,
                    model=FakeModel(),
                    database_root=None if backend == "postgresql" else tmp_path,
                )
                restarted = LangGraphWorkflowEngine()
                await restarted.initialize(reopened)
                await restarted._timer_dispatcher.stop()
                try:
                    async with client_for((reopened, restarted)) as fresh:
                        recovered = await fresh.get(
                            f"/v1/invocations/{target.invocation_id}/states"
                        )
                        assert recovered.status_code == 200, recovered.text
                        assert any(
                            item["channels"] == selected["channels"]
                            for item in recovered.json()["items"]
                        )
                finally:
                    await restarted.shutdown()
                    reopened.close()
                coordinator = services.native_branch_controller.coordinator
                interrupted_target = []

                def stop_after_fork(phase, invocation):
                    if phase == "forked":
                        interrupted_target.append(invocation)
                        raise RuntimeError("injected interruption after channel fork")

                command = {
                    "request_id": uuid4().hex * 2,
                    "source_reference": selected["reference"],
                    "task_reference": "/do/1/second",
                    "values": {"edited": True},
                    "acknowledge_reexecution": True,
                }
                before_calls = len(services.model.calls)
                with pytest.raises(RuntimeError, match="injected interruption"):
                    await coordinator.run_branch(
                        handle, authority(services, "alice"), **command, _phase=stop_after_fork
                    )
                assert len(interrupted_target) == 1 and len(services.model.calls) == before_calls
                recovered = await coordinator.run_branch(
                    handle, authority(services, "alice"), **command
                )
                assert (
                    recovered.invocation_id == interrupted_target[0]
                    and recovered.status == "completed"
                )
                assert len(services.model.calls) == before_calls + 1
            original = await client.get(f"/v1/invocations/{handle.invocation_id}/state")
            assert original.status_code == 200, original.text
            assert original.json()["items"][0]["channels"] == {
                "items": expected,
                "last": ["third"],
            }
    finally:
        await engine.shutdown()
        services.close()
