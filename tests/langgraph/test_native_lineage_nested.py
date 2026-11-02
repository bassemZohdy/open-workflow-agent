"""Completed nested scopes retain their evidence when a root checkpoint is replayed."""

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
from open_workflow_agent.storage import open_storage
from open_workflow_agent.workflow import compile_workflow, generate_default_workflow
from test_state_replay_api import client_for, wait_terminal

pytest.importorskip("langgraph.graph")
from open_workflow_agent_langgraph import LangGraphWorkflowEngine
from open_workflow_agent_langgraph.graph import LangGraphScopeExecutor


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["read_only", "reexecute"])
@pytest.mark.parametrize("kind", ["replay", "branch"])
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("iterations", [1, 2])
async def test_root_lineage_after_completed_nested_scope_preserves_effects(
    tmp_path, monkeypatch, policy, kind, backend, iterations
):
    for actor in ("alice", "bob"):
        monkeypatch.setenv("OWA_NATIVE_STREAM_TEST_" + actor.upper(), "replay-state-" + actor)
    config = configuration(timeout_seconds=30)
    config.execution_authorization.policy.rules[0].actions.extend(["state." + kind, "state.update"])
    if kind == "replay":
        config.native_state_replay = NativeStateReplayConfig()
    else:
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
    source["document"]["name"] = "nested-replay-" + uuid4().hex
    source["document"]["metadata"] = {
        "owa": {
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
    }
    if kind == "branch":
        owa = source["document"]["metadata"]["owa"]
        owa["nativeExtensions"].append({"name": "langgraph.state.update", "version": "1.0.0"})
        owa["nativeExtensionData"]["langgraph.state.update@1.0.0"] = {
            "allowedTaskRefs": ["/do/1"],
            "updateSchema": {
                "type": "object",
                "properties": {"value": {"type": "integer"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            "authorizationPolicyRef": config.execution_authorization.id,
        }
    nested = {"do": [{"inner": {"do": [{"effect": {"call": "agent:1.0.0@default"}}]}}]}
    if iterations > 1:
        nested["for"] = {"each": "turn", "in": list(range(iterations))}
    source["do"] = [
        {"nested": nested},
        {"change": {"set": {"value": 1}}},
        {"after": {"call": "agent:1.0.0@default"}},
    ]
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
        assert len(services.model.calls) == iterations + 1
        async with client_for((services, engine)) as client:
            history = await client.get(f"/v1/invocations/{handle.invocation_id}/states")
            assert history.status_code == 200, history.text
            expected_visits = {"/do/0/nested": 1}
            if kind == "branch":
                expected_visits["/do/1/change"] = 1
            reference = next(
                item["reference"]
                for item in history.json()["items"]
                if item.get("root_task_visits") == expected_visits
            )
            body = {"reference": reference, "acknowledge_reexecution": policy == "reexecute"}
            if kind == "branch":
                body.update({"task_reference": "/do/1/change", "values": {"value": 7}})
            route = "state-replays" if kind == "replay" else "state-branches"
            response = await client.post(
                f"/v1/invocations/{handle.invocation_id}/{route}",
                json=body,
                headers={"Idempotency-Key": "nested-" + kind},
            )
            assert response.status_code == 202, response.text
            target = await wait_terminal(services, response.json()["invocation_id"])
            assert target.status == ("faulted" if policy == "read_only" else "completed")
            assert len(services.model.calls) == iterations + (1 if policy == "read_only" else 2)
            target_history = await client.get(f"/v1/invocations/{target.invocation_id}/states")
            assert target_history.status_code == 200, target_history.text
            if kind == "branch":
                assert any(item["data"] == {"value": 7} for item in target_history.json()["items"])
            assert (
                await client.post(
                    f"/v1/invocations/{handle.invocation_id}/{route}",
                    json=body,
                    headers={"Idempotency-Key": "nested-" + kind},
                )
            ).json()["invocation_id"] == target.invocation_id
            if kind == "branch" and policy == "reexecute":
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
                        repeat = await fresh.post(
                            f"/v1/invocations/{handle.invocation_id}/{route}",
                            json=body,
                            headers={"Idempotency-Key": "nested-" + kind},
                        )
                        assert repeat.status_code == 202, repeat.text
                        assert repeat.json()["invocation_id"] == target.invocation_id
                        assert reopened.model.calls == []
                finally:
                    await restarted.shutdown()
                    reopened.close()
                coordinator = services.native_branch_controller.coordinator
                request_id = uuid4().hex * 2
                interrupted_target = []

                def stop_after_fork(phase, invocation):
                    if phase == "forked":
                        interrupted_target.append(invocation)
                        raise RuntimeError("injected interruption after nested fork")

                command = {
                    "request_id": request_id,
                    "source_reference": reference,
                    "task_reference": "/do/1/change",
                    "values": {"value": 7},
                    "acknowledge_reexecution": True,
                }
                before_calls = len(services.model.calls)
                with pytest.raises(RuntimeError, match="injected interruption"):
                    await coordinator.run_branch(
                        handle, authority(services, "alice"), **command, _phase=stop_after_fork
                    )
                assert len(interrupted_target) == 1
                assert len(services.model.calls) == before_calls
                recovered_target = await coordinator.run_branch(
                    handle, authority(services, "alice"), **command
                )
                assert recovered_target.invocation_id == interrupted_target[0]
                assert recovered_target.status == "completed"
                assert len(services.model.calls) == before_calls + 1
            if policy == "reexecute":
                child = next(
                    scope.reference
                    for scope in plan.scopes
                    if scope.reference.endswith("/inner/do")
                )
                thread = LangGraphScopeExecutor.scope_thread_id(
                    target.invocation_id, plan.fingerprint, child, 1
                )
                connection = open_storage(
                    services.engine_database_path("langgraph", create_namespace=False),
                    "owa_langgraph",
                )
                try:
                    assert (
                        connection.execute(
                            "DELETE FROM checkpoints WHERE thread_id=?", (thread,)
                        ).rowcount
                        > 0
                    )
                    connection.commit()
                finally:
                    connection.close()
                damaged = await client.get(f"/v1/invocations/{target.invocation_id}/states")
                assert damaged.status_code == 409, damaged.text
    finally:
        await engine.shutdown()
        services.close()
