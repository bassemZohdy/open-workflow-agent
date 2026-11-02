"""A lineage fork must preserve declared native Command routing."""

import json
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
from open_workflow_agent.errors import OwaError
from open_workflow_agent.execution_authority import authority_scope
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.storage import open_storage
from open_workflow_agent.workflow import compile_workflow, generate_default_workflow
from test_state_replay_api import client_for, wait_terminal

pytest.importorskip("langgraph.graph")
from open_workflow_agent_langgraph import LangGraphWorkflowEngine
from open_workflow_agent_langgraph.graph import LangGraphScopeExecutor, _node_name
from open_workflow_agent_langgraph.native_lineage_transition import root_lineage_position


@pytest.mark.parametrize(
    "damage", ["none", "wrong_next", "backward", "extra_visit", "repeated", "reordered_contiguous"]
)
def test_forward_route_requires_a_unique_consistent_path(damage):
    source = generate_default_workflow()
    source["do"] = [
        {"first": {"set": {"value": 1}, "then": "finish"}},
        {"never": {"set": {"value": 2}}},
        {"finish": {"set": {"value": 3}}},
        {"after": {"set": {"value": 4}}},
    ]
    if damage == "backward":
        source["do"][2]["finish"]["then"] = "first"
    if damage == "reordered_contiguous":
        source["do"][1]["never"]["then"] = "after"
        source["do"][2]["finish"]["then"] = "never"
    plan = compile_workflow(source)
    visits = {"/do/0/first": 1, "/do/2/finish": 1}
    if damage == "extra_visit":
        visits["/do/3/after"] = 1
    if damage == "repeated":
        visits["/do/2/finish"] = 2
    if damage == "reordered_contiguous":
        visits["/do/1/never"] = 1
    destination = _node_name(plan.scopes[0].tasks[3 if damage != "wrong_next" else 0])
    native = {"snapshot": json.dumps({"task_visits": visits}), "next_node": destination}
    if damage == "none":
        assert root_lineage_position(plan, native) == 3
    else:
        with pytest.raises(OwaError):
            root_lineage_position(plan, native)


@pytest.mark.parametrize("backward", [False, True])
def test_contiguous_switch_scope_must_not_allow_backward_routing(backward):
    source = generate_default_workflow()
    source["do"] = [
        {"first": {"set": {"value": 1}}},
        {
            "route": {
                "switch": [
                    {"choice": {"when": "${ true }", "then": "first" if backward else "finish"}}
                ]
            }
        },
        {"finish": {"set": {"value": 3}}},
        {"after": {"set": {"value": 4}}},
    ]
    plan = compile_workflow(source)
    native = {
        "snapshot": json.dumps(
            {
                "task_visits": {
                    "/do/0/first": 1,
                    "/do/1/route": 1,
                    "/do/2/finish": 1,
                }
            }
        ),
        "next_node": _node_name(plan.scopes[0].tasks[3]),
    }
    if backward:
        with pytest.raises(OwaError):
            root_lineage_position(plan, native)
    else:
        assert root_lineage_position(plan, native) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_reordered_contiguous_visits_do_not_authorize_wrong_attribution(
    tmp_path, monkeypatch, backend
):
    monkeypatch.setenv("OWA_NATIVE_STREAM_TEST_ALICE", "replay-state-alice")
    config = configuration(timeout_seconds=30)
    config.execution_authorization.policy.rules[0].actions.extend(["state.branch", "state.update"])
    config.native_state_updates = NativeStateUpdateConfig()
    config.native_state_branch = NativeStateBranchConfig()
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
    source["document"]["name"] = "reordered-lineage-" + uuid4().hex
    source["do"] = [
        {"first": {"set": {"value": 1}, "then": "third"}},
        {"second": {"set": {"value": 2}, "then": "after"}},
        {"third": {"set": {"value": 3}, "then": "second"}},
        {"after": {"call": "agent:1.0.0@default"}},
    ]
    source["document"]["metadata"] = {
        "owa": {
            "nativeExtensions": [
                {"name": "langgraph.state.read", "version": "1.0.0"},
                {"name": "langgraph.state.update", "version": "1.0.0"},
                {"name": "langgraph.state.branch", "version": "1.0.0"},
                {"name": "langgraph.command", "version": "1.0.0"},
            ],
            "nativeExtensionData": {
                "langgraph.state.update@1.0.0": {
                    "allowedTaskRefs": ["/do/1", "/do/2"],
                    "updateSchema": {
                        "type": "object",
                        "properties": {"value": {"type": "integer"}},
                        "required": ["value"],
                        "additionalProperties": False,
                    },
                    "authorizationPolicyRef": config.execution_authorization.id,
                },
                "langgraph.state.branch@1.0.0": {
                    "authorizationPolicyRef": config.execution_authorization.id,
                    "branchEffectPolicy": "reexecute",
                },
            },
        }
    }
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
            selected = next(
                item
                for item in history.json()["items"]
                if item["root_task_visits"]
                == {
                    "/do/0/first": 1,
                    "/do/1/second": 1,
                    "/do/2/third": 1,
                }
            )
            count = services.invocations.connection.execute(
                "SELECT COUNT(*) FROM invocations"
            ).fetchone()[0]
            for task in ("/do/1/second", "/do/2/third"):
                result = await client.post(
                    f"/v1/invocations/{handle.invocation_id}/state-branches",
                    json={
                        "reference": selected["reference"],
                        "task_reference": task,
                        "values": {"value": 8},
                        "acknowledge_reexecution": True,
                    },
                    headers={"Idempotency-Key": "reordered-" + task.rsplit("/", 1)[-1]},
                )
                assert result.status_code == 409, result.text
            assert (
                services.invocations.connection.execute(
                    "SELECT COUNT(*) FROM invocations"
                ).fetchone()[0]
                == count
            )
            assert len(services.model.calls) == 1
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("kind", ["replay", "branch"])
@pytest.mark.parametrize("policy", ["read_only", "reexecute"])
@pytest.mark.parametrize("selected_step", ["first", "jump", "finish"])
@pytest.mark.parametrize("native_command", [True, False])
async def test_lineage_continues_native_command_routing(
    tmp_path, monkeypatch, backend, kind, policy, selected_step, native_command
):
    if not native_command and (selected_step != "finish" or policy != "reexecute"):
        pytest.skip("conditional routing coverage uses the completed forward jump")
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
    source["document"]["name"] = "command-lineage-" + uuid4().hex
    source["do"] = [
        {"first": {"set": {"value": 1}}},
        {"jump": {"set": {"value": "${ .value + 1 }"}, "then": "finish"}},
        {"never": {"call": "agent:1.0.0@default"}},
        {"finish": {"set": {"value": "${ .value + 1 }"}}},
        {"after": {"call": "agent:1.0.0@default"}},
    ]
    owa = {
        "nativeExtensions": [
            {"name": "langgraph.state.read", "version": "1.0.0"},
            {"name": "langgraph.state." + kind, "version": "1.0.0"},
        ],
        "nativeExtensionData": {
            "langgraph.state." + kind + "@1.0.0": {
                "authorizationPolicyRef": config.execution_authorization.id,
                kind + "EffectPolicy": policy,
            }
        },
    }
    if native_command:
        owa["nativeExtensions"].append({"name": "langgraph.command", "version": "1.0.0"})
    if kind == "branch":
        owa["nativeExtensions"].append({"name": "langgraph.state.update", "version": "1.0.0"})
        owa["nativeExtensionData"]["langgraph.state.update@1.0.0"] = {
            "allowedTaskRefs": ["/do/0", "/do/1", "/do/3"],
            "updateSchema": {
                "type": "object",
                "properties": {"value": {"type": "integer"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            "authorizationPolicyRef": config.execution_authorization.id,
        }
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
        assert len(services.model.calls) == 1
        async with client_for((services, engine)) as client:
            history = await client.get(f"/v1/invocations/{handle.invocation_id}/states")
            assert history.status_code == 200, history.text
            expected_visits = {"/do/0/first": 1}
            if selected_step in {"jump", "finish"}:
                expected_visits["/do/1/jump"] = 1
            if selected_step == "finish":
                expected_visits["/do/3/finish"] = 1
            selected = next(
                item
                for item in history.json()["items"]
                if item["root_task_visits"] == expected_visits
            )
            body = {
                "reference": selected["reference"],
                "acknowledge_reexecution": policy == "reexecute",
            }
            if kind == "branch":
                body.update(
                    {
                        "task_reference": {
                            "first": "/do/0/first",
                            "jump": "/do/1/jump",
                            "finish": "/do/3/finish",
                        }[selected_step],
                        "values": {"value": 10},
                    }
                )
            route = "state-replays" if kind == "replay" else "state-branches"
            if kind == "branch" and selected_step == "finish":
                wrong = await client.post(
                    f"/v1/invocations/{handle.invocation_id}/{route}",
                    json=body | {"task_reference": "/do/0/first"},
                    headers={"Idempotency-Key": "wrong-attribution"},
                )
                assert wrong.status_code == 409, wrong.text
                assert len(services.model.calls) == 1
            response = await client.post(
                f"/v1/invocations/{handle.invocation_id}/{route}",
                json=body,
                headers={"Idempotency-Key": "command-" + kind},
            )
            assert response.status_code == 202, response.text
            target = await wait_terminal(services, response.json()["invocation_id"])
            assert target.status == ("faulted" if policy == "read_only" else "completed")
            assert len(services.model.calls) == (1 if policy == "read_only" else 2)
            target_history = await client.get(f"/v1/invocations/{target.invocation_id}/states")
            assert target_history.status_code == 200, target_history.text
            values = target_history.json()["items"]
            expected_value = (
                {"first": 12, "jump": 11, "finish": 10}[selected_step] if kind == "branch" else 3
            )
            assert any(item["data"] == {"value": expected_value} for item in values)
            assert all("/do/2/never" not in item["root_task_visits"] for item in values)
            if (
                kind == "branch"
                and policy == "reexecute"
                and selected_step == "finish"
                and native_command
            ):
                coordinator = services.native_branch_controller.coordinator
                stopped = []

                def stop_after_forward_fork(phase, invocation):
                    if phase == "forked":
                        stopped.append(invocation)
                        raise RuntimeError("stop after forward fork")

                command = {
                    "request_id": uuid4().hex * 2,
                    "source_reference": selected["reference"],
                    "task_reference": "/do/3/finish",
                    "values": {"value": 10},
                    "acknowledge_reexecution": True,
                }
                before_calls = len(services.model.calls)
                with pytest.raises(RuntimeError, match="stop after forward fork"):
                    await coordinator.run_branch(
                        handle,
                        authority(services, "alice"),
                        **command,
                        _phase=stop_after_forward_fork,
                    )
                assert len(stopped) == 1 and len(services.model.calls) == before_calls
                reopened = RuntimeServices(
                    config,
                    model=FakeModel(),
                    database_root=None if backend == "postgresql" else tmp_path,
                )
                restarted = LangGraphWorkflowEngine()
                await restarted.initialize(reopened)
                await restarted._timer_dispatcher.stop()
                try:
                    source_handle = reopened.invocations.get(handle.invocation_id)
                    recovered = await reopened.native_branch_controller.coordinator.run_branch(
                        source_handle, authority(reopened, "alice"), **command
                    )
                    assert recovered.invocation_id == stopped[0] and recovered.status == "completed"
                    assert len(reopened.model.calls) == 1
                finally:
                    await restarted.shutdown()
                    reopened.close()
                assert len(services.model.calls) == before_calls
            if kind == "branch" and policy == "reexecute" and selected_step == "jump":
                coordinator = services.native_branch_controller.coordinator
                stopped = []

                def stop_after_fork(phase, invocation):
                    if phase == "forked":
                        stopped.append(invocation)
                        raise RuntimeError("stop after command fork")

                command = {
                    "request_id": uuid4().hex * 2,
                    "source_reference": selected["reference"],
                    "task_reference": "/do/1/jump",
                    "values": {"value": 10},
                    "acknowledge_reexecution": True,
                }
                before_calls = len(services.model.calls)
                with pytest.raises(RuntimeError, match="stop after command fork"):
                    await coordinator.run_branch(
                        handle, authority(services, "alice"), **command, _phase=stop_after_fork
                    )
                assert len(stopped) == 1
                target_id = stopped[0]
                database = services.engine_database_path("langgraph", create_namespace=False)
                connection = open_storage(database, "owa_langgraph")
                try:
                    record = connection.execute(
                        "SELECT result_json FROM owa_state_lineage_v1 WHERE target_id=?",
                        (target_id,),
                    ).fetchone()
                    checkpoint_id = json.loads(record[0])[0][2]
                    thread = LangGraphScopeExecutor.scope_thread_id(
                        target_id, plan.fingerprint, "/do", 1
                    )
                    row = connection.execute(
                        "SELECT checkpoint"
                        + ("" if connection.is_postgresql else ",type")
                        + " FROM checkpoints WHERE thread_id=? AND checkpoint_id=?",
                        (thread, checkpoint_id),
                    ).fetchone()
                    if connection.is_postgresql:
                        checkpoint = row[0]
                    else:
                        from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

                        serde = JsonPlusSerializer()
                        checkpoint = serde.loads_typed((row[1], row[0]))
                    destination = _node_name(plan.scopes[0].tasks[3])
                    trigger = "branch:to:" + destination
                    checkpoint["versions_seen"].setdefault(destination, {})[trigger] = checkpoint[
                        "channel_versions"
                    ][trigger]
                    if connection.is_postgresql:
                        from psycopg.types.json import Jsonb

                        connection.execute(
                            "UPDATE checkpoints SET checkpoint=? "
                            "WHERE thread_id=? AND checkpoint_id=?",
                            (Jsonb(checkpoint), thread, checkpoint_id),
                        )
                    else:
                        marker, value = serde.dumps_typed(checkpoint)
                        connection.execute(
                            "UPDATE checkpoints SET checkpoint=?,type=? "
                            "WHERE thread_id=? AND checkpoint_id=?",
                            (value, marker, thread, checkpoint_id),
                        )
                    connection.commit()
                finally:
                    connection.close()
                with pytest.raises(OwaError):
                    await coordinator.run_branch(handle, authority(services, "alice"), **command)
                assert len(services.model.calls) == before_calls
    finally:
        await engine.shutdown()
        services.close()
