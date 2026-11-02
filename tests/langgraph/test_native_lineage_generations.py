"""Public lineage generations preserve effects and proofs from every ancestor."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from native_state_test_support import authority, configuration
from open_workflow_agent import lineage_registry as lineage_module
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import (
    NativeStateBranchConfig,
    NativeStateConfig,
    NativeStateReplayConfig,
    NativeStateUpdateConfig,
)
from open_workflow_agent.effects import EffectIdentity
from open_workflow_agent.execution_authority import authority_scope
from open_workflow_agent.lineage_registry import lineage_identity
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow, generate_default_workflow
from test_state_replay_api import client_for, wait_terminal

pytest.importorskip("langgraph.graph")
from open_workflow_agent_langgraph import LangGraphWorkflowEngine


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("first_kind", ["replay", "branch"])
@pytest.mark.parametrize("second_kind", ["replay", "branch"])
async def test_lineage_generation_retains_ancestor_effects(
    tmp_path, monkeypatch, backend, first_kind, second_kind
):
    for actor in ("alice", "bob"):
        monkeypatch.setenv("OWA_NATIVE_STREAM_TEST_" + actor.upper(), "replay-state-" + actor)
    config = configuration(timeout_seconds=30)
    config.execution_authorization.policy.rules[0].actions.extend(
        ["state.replay", "state.branch", "state.update"]
    )
    bounds = NativeStateConfig(max_native_bytes=4_194_304, timeout_seconds=30)
    config.native_state = bounds
    config.native_state_updates = NativeStateUpdateConfig(bounds=bounds)
    config.native_state_replay = NativeStateReplayConfig(bounds=bounds)
    config.native_state_branch = NativeStateBranchConfig(bounds=bounds)
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
    source["document"]["name"] = "lineage-generation-" + uuid4().hex
    source["do"] = [
        {"first": {"call": "agent:1.0.0@default"}},
        {"second": {"set": {"value": 1}}},
        {"third": {"call": "agent:1.0.0@default"}},
        {"fourth": {"set": {"value": 2}}},
        {"fifth": {"call": "agent:1.0.0@default"}},
    ]
    source["document"]["metadata"] = {
        "owa": {
            "nativeExtensions": [
                {"name": "langgraph.state.read", "version": "1.0.0"},
                {"name": "langgraph.state.update", "version": "1.0.0"},
                {"name": "langgraph.state.replay", "version": "1.0.0"},
                {"name": "langgraph.state.branch", "version": "1.0.0"},
            ],
            "nativeExtensionData": {
                "langgraph.state.update@1.0.0": {
                    "allowedTaskRefs": ["/do/1", "/do/3"],
                    "updateSchema": {
                        "type": "object",
                        "properties": {"value": {"type": "integer"}},
                        "required": ["value"],
                        "additionalProperties": False,
                    },
                    "authorizationPolicyRef": config.execution_authorization.id,
                },
                "langgraph.state.replay@1.0.0": {
                    "authorizationPolicyRef": config.execution_authorization.id,
                    "replayEffectPolicy": "reexecute",
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
            result = await engine.invoke(plan, handle, {})
        assert result.status == "completed", result.error
        assert len(services.model.calls) == 3
        async with client_for((services, engine)) as client:

            async def select(invocation, count):
                history = await client.get(f"/v1/invocations/{invocation}/states")
                assert history.status_code == 200, history.text
                expected = {
                    f"/do/{i}/{name}": 1
                    for i, name in enumerate(("first", "second", "third", "fourth")[:count])
                }
                return next(
                    item["reference"]
                    for item in history.json()["items"]
                    if item["root_task_visits"] == expected
                )

            async def fork(invocation, kind, reference, task, key):
                body = {"reference": reference, "acknowledge_reexecution": True}
                if kind == "branch":
                    body.update({"task_reference": task, "values": {"value": 7}})
                route = "state-replays" if kind == "replay" else "state-branches"
                response = await client.post(
                    f"/v1/invocations/{invocation}/{route}",
                    json=body,
                    headers={"Idempotency-Key": key},
                )
                assert response.status_code == 202, response.text
                target = response.json()["invocation_id"]
                finished = await wait_terminal(services, target, timeout=300)
                assert finished.status == "completed", finished.error
                repeated = await client.post(
                    f"/v1/invocations/{invocation}/{route}",
                    json=body,
                    headers={"Idempotency-Key": key},
                )
                assert repeated.status_code == 202, repeated.text
                assert repeated.json()["invocation_id"] == target
                return target

            first_ref = await select(handle.invocation_id, 2)
            parent = await fork(
                handle.invocation_id, first_kind, first_ref, "/do/1/second", "first"
            )
            assert len(services.model.calls) == 5
            second_ref = await select(parent, 4)
            child = await fork(parent, second_kind, second_ref, "/do/3/fourth", "second")
            assert len(services.model.calls) == 6
            third_ref = await select(child, 4)
            descendant = await fork(child, first_kind, third_ref, "/do/3/fourth", "third")
            assert len(services.model.calls) == 7
            if first_kind == "replay" and second_kind == "replay":
                descendant_ref = await select(descendant, 4)
                before = services.invocations.connection.execute(
                    "SELECT COUNT(*) FROM invocations"
                ).fetchone()[0]
                with monkeypatch.context() as context:
                    context.setattr(lineage_module, "MAX_LINEAGE_DEPTH", 3)
                    too_deep = await client.post(
                        f"/v1/invocations/{descendant}/state-replays",
                        json={"reference": descendant_ref, "acknowledge_reexecution": True},
                        headers={"Idempotency-Key": "over-depth"},
                    )
                assert too_deep.status_code == 409, too_deep.text
                assert (
                    services.invocations.connection.execute(
                        "SELECT COUNT(*) FROM invocations"
                    ).fetchone()[0]
                    == before
                )
            rows = services.effect_journal.connection.execute(
                "SELECT identity,payload FROM workflow_effect_invocations WHERE invocation_id=?",
                (handle.invocation_id,),
            ).fetchall()
            first_receipt = next(
                identity
                for identity, raw in rows
                if (entry := EffectIdentity.from_value(json.loads(raw))).task_reference
                == "/do/0/first"
                and entry.operation.startswith("call:")
            )
            parent_rows = services.effect_journal.connection.execute(
                "SELECT identity,payload FROM workflow_effect_invocations WHERE invocation_id=?",
                (parent,),
            ).fetchall()
            parent_receipt = next(
                identity
                for identity, raw in parent_rows
                if (entry := EffectIdentity.from_value(json.loads(raw))).task_reference
                == "/do/2/third"
                and entry.operation.startswith("call:")
            )
            parent_marker = lineage_identity(services.invocations.get(parent)).key
            connection = services.effect_journal.connection
            for identity in (first_receipt, parent_receipt, parent_marker):
                original = connection.execute(
                    "SELECT payload FROM workflow_effects WHERE identity=?", (identity,)
                ).fetchone()[0]
                connection.execute(
                    "UPDATE workflow_effects SET payload=? WHERE identity=?", ("{}", identity)
                )
                connection.commit()
                damaged = await client.get(f"/v1/invocations/{descendant}/states")
                assert damaged.status_code != 200, damaged.text
                before = services.invocations.connection.execute(
                    "SELECT COUNT(*) FROM invocations"
                ).fetchone()[0]
                rejected = await client.post(
                    f"/v1/invocations/{child}/state-replays",
                    json={"reference": third_ref, "acknowledge_reexecution": True},
                    headers={"Idempotency-Key": "damaged-" + identity[:16]},
                )
                assert rejected.status_code >= 400, rejected.text
                assert (
                    services.invocations.connection.execute(
                        "SELECT COUNT(*) FROM invocations"
                    ).fetchone()[0]
                    == before
                )
                connection.execute(
                    "UPDATE workflow_effects SET payload=? WHERE identity=?", (original, identity)
                )
                connection.commit()
                restored = await client.get(f"/v1/invocations/{descendant}/states")
                assert restored.status_code == 200, restored.text
            if first_kind == "replay" and second_kind == "replay":
                environment = os.environ.copy()
                if backend == "sqlite":
                    environment.pop("OWA_TEST_POSTGRES_URL", None)
                for phase in ("forked", "effect"):
                    key = "generation-stop-" + phase
                    process = subprocess.Popen(
                        [
                            sys.executable,
                            str(Path(__file__).with_name("native_lineage_generation_worker.py")),
                            str(tmp_path),
                            child,
                            key,
                            third_ref,
                            phase,
                        ],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        env=environment,
                    )
                    output = []
                    try:

                        async def read_phase(active_process=process, lines=output):
                            while True:
                                line = await asyncio.to_thread(active_process.stdout.readline)
                                if not line:
                                    raise AssertionError(
                                        "worker exited before stop: " + "".join(lines)
                                    )
                                lines.append(line)
                                if line.startswith("GENERATION_PHASE "):
                                    return line.strip().split()

                        marker = await asyncio.wait_for(read_phase(), timeout=300)
                        assert marker[1] == phase
                    finally:
                        if process.poll() is None:
                            process.kill()
                            await asyncio.to_thread(process.wait, timeout=10)
                        process.stdout.close()
                    await asyncio.sleep(61)
                    before = len(services.model.calls)
                    path = f"/v1/invocations/{child}/state-replays"
                    body = {"reference": third_ref, "acknowledge_reexecution": True}
                    resumed = await client.post(path, json=body, headers={"Idempotency-Key": key})
                    assert resumed.status_code == 202, resumed.text
                    assert resumed.json()["invocation_id"] == marker[2]
                    recovered = await wait_terminal(services, marker[2], timeout=300)
                    assert recovered.status == ("faulted" if phase == "effect" else "completed")
                    assert len(services.model.calls) == before + (phase == "forked")
                    repeated = await client.post(path, json=body, headers={"Idempotency-Key": key})
                    assert repeated.status_code == 202, repeated.text
                    assert repeated.json()["invocation_id"] == marker[2]
                    assert len(services.model.calls) == before + (phase == "forked")
    finally:
        await engine.shutdown()
        services.close()
