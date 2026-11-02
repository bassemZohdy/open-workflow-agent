"""A later child occurrence cannot hide repeated effects behind total visit counts."""

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
from open_workflow_agent.execution_authority import authority_scope
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow, generate_default_workflow
from test_state_replay_api import client_for

pytest.importorskip("langgraph.graph")
from open_workflow_agent_langgraph import LangGraphWorkflowEngine


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("kind", ["replay", "branch"])
@pytest.mark.parametrize("scenario", ["catchup", "reordered"])
async def test_repeated_child_cannot_catch_up_after_a_skipped_occurrence(
    tmp_path, monkeypatch, backend, kind, scenario
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
    source["document"]["name"] = "child-catch-up-" + uuid4().hex
    source["do"] = [
        {
            "nested": {
                "for": {"each": "turn", "in": [0, 1]},
                "do": [
                    {
                        "start": {
                            "switch": [{"skip": {"when": "${ $turn == 0 }", "then": "finish"}}]
                        }
                    },
                    {"repeat": {"set": {"count": "${ (.count // 0) + 1 }"}}},
                    {
                        "again": {
                            "switch": [{"loop": {"when": "${ .count < 2 }", "then": "repeat"}}]
                        }
                    },
                    {"finish": {"call": "agent:1.0.0@default"}},
                ],
            }
        },
        {"change": {"set": {"value": 1}}},
        {"after": {"call": "agent:1.0.0@default"}},
    ]
    if scenario == "reordered":
        source["do"][0]["nested"] = {
            "do": [
                {"first": {"set": {"value": 1}, "then": "finish"}},
                {"middle": {"set": {"value": 2}, "then": "exit"}},
                {"finish": {"call": "agent:1.0.0@default", "then": "middle"}},
            ]
        }
    extensions = [
        {"name": "langgraph.state.read", "version": "1.0.0"},
        {"name": "langgraph.state." + kind, "version": "1.0.0"},
    ]
    data = {
        "langgraph.state." + kind + "@1.0.0": {
            "authorizationPolicyRef": config.execution_authorization.id,
            kind + "EffectPolicy": "reexecute",
        }
    }
    if kind == "branch":
        extensions.append({"name": "langgraph.state.update", "version": "1.0.0"})
        data["langgraph.state.update@1.0.0"] = {
            "allowedTaskRefs": ["/do/1"],
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
        expected_calls = 3 if scenario == "catchup" else 2
        assert len(services.model.calls) == expected_calls
        async with client_for((services, engine)) as client:
            history = await client.get(f"/v1/invocations/{handle.invocation_id}/states")
            assert history.status_code == 200, history.text
            selected = next(
                item
                for item in history.json()["items"]
                if item["root_task_visits"] == {"/do/0/nested": 1, "/do/1/change": 1}
            )
            body = {"reference": selected["reference"], "acknowledge_reexecution": True}
            if kind == "branch":
                body.update({"task_reference": "/do/1/change", "values": {"value": 7}})
            from open_workflow_agent_langgraph.native_lineage_preflight import (
                prepare_native_lineage,
            )

            evidence = await prepare_native_lineage(
                services,
                handle,
                authority(services, "alice"),
                kind=kind,
                target_invocation_id=str(uuid4()),
                request_id=uuid4().hex * 2,
                source_reference=selected["reference"],
                acknowledge_reexecution=True,
                task_reference="/do/1/change" if kind == "branch" else None,
                values={"value": 7} if kind == "branch" else None,
            )
            visits = {
                item.occurrence: json.loads(json.loads(item.native_json)["snapshot"])["task_visits"]
                for item in evidence.snapshots
                if item.reference == "/do/0/nested/do"
            }
            if scenario == "catchup":
                for task in ("repeat", "again"):
                    reference = f"/do/0/nested/do/{1 if task == 'repeat' else 2}/{task}"
                    assert visits[1].get(reference, 0) == 0
                    assert visits[2].get(reference, 0) == 2
            else:
                for index, task in enumerate(("first", "middle", "finish")):
                    assert visits[1].get(f"/do/0/nested/do/{index}/{task}") == 1
            route = "state-replays" if kind == "replay" else "state-branches"
            before = services.invocations.connection.execute(
                "SELECT COUNT(*) FROM invocations"
            ).fetchone()[0]
            response = await client.post(
                f"/v1/invocations/{handle.invocation_id}/{route}",
                json=body,
                headers={"Idempotency-Key": "catch-up"},
            )
            assert response.status_code == 409, response.text
            assert (
                services.invocations.connection.execute(
                    "SELECT COUNT(*) FROM invocations"
                ).fetchone()[0]
                == before
            )
            assert len(services.model.calls) == expected_calls
    finally:
        await engine.shutdown()
        services.close()
