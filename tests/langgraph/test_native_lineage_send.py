"""Lineage around a completed native Send fan-out preserves effect ownership."""

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
from open_workflow_agent.effects import EffectIdentity
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
@pytest.mark.parametrize("after_fan", [False, True])
@pytest.mark.parametrize("nested_branches", [False, True])
@pytest.mark.parametrize("with_command", [False, True])
async def test_send_lineage_does_not_repeat_retained_effects(
    tmp_path, monkeypatch, backend, kind, policy, after_fan, nested_branches, with_command
):
    if with_command and (kind != "branch" or policy != "reexecute"):
        pytest.skip("Command/Send coverage uses a reexecuting branch")
    if nested_branches and (not after_fan or kind != "branch" or policy != "reexecute"):
        pytest.skip("nested fan-out coverage uses a completed reexecuting branch")
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
    source["document"]["name"] = "send-lineage-" + uuid4().hex
    branches = [
        {"left": {"call": "agent:1.0.0@default"}},
        {"right": {"call": "agent:1.0.0@default"}},
    ]
    if nested_branches:
        branches = [
            {"left": {"do": [{"inside_left": {"call": "agent:1.0.0@default"}}]}},
            {"right": {"do": [{"inside_right": {"call": "agent:1.0.0@default"}}]}},
        ]
    source["do"] = [
        {"first": {"set": {"value": 1}}},
        {"fan": {"fork": {"branches": branches}}},
        {"change": {"set": {"value": 2}}},
        {"after": {"call": "agent:1.0.0@default"}},
    ]
    owa = {
        "nativeExtensions": [
            {"name": "langgraph.state.read", "version": "1.0.0"},
            {"name": "langgraph.state." + kind, "version": "1.0.0"},
            {"name": "langgraph.send", "version": "1.0.0"},
        ],
        "nativeExtensionData": {
            "langgraph.state." + kind + "@1.0.0": {
                "authorizationPolicyRef": config.execution_authorization.id,
                kind + "EffectPolicy": policy,
            }
        },
    }
    if with_command:
        owa["nativeExtensions"].append({"name": "langgraph.command", "version": "1.0.0"})
    if kind == "branch":
        owa["nativeExtensions"].append({"name": "langgraph.state.update", "version": "1.0.0"})
        owa["nativeExtensionData"]["langgraph.state.update@1.0.0"] = {
            "allowedTaskRefs": ["/do/0", "/do/2"],
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
            result = await engine.invoke(plan, handle, {})
            assert result.status == "completed", result.error
        assert len(services.model.calls) == 3
        async with client_for((services, engine)) as client:
            history = await client.get(f"/v1/invocations/{handle.invocation_id}/states")
            assert history.status_code == 200, history.text
            visits = {"/do/0/first": 1}
            if after_fan:
                visits.update({"/do/1/fan": 1, "/do/2/change": 1})
            selected = next(
                item for item in history.json()["items"] if item["root_task_visits"] == visits
            )
            body = {
                "reference": selected["reference"],
                "acknowledge_reexecution": policy == "reexecute",
            }
            if kind == "branch":
                body.update(
                    {
                        "task_reference": "/do/2/change" if after_fan else "/do/0/first",
                        "values": {"value": 8},
                    }
                )
            route = "state-replays" if kind == "replay" else "state-branches"
            response = await client.post(
                f"/v1/invocations/{handle.invocation_id}/{route}",
                json=body,
                headers={"Idempotency-Key": "send-" + kind},
            )
            assert response.status_code == 202, response.text
            target = await wait_terminal(services, response.json()["invocation_id"])
            assert target.status == ("faulted" if policy == "read_only" else "completed")
            expected = 3 if policy == "read_only" else (4 if after_fan else 6)
            assert len(services.model.calls) == expected
            history = await client.get(f"/v1/invocations/{target.invocation_id}/states")
            assert history.status_code == 200, history.text
            retry = await client.post(
                f"/v1/invocations/{handle.invocation_id}/{route}",
                json=body,
                headers={"Idempotency-Key": "send-" + kind},
            )
            assert (
                retry.status_code == 202 and retry.json()["invocation_id"] == target.invocation_id
            )
            assert len(services.model.calls) == expected
            if after_fan and kind == "branch" and policy == "reexecute":
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
                        restored = await fresh.get(f"/v1/invocations/{target.invocation_id}/states")
                        assert restored.status_code == 200, restored.text
                        repeated = await fresh.post(
                            f"/v1/invocations/{handle.invocation_id}/{route}",
                            json=body,
                            headers={"Idempotency-Key": "send-" + kind},
                        )
                        assert repeated.status_code == 202
                        assert repeated.json()["invocation_id"] == target.invocation_id
                        assert reopened.model.calls == []
                finally:
                    await restarted.shutdown()
                    reopened.close()
                stopped = []

                def stop_after_fork(phase, invocation):
                    if phase == "forked":
                        stopped.append(invocation)
                        raise RuntimeError("stop after send fork")

                command = {
                    "request_id": uuid4().hex * 2,
                    "source_reference": selected["reference"],
                    "task_reference": "/do/2/change",
                    "values": {"value": 8},
                    "acknowledge_reexecution": True,
                }
                before_calls = len(services.model.calls)
                coordinator = services.native_branch_controller.coordinator
                with pytest.raises(RuntimeError, match="stop after send fork"):
                    await coordinator.run_branch(
                        handle, authority(services, "alice"), **command, _phase=stop_after_fork
                    )
                assert len(stopped) == 1 and len(services.model.calls) == before_calls
                recovered = await coordinator.run_branch(
                    handle, authority(services, "alice"), **command
                )
                assert recovered.invocation_id == stopped[0] and recovered.status == "completed"
                assert len(services.model.calls) == before_calls + 1
                rows = services.effect_journal.connection.execute(
                    "SELECT identity,payload FROM workflow_effect_invocations "
                    "WHERE invocation_id=?",
                    (handle.invocation_id,),
                ).fetchall()
                left = next(
                    identity
                    for identity, raw in rows
                    if (entry := EffectIdentity.from_value(json.loads(raw))).operation.startswith(
                        "call:"
                    )
                    and "/left" in entry.task_reference
                )
                services.effect_journal.connection.execute(
                    "DELETE FROM workflow_effects WHERE identity=?", (left,)
                )
                services.effect_journal.connection.commit()
                before = services.invocations.connection.execute(
                    "SELECT COUNT(*) FROM invocations"
                ).fetchone()[0]
                denied = await client.post(
                    f"/v1/invocations/{handle.invocation_id}/{route}",
                    json=body,
                    headers={"Idempotency-Key": "missing-fan-receipt"},
                )
                assert denied.status_code == 409, denied.text
                assert (
                    services.invocations.connection.execute(
                        "SELECT COUNT(*) FROM invocations"
                    ).fetchone()[0]
                    == before
                )
    finally:
        await engine.shutdown()
        services.close()
