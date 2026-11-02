"""A completed Send fan-out can be retained on a verified forward route."""

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
@pytest.mark.parametrize("nested_branches", [False, True])
async def test_forward_send_lineage_preserves_branch_effects(
    tmp_path, monkeypatch, backend, kind, policy, nested_branches
):
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
    source["document"]["name"] = "forward-send-" + uuid4().hex
    source["do"] = [
        {"start": {"set": {"value": 1}, "then": "fan"}},
        {"never": {"set": {"value": -1}}},
        {
            "fan": {
                "fork": {
                    "branches": [
                        {"left": {"call": "agent:1.0.0@default"}},
                        {"right": {"call": "agent:1.0.0@default"}},
                    ]
                }
            }
        },
        {"change": {"set": {"value": 2}}},
        {"after": {"call": "agent:1.0.0@default"}},
    ]
    if nested_branches:
        source["do"][2]["fan"]["fork"]["branches"] = [
            {"left": {"do": [{"effect": {"call": "agent:1.0.0@default"}}]}},
            {"right": {"do": [{"effect": {"call": "agent:1.0.0@default"}}]}},
        ]
    extensions = [
        {"name": "langgraph.state.read", "version": "1.0.0"},
        {"name": "langgraph.state." + kind, "version": "1.0.0"},
        {"name": "langgraph.send", "version": "1.0.0"},
    ]
    data = {
        "langgraph.state." + kind + "@1.0.0": {
            "authorizationPolicyRef": config.execution_authorization.id,
            kind + "EffectPolicy": policy,
        }
    }
    if kind == "branch":
        extensions.append({"name": "langgraph.state.update", "version": "1.0.0"})
        data["langgraph.state.update@1.0.0"] = {
            "allowedTaskRefs": ["/do/1", "/do/3"],
            "updateSchema": {
                "type": "object",
                "properties": {"value": {"type": "integer"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            "authorizationPolicyRef": config.execution_authorization.id,
        }
    source["document"]["metadata"] = {
        "owa": {"nativeExtensions": extensions, "nativeExtensionData": data}
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
            result = await engine.invoke(plan, handle, {})
        assert result.status == "completed", result.error
        assert len(services.model.calls) == 3
        async with client_for((services, engine)) as client:
            history = await client.get(f"/v1/invocations/{handle.invocation_id}/states")
            assert history.status_code == 200, history.text
            selected = next(
                item
                for item in history.json()["items"]
                if item["root_task_visits"] == {"/do/0/start": 1, "/do/2/fan": 1, "/do/3/change": 1}
            )
            body = {
                "reference": selected["reference"],
                "acknowledge_reexecution": policy == "reexecute",
            }
            if kind == "branch":
                body.update({"task_reference": "/do/3/change", "values": {"value": 8}})
            route = "state-replays" if kind == "replay" else "state-branches"
            if kind == "branch":
                wrong = await client.post(
                    f"/v1/invocations/{handle.invocation_id}/{route}",
                    json=body | {"task_reference": "/do/1/never"},
                    headers={"Idempotency-Key": "wrong-attribution"},
                )
                assert wrong.status_code == 409, wrong.text
            response = await client.post(
                f"/v1/invocations/{handle.invocation_id}/{route}",
                json=body,
                headers={"Idempotency-Key": "forward-send"},
            )
            assert response.status_code == 202, response.text
            target = await wait_terminal(services, response.json()["invocation_id"], timeout=180)
            assert target.status == ("faulted" if policy == "read_only" else "completed")
            assert len(services.model.calls) == (3 if policy == "read_only" else 4)
            retained = await client.get(f"/v1/invocations/{target.invocation_id}/states")
            assert retained.status_code == 200, retained.text
            assert all(
                "/do/1/never" not in item["root_task_visits"] for item in retained.json()["items"]
            )
            assert any(
                item["data"] == {"value": 8 if kind == "branch" else 2}
                for item in retained.json()["items"]
            )
            retry = await client.post(
                f"/v1/invocations/{handle.invocation_id}/{route}",
                json=body,
                headers={"Idempotency-Key": "forward-send"},
            )
            assert (
                retry.status_code == 202 and retry.json()["invocation_id"] == target.invocation_id
            )
            assert len(services.model.calls) == (3 if policy == "read_only" else 4)
            if policy == "reexecute":
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
                denied = await client.get(f"/v1/invocations/{target.invocation_id}/states")
                assert denied.status_code == 409, denied.text
                before = services.invocations.connection.execute(
                    "SELECT COUNT(*) FROM invocations"
                ).fetchone()[0]
                rejected = await client.post(
                    f"/v1/invocations/{handle.invocation_id}/{route}",
                    json=body,
                    headers={"Idempotency-Key": "missing-fan-receipt"},
                )
                assert rejected.status_code == 409, rejected.text
                assert (
                    services.invocations.connection.execute(
                        "SELECT COUNT(*) FROM invocations"
                    ).fetchone()[0]
                    == before
                )
    finally:
        await engine.shutdown()
        services.close()
