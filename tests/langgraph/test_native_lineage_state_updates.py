"""Lineage from an attested state update retains its historical data and proof."""

import asyncio
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
from test_state_replay_api import client_for

pytest.importorskip("langgraph.graph")
from open_workflow_agent_langgraph import LangGraphWorkflowEngine


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("kind", ["replay", "branch"])
@pytest.mark.parametrize("policy", ["read_only", "reexecute"])
async def test_lineage_from_updated_waiting_checkpoint(
    tmp_path, monkeypatch, backend, kind, policy
):
    for actor in ("alice", "bob"):
        monkeypatch.setenv("OWA_NATIVE_STREAM_TEST_" + actor.upper(), "replay-state-" + actor)
    config = configuration(timeout_seconds=30)
    config.execution_authorization.policy.rules[0].actions.extend(["state.update", "state." + kind])
    config.native_state_updates = NativeStateUpdateConfig()
    if kind == "replay":
        config.native_state_replay = NativeStateReplayConfig()
    else:
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
    source["document"]["name"] = "updated-lineage-" + uuid4().hex
    source["document"]["metadata"] = {
        "owa": {
            "nativeExtensions": [
                {"name": "langgraph.state.read", "version": "1.0.0"},
                {"name": "langgraph.state.update", "version": "1.0.0"},
                {"name": "langgraph.state." + kind, "version": "1.0.0"},
            ],
            "nativeExtensionData": {
                "langgraph.state.update@1.0.0": {
                    "allowedTaskRefs": ["/do/0"],
                    "updateSchema": {
                        "type": "object",
                        "properties": {"value": {"type": "integer"}},
                        "required": ["value"],
                        "additionalProperties": False,
                    },
                    "authorizationPolicyRef": config.execution_authorization.id,
                },
                "langgraph.state." + kind + "@1.0.0": {
                    "authorizationPolicyRef": config.execution_authorization.id,
                    kind + "EffectPolicy": policy,
                },
            },
        }
    }
    source["do"] = [
        {"first": {"set": {"value": 1}}},
        {"pause": {"wait": {"seconds": 300}}},
        {"last": {"set": {"value": "${ .value + 1 }"}}},
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
            result = await engine.invoke(plan, handle, {})
        assert result.status == "waiting", result.error
        path = f"/v1/invocations/{handle.invocation_id}"
        async with client_for((services, engine)) as client:
            current = await client.get(path + "/state")
            assert current.status_code == 200, current.text
            updated = await client.post(
                path + "/state-updates",
                json={
                    "base_reference": current.json()["items"][0]["reference"],
                    "task_reference": "/do/0/first",
                    "values": {"value": 6},
                },
                headers={"Idempotency-Key": "update"},
            )
            assert updated.status_code == 200, updated.text
            current = await client.get(path + "/state")
            assert current.status_code == 200, current.text
            second = await client.post(
                path + "/state-updates",
                json={
                    "base_reference": current.json()["items"][0]["reference"],
                    "task_reference": "/do/0/first",
                    "values": {"value": 7},
                },
                headers={"Idempotency-Key": "second-update"},
            )
            assert second.status_code == 200, second.text
            current = await client.get(path + "/state")
            assert current.status_code == 200, current.text
            reference = current.json()["items"][0]["reference"]
            body = {"reference": reference, "acknowledge_reexecution": policy == "reexecute"}
            if kind == "branch":
                body.update({"task_reference": "/do/0/first", "values": {"value": 9}})
            route = "/state-replays" if kind == "replay" else "/state-branches"
            response = await client.post(
                path + route, json=body, headers={"Idempotency-Key": "lineage"}
            )
            assert response.status_code == 202, response.text
            target = response.json()["invocation_id"]
            async with asyncio.timeout(60):
                while services.invocations.get(target).status not in {
                    "waiting",
                    "faulted",
                    "completed",
                }:
                    await asyncio.sleep(0.1)
            assert services.invocations.get(target).status == "waiting"
            coordinator = getattr(services, "native_" + kind + "_controller").coordinator
            invoke = coordinator.run if kind == "replay" else coordinator.run_branch
            interrupted = []
            command = {
                "request_id": uuid4().hex * 2,
                "source_reference": reference,
                "acknowledge_reexecution": policy == "reexecute",
            }
            if kind == "branch":
                command.update({"task_reference": "/do/0/first", "values": {"value": 9}})

            def stop_after_reservation(phase, invocation):
                if phase == "reserved":
                    interrupted.append(invocation)
                    raise RuntimeError("stop after reservation")

            if policy == "reexecute":
                with pytest.raises(RuntimeError, match="stop after reservation"):
                    await invoke(
                        handle,
                        authority(services, "alice"),
                        **command,
                        _phase=stop_after_reservation,
                    )
                assert len(interrupted) == 1
            # A later update on the source must not rewrite the selected historical basis.
            later = await client.post(
                path + "/state-updates",
                json={
                    "base_reference": reference,
                    "task_reference": "/do/0/first",
                    "values": {"value": 12},
                },
                headers={"Idempotency-Key": "later-update"},
            )
            assert later.status_code == 200, later.text
            if interrupted:
                recovered = await invoke(handle, authority(services, "alice"), **command)
                assert recovered.invocation_id == interrupted[0]
                assert recovered.status == "waiting"
            resumed = await client.post(f"/v1/invocations/{target}/resume", json={"input": None})
            assert resumed.status_code == 200, resumed.text
            assert resumed.json()["status"] == "completed", resumed.text
            assert resumed.json()["output"] == {"value": 8 if kind == "replay" else 10}
            repeated = await client.post(
                path + route, json=body, headers={"Idempotency-Key": "lineage"}
            )
            assert repeated.status_code == 202, repeated.text
            assert repeated.json()["invocation_id"] == target
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
                    restored = await fresh.get(f"/v1/invocations/{target}/states")
                    assert restored.status_code == 200, restored.text
                    retry = await fresh.post(
                        path + route, json=body, headers={"Idempotency-Key": "lineage"}
                    )
                    assert retry.status_code == 202, retry.text
                    assert retry.json()["invocation_id"] == target
            finally:
                await restarted.shutdown()
                reopened.close()
            guarded_target = None
            if policy == "reexecute":
                guarded = await client.post(
                    path + route, json=body, headers={"Idempotency-Key": "guarded"}
                )
                assert guarded.status_code == 202, guarded.text
                guarded_target = guarded.json()["invocation_id"]
                async with asyncio.timeout(60):
                    while services.invocations.get(guarded_target).status not in {
                        "waiting",
                        "faulted",
                        "completed",
                    }:
                        await asyncio.sleep(0.1)
                assert services.invocations.get(guarded_target).status == "waiting"
            # Damage the selected control's acknowledgement; neither a new target nor
            # inspection of the existing target may silently adopt its changed proof.
            services.effect_journal.connection.execute(
                "UPDATE state_update_intents_v1 SET proof_json=? WHERE invocation_id=?",
                ("{}", handle.invocation_id),
            )
            services.effect_journal.connection.commit()
            before = services.invocations.connection.execute(
                "SELECT COUNT(*) FROM invocations"
            ).fetchone()[0]
            denied = await client.post(
                path + route, json=body, headers={"Idempotency-Key": "damaged"}
            )
            assert denied.status_code == 409, denied.text
            assert (
                services.invocations.connection.execute(
                    "SELECT COUNT(*) FROM invocations"
                ).fetchone()[0]
                == before
            )
            inspected = await client.get(f"/v1/invocations/{target}/states")
            assert inspected.status_code != 200, inspected.text
            if guarded_target is not None:
                denied_resume = await client.post(
                    f"/v1/invocations/{guarded_target}/resume", json={"input": None}
                )
                assert denied_resume.status_code >= 400 or (
                    denied_resume.json()["status"] == "faulted"
                ), denied_resume.text
    finally:
        await engine.shutdown()
        services.close()
