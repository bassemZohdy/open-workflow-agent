"""Child-route admission preserves completion, effects, and scope boundaries."""

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
from test_state_replay_api import client_for, wait_terminal

pytest.importorskip("langgraph.graph")
from open_workflow_agent_langgraph import LangGraphWorkflowEngine


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("kind", ["replay", "branch"])
@pytest.mark.parametrize("scenario", ["catchup", "reordered", "scope_exit", "scope_exit_readonly"])
async def test_child_route_admission_preserves_scope_boundaries(
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
    scope_exit = scenario.startswith("scope_exit")
    if scope_exit:
        source["do"] = [
            {"start": {"set": {"value": 0}, "then": "nested"}},
            {"never": {"set": {"value": -1}}},
            {
                "nested": {
                    "do": [
                        {"effect": {"call": "agent:1.0.0@default", "then": "exit"}},
                        {"never_child": {"set": {"value": -1}}},
                    ]
                }
            },
            {"change": {"set": {"value": 1}}},
            {"after": {"call": "agent:1.0.0@default"}},
        ]
    policy = "read_only" if scenario == "scope_exit_readonly" else "reexecute"
    task_reference = "/do/3/change" if scope_exit else "/do/1/change"
    extensions = [
        {"name": "langgraph.state.read", "version": "1.0.0"},
        {"name": "langgraph.state." + kind, "version": "1.0.0"},
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
            "allowedTaskRefs": ["/do/3" if scope_exit else "/do/1"],
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
                if item["root_task_visits"]
                == (
                    {"/do/0/start": 1, "/do/2/nested": 1, "/do/3/change": 1}
                    if scope_exit
                    else {"/do/0/nested": 1, "/do/1/change": 1}
                )
            )
            body = {
                "reference": selected["reference"],
                "acknowledge_reexecution": policy == "reexecute",
            }
            if kind == "branch":
                body.update({"task_reference": task_reference, "values": {"value": 7}})
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
                acknowledge_reexecution=policy == "reexecute",
                task_reference=task_reference if kind == "branch" else None,
                values={"value": 7} if kind == "branch" else None,
            )
            visits = {
                item.occurrence: json.loads(json.loads(item.native_json)["snapshot"])["task_visits"]
                for item in evidence.snapshots
                if item.reference == ("/do/2/nested/do" if scope_exit else "/do/0/nested/do")
            }
            if scenario == "catchup":
                for task in ("repeat", "again"):
                    reference = f"/do/0/nested/do/{1 if task == 'repeat' else 2}/{task}"
                    assert visits[1].get(reference, 0) == 0
                    assert visits[2].get(reference, 0) == 2
            elif scenario == "reordered":
                for index, task in enumerate(("first", "middle", "finish")):
                    assert visits[1].get(f"/do/0/nested/do/{index}/{task}") == 1
            else:
                assert visits[1].get("/do/2/nested/do/0/effect") == 1
                assert visits[1].get("/do/2/nested/do/1/never_child", 0) == 0
            route = "state-replays" if kind == "replay" else "state-branches"
            before = services.invocations.connection.execute(
                "SELECT COUNT(*) FROM invocations"
            ).fetchone()[0]
            response = await client.post(
                f"/v1/invocations/{handle.invocation_id}/{route}",
                json=body,
                headers={"Idempotency-Key": "catch-up"},
            )
            if scope_exit:
                assert response.status_code == 202, response.text
                target = await wait_terminal(
                    services, response.json()["invocation_id"], timeout=180
                )
                assert target.status == ("faulted" if policy == "read_only" else "completed")
                assert len(services.model.calls) == expected_calls + (policy == "reexecute")
                repeated = await client.post(
                    f"/v1/invocations/{handle.invocation_id}/{route}",
                    json=body,
                    headers={"Idempotency-Key": "catch-up"},
                )
                assert repeated.status_code == 202
                assert repeated.json()["invocation_id"] == target.invocation_id
                assert len(services.model.calls) == expected_calls + (policy == "reexecute")
                return
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
