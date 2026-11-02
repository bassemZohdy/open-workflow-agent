"""Lineage from an attested state update retains its historical data and proof."""

import asyncio
import json
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from native_state_test_support import authority, configuration
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import (
    NativeStateBranchConfig,
    NativeStateReplayConfig,
    NativeStateUpdateConfig,
)
from open_workflow_agent.errors import UnsupportedWorkflowFeature
from open_workflow_agent.execution_authority import authority_scope
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow, generate_default_workflow
from test_state_replay_api import client_for

pytest.importorskip("langgraph.graph")
from open_workflow_agent_langgraph import LangGraphWorkflowEngine


async def stop_committed_target_update(tmp_path, config, target, body, backend, key):
    settings = tmp_path / "target-update-worker.json"
    ownership_seconds = 30 if backend == "postgresql" else 12
    settings.write_text(
        json.dumps(
            {
                "config": config.model_dump(mode="json", exclude={"sandbox"}),
                "root": str(tmp_path),
                "target": target,
                "body": body,
                "key": key,
                "ownership_seconds": ownership_seconds,
            }
        ),
        encoding="utf-8",
    )
    environment = os.environ.copy()
    if backend == "sqlite":
        environment.pop("OWA_TEST_POSTGRES_URL", None)
    process = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).with_name("lineage_target_update_worker.py")),
            str(settings),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=environment,
    )
    lines = []
    try:

        async def wait_for_commit():
            while True:
                line = await asyncio.to_thread(process.stdout.readline)
                if not line:
                    raise AssertionError("worker exited before native commit: " + "".join(lines))
                lines.append(line)
                if line.startswith("TARGET_UPDATE_COMMITTED"):
                    return tuple(line.strip().split()[1:])

        operation_id = await asyncio.wait_for(wait_for_commit(), timeout=180)
    finally:
        if process.poll() is None:
            process.kill()
            await asyncio.to_thread(process.wait, timeout=10)
        process.stdout.close()
    await asyncio.sleep(ownership_seconds + 1)
    return operation_id


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("kind", ["replay", "branch"])
@pytest.mark.parametrize("policy", ["read_only", "reexecute"])
@pytest.mark.parametrize("target_update", [False, True])
@pytest.mark.parametrize("nested_scope", [False, True])
async def test_lineage_from_updated_waiting_checkpoint(
    tmp_path, monkeypatch, backend, kind, policy, target_update, nested_scope
):
    if nested_scope and (not target_update or policy != "reexecute"):
        pytest.skip("nested scope coverage uses an updated reexecuting target")
    for actor in ("alice", "bob"):
        monkeypatch.setenv("OWA_NATIVE_STREAM_TEST_" + actor.upper(), "replay-state-" + actor)
    config = configuration(timeout_seconds=30)
    config.execution_authorization.policy.rules[0].actions.extend(
        ["state.update", "state.resolve", "state." + kind]
    )
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
                    "allowedTaskRefs": ["/do/0", "/do/2"] if nested_scope else ["/do/0"],
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
    if nested_scope:
        owa = source["document"]["metadata"]["owa"]
        owa["nativeExtensions"].append({"name": "langgraph.channels", "version": "1.0.0"})
        owa["nativeExtensionData"]["langgraph.channels@1.0.0"] = {
            "channels": [
                {
                    "id": "prior",
                    "reducer": "last_value",
                    "initial": None,
                    "writes": [{"task": "/do/0"}],
                }
            ],
            "reads": [],
        }
    source["do"] = [
        {"first": {"set": {"value": 1}}},
        *(
            [
                {"group": {"do": [{"child": {"set": {"value": "${ .value }"}}}]}},
                {"middle": {"set": {"value": "${ .value }"}}},
            ]
            if nested_scope
            else []
        ),
        {"pause": {"wait": {"seconds": 300}}},
        {"last": {"set": {"value": "${ .value + 1 }"}}},
    ]
    update_task = "/do/2/middle" if nested_scope else "/do/0/first"
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
                    "task_reference": update_task,
                    "values": {"value": 6},
                },
                headers={"Idempotency-Key": "update"},
            )
            assert updated.status_code == 200, updated.text
            source_control = updated.json()["operation_id"]
            current = await client.get(path + "/state")
            assert current.status_code == 200, current.text
            second = await client.post(
                path + "/state-updates",
                json={
                    "base_reference": current.json()["items"][0]["reference"],
                    "task_reference": update_task,
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
                body.update({"task_reference": update_task, "values": {"value": 9}})
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
            updated_descendant = None
            if target_update:
                target_path = f"/v1/invocations/{target}"
                first_target_control = ""
                for value in (19, 20):
                    target_state = await client.get(target_path + "/state")
                    assert target_state.status_code == 200, target_state.text
                    target_update_body = {
                        "base_reference": target_state.json()["items"][0]["reference"],
                        "task_reference": update_task,
                        "values": {"value": value},
                    }
                    stopped = None
                    if value == 20 and policy == "reexecute" and not nested_scope:
                        stopped = await stop_committed_target_update(
                            tmp_path,
                            config,
                            target,
                            target_update_body,
                            backend,
                            f"target-update-{value}",
                        )
                        stopped_operation, request_digest = stopped
                        connection = services.effect_journal.connection
                        source_proof = connection.execute(
                            "SELECT proof_json FROM state_update_intents_v1 WHERE identity=?",
                            (source_control,),
                        ).fetchone()[0]
                        resolution = {
                            "request_digest": request_digest,
                            "decision": "complete",
                            "acknowledge_unknown": False,
                        }
                        resolve_path = target_path + f"/state-updates/{stopped_operation}/resolve"
                        acquire = engine._own_execution

                        @asynccontextmanager
                        async def corrupt_after_acquisition(
                            active,
                            acquire=acquire,
                            connection=connection,
                            source_control=source_control,
                        ):
                            async with acquire(active):
                                connection.execute(
                                    "UPDATE state_update_intents_v1 SET proof_json=? "
                                    "WHERE identity=?",
                                    ("{}", source_control),
                                )
                                connection.commit()
                                yield

                        with monkeypatch.context() as context:
                            context.setattr(engine, "_own_execution", corrupt_after_acquisition)
                            denied_resolution = await client.post(
                                resolve_path,
                                json=resolution,
                                headers={"Idempotency-Key": "target-resolve"},
                            )
                        assert denied_resolution.status_code >= 400, denied_resolution.text
                        connection.execute(
                            "UPDATE state_update_intents_v1 SET proof_json=? WHERE identity=?",
                            (source_proof, source_control),
                        )
                        connection.commit()
                        assert first_target_control
                        prior_proof = connection.execute(
                            "SELECT proof_json FROM state_update_intents_v1 WHERE identity=?",
                            (first_target_control,),
                        ).fetchone()[0]
                        connection.execute(
                            "UPDATE state_update_intents_v1 SET proof_json=? WHERE identity=?",
                            ("{}", first_target_control),
                        )
                        connection.commit()
                        denied_prior = await client.post(
                            resolve_path,
                            json=resolution,
                            headers={"Idempotency-Key": "target-resolve"},
                        )
                        assert denied_prior.status_code >= 400, denied_prior.text
                        connection.execute(
                            "UPDATE state_update_intents_v1 SET proof_json=? WHERE identity=?",
                            (prior_proof, first_target_control),
                        )
                        connection.commit()
                        resolved = await client.post(
                            resolve_path,
                            json=resolution,
                            headers={"Idempotency-Key": "target-resolve"},
                        )
                        assert resolved.status_code == 200, resolved.text
                    changed = await client.post(
                        target_path + "/state-updates",
                        json=target_update_body,
                        headers={"Idempotency-Key": f"target-update-{value}"},
                    )
                    assert changed.status_code == 200, changed.text
                    if stopped is not None:
                        assert changed.json()["operation_id"] == stopped[0]
                    if value == 19:
                        first_target_control = changed.json()["operation_id"]
                target_state = await client.get(target_path + "/state")
                assert target_state.status_code == 200, target_state.text
                if nested_scope:
                    assert target_state.json()["items"][0]["channels"] == {"prior": {"value": 1}}
                target_reference = target_state.json()["items"][0]["reference"]
                connection = services.effect_journal.connection
                identity, proof = connection.execute(
                    "SELECT identity,proof_json FROM state_update_intents_v1 "
                    "WHERE identity=? AND invocation_id=?",
                    (first_target_control, target),
                ).fetchone()
                connection.execute(
                    "UPDATE state_update_intents_v1 SET proof_json=? WHERE identity=?",
                    ("{}", identity),
                )
                connection.commit()
                before_controls = connection.execute(
                    "SELECT COUNT(*) FROM state_update_intents_v1 WHERE invocation_id=?", (target,)
                ).fetchone()[0]
                damaged = await client.post(
                    target_path + "/state-updates",
                    json={
                        "base_reference": target_reference,
                        "task_reference": update_task,
                        "values": {"value": 22},
                    },
                    headers={"Idempotency-Key": "damaged-target-proof"},
                )
                assert damaged.status_code == 409, damaged.text
                assert (
                    connection.execute(
                        "SELECT COUNT(*) FROM state_update_intents_v1 WHERE invocation_id=?",
                        (target,),
                    ).fetchone()[0]
                    == before_controls
                )
                connection.execute(
                    "UPDATE state_update_intents_v1 SET proof_json=? WHERE identity=?",
                    (proof, identity),
                )
                connection.commit()
                if policy == "reexecute" and not nested_scope:
                    descendant_body = {
                        "reference": target_reference,
                        "acknowledge_reexecution": True,
                    }
                    if kind == "branch":
                        descendant_body.update(
                            {"task_reference": update_task, "values": {"value": 17}}
                        )
                    descendant = await client.post(
                        f"/v1/invocations/{target}{route}",
                        json=descendant_body,
                        headers={"Idempotency-Key": "updated-descendant"},
                    )
                    assert descendant.status_code == 202, descendant.text
                    updated_descendant = descendant.json()["invocation_id"]
                    async with asyncio.timeout(60):
                        while services.invocations.get(updated_descendant).status not in {
                            "waiting",
                            "faulted",
                            "completed",
                        }:
                            await asyncio.sleep(0.1)
                    assert services.invocations.get(updated_descendant).status == "waiting"
                    descendant_result = await client.post(
                        f"/v1/invocations/{updated_descendant}/resume", json={"input": None}
                    )
                    assert descendant_result.status_code == 200, descendant_result.text
                    assert descendant_result.json()["status"] == "completed", descendant_result.text
                    assert descendant_result.json()["output"] == {
                        "value": 21 if kind == "replay" else 18
                    }
            coordinator = getattr(services, "native_" + kind + "_controller").coordinator
            invoke = coordinator.run if kind == "replay" else coordinator.run_branch
            interrupted = []
            command = {
                "request_id": uuid4().hex * 2,
                "source_reference": reference,
                "acknowledge_reexecution": policy == "reexecute",
            }
            if kind == "branch":
                command.update({"task_reference": update_task, "values": {"value": 9}})

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
                    "task_reference": update_task,
                    "values": {"value": 12},
                },
                headers={"Idempotency-Key": "later-update"},
            )
            assert later.status_code == 200, later.text
            if interrupted:

                def unsupported(_plan):
                    raise UnsupportedWorkflowFeature("required capability unavailable")

                with monkeypatch.context() as context:
                    context.setattr(engine, "_verify_workflow_capabilities", unsupported)
                    with pytest.raises(UnsupportedWorkflowFeature):
                        await invoke(handle, authority(services, "alice"), **command)
                assert services.invocations.get(interrupted[0]).status == "running"
                recovered = await invoke(handle, authority(services, "alice"), **command)
                assert recovered.invocation_id == interrupted[0]
                assert recovered.status == "waiting"
            if target_update:
                fresh_services = RuntimeServices(
                    config,
                    model=FakeModel(),
                    database_root=None if backend == "postgresql" else tmp_path,
                )
                fresh_engine = LangGraphWorkflowEngine()
                await fresh_engine.initialize(fresh_services)
                await fresh_engine._timer_dispatcher.stop()
                try:
                    async with client_for((fresh_services, fresh_engine)) as fresh_client:
                        resumed = await fresh_client.post(
                            f"/v1/invocations/{target}/resume", json={"input": None}
                        )
                finally:
                    await fresh_engine.shutdown()
                    fresh_services.close()
            else:
                resumed = await client.post(
                    f"/v1/invocations/{target}/resume", json={"input": None}
                )
            assert resumed.status_code == 200, resumed.text
            assert resumed.json()["status"] == "completed", resumed.text
            assert resumed.json()["output"] == {
                "value": 21 if target_update else 8 if kind == "replay" else 10
            }
            if target_update:
                update_retry = await client.post(
                    target_path + "/state-updates",
                    json=target_update_body,
                    headers={"Idempotency-Key": "target-update-20"},
                )
                assert update_retry.status_code == 200, update_retry.text
                connection.execute(
                    "UPDATE state_update_intents_v1 SET proof_json=? WHERE identity=?",
                    ("{}", identity),
                )
                connection.commit()
                damaged_retry = await client.post(
                    target_path + "/state-updates",
                    json=target_update_body,
                    headers={"Idempotency-Key": "target-update-20"},
                )
                assert damaged_retry.status_code >= 400, damaged_retry.text
                connection.execute(
                    "UPDATE state_update_intents_v1 SET proof_json=? WHERE identity=?",
                    (proof, identity),
                )
                connection.commit()
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
                    if nested_scope:
                        assert restored.json()["items"][0]["channels"] == {"prior": {"value": 1}}
                    retry = await fresh.post(
                        path + route, json=body, headers={"Idempotency-Key": "lineage"}
                    )
                    assert retry.status_code == 202, retry.text
                    assert retry.json()["invocation_id"] == target
            finally:
                await restarted.shutdown()
                reopened.close()
            guarded_target = None
            guarded_reference = None
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
                if target_update:
                    guarded_view = await client.get(f"/v1/invocations/{guarded_target}/state")
                    assert guarded_view.status_code == 200, guarded_view.text
                    guarded_reference = guarded_view.json()["items"][0]["reference"]
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
            if updated_descendant is not None:
                descendant_view = await client.get(f"/v1/invocations/{updated_descendant}/states")
                assert descendant_view.status_code != 200, descendant_view.text
            if target_update:
                denied_retry = await client.post(
                    target_path + "/state-updates",
                    json=target_update_body,
                    headers={"Idempotency-Key": "target-update-20"},
                )
                assert denied_retry.status_code >= 400, denied_retry.text
            if guarded_target is not None:
                if guarded_reference is not None:
                    denied_update = await client.post(
                        f"/v1/invocations/{guarded_target}/state-updates",
                        json={
                            "base_reference": guarded_reference,
                            "task_reference": update_task,
                            "values": {"value": 24},
                        },
                        headers={"Idempotency-Key": "damaged-source-proof"},
                    )
                    assert denied_update.status_code >= 400, denied_update.text
                    assert (
                        services.effect_journal.connection.execute(
                            "SELECT COUNT(*) FROM state_update_intents_v1 WHERE invocation_id=?",
                            (guarded_target,),
                        ).fetchone()[0]
                        == 0
                    )
                denied_resume = await client.post(
                    f"/v1/invocations/{guarded_target}/resume", json={"input": None}
                )
                assert denied_resume.status_code >= 400 or (
                    denied_resume.json()["status"] == "faulted"
                ), denied_resume.text
    finally:
        await engine.shutdown()
        services.close()
