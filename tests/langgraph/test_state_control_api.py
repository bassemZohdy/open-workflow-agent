"""Configured HTTP controls operate real OWA checkpoints without SDK identities."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import yaml
from native_state_test_support import authority, configuration
from open_workflow_agent.api import create_app
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import NativeStateUpdateConfig, RuntimeConfig
from open_workflow_agent.execution_authority import authority_scope
from open_workflow_agent.native_state import NativeStateUnavailable
from open_workflow_agent.native_state_policy import CAPABILITY
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow, generate_default_workflow

pytest.importorskip("langgraph")
from open_workflow_agent_langgraph import LangGraphWorkflowEngine
from open_workflow_agent_langgraph.native_state_transition import AttestedStateControls
from open_workflow_agent_langgraph.state_control import LangGraphStateController


def workflow(config):
    source = generate_default_workflow()
    source["document"]["name"] = "state-control-" + uuid4().hex
    source["document"]["metadata"] = {
        "owa": {
            "nativeExtensions": [
                {"name": "langgraph.state.read", "version": "1.0.0"},
                {"name": "langgraph.state.update", "version": "1.0.0"},
            ],
            "nativeExtensionData": {
                CAPABILITY: {
                    "allowedTaskRefs": ["/do/0"],
                    "updateSchema": {
                        "type": "object",
                        "properties": {"value": {"type": "integer"}},
                        "required": ["value"],
                        "additionalProperties": False,
                    },
                    "authorizationPolicyRef": config.execution_authorization.id,
                }
            },
        }
    }
    source["do"] = [
        {"first": {"set": {"value": 1}}},
        {"pause": {"wait": {"seconds": 300}}},
        {"last": {"set": {"value": "${ .value + 1 }"}}},
    ]
    return compile_workflow(source)


@pytest.fixture(params=["sqlite", "postgresql"])
async def controls_runtime(request, tmp_path, monkeypatch):
    for actor in ("alice", "bob"):
        monkeypatch.setenv("OWA_NATIVE_STREAM_TEST_" + actor.upper(), "controlled-state-" + actor)
    backend, options = request.param if isinstance(request.param, tuple) else (request.param, {})
    config = configuration()
    config.execution_authorization.policy.rules[0].actions.extend(["state.update", "state.resolve"])
    config.native_state_updates = NativeStateUpdateConfig(**options)
    if backend == "postgresql":
        datasource = os.getenv("OWA_TEST_POSTGRES_URL")
        if not datasource:
            pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
        config.persistence.datasource = datasource
    services = RuntimeServices(
        config, model=FakeModel(), database_root=None if backend == "postgresql" else tmp_path
    )
    engine = LangGraphWorkflowEngine()
    await engine.initialize(services)
    await engine._timer_dispatcher.stop()
    plan = workflow(config)
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
    with authority_scope(authority(services, "alice")):
        result = await engine.invoke(plan, handle, {})
    assert result.status == "waiting", result.error
    try:
        yield services, engine, handle, plan
    finally:
        await engine.shutdown()
        services.close()


def client_for(runtime, actor="alice"):
    services, engine, *_ = runtime
    config = services.config.model_copy(deep=True)
    config.server.api_security_profile = "actor-" + actor
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=create_app(config=config, services=services, engine=engine)
        ),
        base_url="http://test",
        headers={"Authorization": "Bearer controlled-state-" + actor},
    )


async def command(client, handle, value=7):
    current = await client.get(f"/v1/invocations/{handle.invocation_id}/state")
    assert current.status_code == 200, current.text
    return {
        "base_reference": current.json()["items"][0]["reference"],
        "task_reference": "/do/0/first",
        "values": {"value": value},
    }


@pytest.mark.asyncio
async def test_public_update_read_resume_and_terminal_idempotency(controls_runtime):
    services, engine, handle, plan = controls_runtime
    path = f"/v1/invocations/{handle.invocation_id}"
    async with client_for(controls_runtime) as client:
        capabilities = (await client.get("/v1/capabilities")).json()
        assert CAPABILITY in capabilities["nativeExtensions"]
        assert capabilities["features"]["nativeStateUpdates"]["profile"] == CAPABILITY
        body = await command(client, handle)
        result = await client.post(
            path + "/state-updates", json=body, headers={"Idempotency-Key": "update-1"}
        )
        assert result.status_code == 200, result.text
        assert result.json()["state"] == "completed"
        assert "checkpoint_id" not in result.text and "snapshot" not in result.text
        again = await client.post(
            path + "/state-updates", json=body, headers={"Idempotency-Key": "update-1"}
        )
        assert again.json() == result.json()
        current = await client.get(path + "/state")
        assert current.status_code == 200, current.text
        assert current.json()["items"][0]["data"] == {"value": 7}
        entries = await client.get(path + "/state-updates")
        assert entries.json()["verification"] == "common_metadata_only"
        assert len(entries.json()["updates"]) == 1
        resumed = await client.post(path + "/resume", json={"input": None})
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["status"] == "completed", resumed.text
        assert resumed.json()["output"] == {"value": 8}
        terminal = await client.post(
            path + "/state-updates", json=body, headers={"Idempotency-Key": "update-1"}
        )
        assert terminal.status_code == 200, terminal.text
        assert terminal.json() == result.json()
        generation = services.effect_journal.connection.execute(
            "SELECT generation FROM invocation_ownership WHERE invocation_id=?",
            (handle.invocation_id,),
        ).fetchone()[0]
        rejected = await client.post(
            path + "/state-updates", json=body, headers={"Idempotency-Key": "terminal-new"}
        )
        assert rejected.status_code == 409, rejected.text
        assert (
            services.effect_journal.connection.execute(
                "SELECT generation FROM invocation_ownership WHERE invocation_id=?",
                (handle.invocation_id,),
            ).fetchone()[0]
            == generation
        )
    assert services.model.calls == []
    assert engine.state_controller.bindings == {}
    assert engine.state_controller.coordinator._backends == {}


@pytest.mark.asyncio
async def test_owner_schema_attribution_and_conflicting_idempotency_fail_before_mutation(
    controls_runtime,
):
    services, _engine, handle, _plan = controls_runtime
    path = f"/v1/invocations/{handle.invocation_id}/state-updates"
    async with client_for(controls_runtime) as client:
        body = await command(client, handle)
        for change in (
            {"values": {"value": "bad"}},
            {"task_reference": "/do/2/last"},
            {"base_reference": "0" * 64},
            {"private_checkpoint": "forbidden"},
        ):
            response = await client.post(
                path, json=body | change, headers={"Idempotency-Key": "rejected"}
            )
            assert response.status_code in {400, 409, 422}, response.text
        assert (await client.get(path)).json()["updates"] == []
        accepted = await client.post(path, json=body, headers={"Idempotency-Key": "one"})
        assert accepted.status_code == 200, accepted.text
        changed = await client.post(
            path, json=body | {"values": {"value": 9}}, headers={"Idempotency-Key": "one"}
        )
        assert changed.status_code == 409, changed.text
    async with client_for(controls_runtime, "bob") as client:
        hidden = await client.post(path, json=body, headers={"Idempotency-Key": "different"})
        assert hidden.status_code == 404, hidden.text
    assert services.model.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("committed", [False, True])
async def test_http_resolves_retained_proof_or_unknown_outcome_without_native_redispatch(
    controls_runtime, committed, monkeypatch
):
    services, engine, handle, plan = controls_runtime
    calls = []
    original = AttestedStateControls.apply

    async def interrupted(bridge, request, handle):
        calls.append(request.identity.key)
        if committed:
            await original(bridge, request, handle)
        raise RuntimeError("interrupted native control")

    monkeypatch.setattr(AttestedStateControls, "apply", interrupted)
    path = f"/v1/invocations/{handle.invocation_id}/state-updates"
    async with client_for(controls_runtime) as client:
        body = await command(client, handle)
        response = await client.post(path, json=body, headers={"Idempotency-Key": "interrupted"})
        assert response.status_code == 409, response.text
        entry = (await client.get(path)).json()["updates"][0]
        assert response.json()["error"]["details"]["operation_id"] == entry["operation_id"]
        assert response.json()["error"]["details"]["request_digest"] == entry["request_digest"]
        resolution = {
            "request_digest": entry["request_digest"],
            "decision": "complete" if committed else "abandon_unknown",
            "acknowledge_unknown": not committed,
        }
        target = path + "/" + entry["operation_id"] + "/resolve"
        response = await client.post(
            target, json=resolution, headers={"Idempotency-Key": "resolve-1"}
        )
        assert response.status_code == 200, response.text
        again = await client.post(target, json=resolution, headers={"Idempotency-Key": "resolve-1"})
        assert again.json() == response.json()
        assert calls == [entry["operation_id"]]
        if committed:
            with authority_scope(authority(services, "alice")):
                result = await engine.resume(handle, None, plan)
            assert result.output == {"value": 8}, result.error
        else:
            rejected = await client.post(path, json=body, headers={"Idempotency-Key": "new-update"})
            assert rejected.status_code == 409, rejected.text
    assert services.model.calls == []


@pytest.mark.asyncio
async def test_fresh_process_repeats_update_and_resumes_through_http(controls_runtime, tmp_path):
    services, _engine, handle, _plan = controls_runtime
    async with client_for(controls_runtime) as client:
        body = await command(client, handle)
        response = await client.post(
            f"/v1/invocations/{handle.invocation_id}/state-updates",
            json=body,
            headers={"Idempotency-Key": "update-1"},
        )
        assert response.status_code == 200, response.text
    source, result = tmp_path / "state-controller.json", tmp_path / "state-controller-result.json"
    source.write_text(
        json.dumps(
            {
                "config": services.config.model_dump(mode="json", exclude={"sandbox"}),
                "root": str(services.database_root) if services.database_root is not None else None,
                "invocation_id": handle.invocation_id,
                "command": body,
            }
        ),
        encoding="utf-8",
    )
    process = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).with_name("state_control_api_worker.py")),
            str(source),
            str(result),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        output, _ = await asyncio.to_thread(process.communicate, timeout=60)
        assert process.returncode == 0, output.decode(errors="replace")
    finally:
        if process.poll() is None:
            process.kill()
            await asyncio.to_thread(process.communicate, timeout=10)
    observed = json.loads(result.read_text(encoding="utf-8"))
    assert observed["data"] == {"value": 7} and observed["output"] == {"value": 8}
    assert observed["update"] == response.json()
    assert observed["model_calls"] == observed["bindings"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "controls_runtime",
    [
        ("sqlite", {"bounds": {"max_concurrency": 1, "timeout_seconds": 0.05}}),
        ("postgresql", {"bounds": {"max_concurrency": 1, "timeout_seconds": 0.05}}),
    ],
    indirect=True,
)
async def test_deadline_and_repeated_cancellation_keep_capacity_until_cleanup(controls_runtime):
    services, engine, handle, _plan = controls_runtime
    controller = engine.state_controller
    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def operation():
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleaning.set()
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    continue
            raise

    task = asyncio.create_task(
        controller._run(handle, authority(services, "alice"), "state.inspect", operation)
    )
    try:
        await asyncio.wait_for(entered.wait(), 5)
        await asyncio.wait_for(cleaning.wait(), 5)
        task.cancel()
        task.cancel()
        await asyncio.sleep(0)
        assert controller._slots.locked() and not task.done()
        async with client_for(controls_runtime) as client:
            rejected = await client.get(f"/v1/invocations/{handle.invocation_id}/state-updates")
            assert rejected.status_code == 429, rejected.text
    finally:
        release.set()
        with pytest.raises((NativeStateUnavailable, asyncio.CancelledError)):
            await task
    assert not controller._slots.locked() and controller._tasks == set()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "controls_runtime",
    [
        ("sqlite", {"bounds": {"max_concurrency": 1}}),
        ("postgresql", {"bounds": {"max_concurrency": 1}}),
    ],
    indirect=True,
)
async def test_cancellation_drains_real_sdk_transaction_before_releasing_slot_and_owner(
    controls_runtime, monkeypatch
):
    services, engine, handle, _plan = controls_runtime
    controller = engine.state_controller
    entered, release = threading.Event(), threading.Event()
    original = AttestedStateControls._update_owned
    calls = []

    async def held(bridge, saver, config, request):
        calls.append(request.identity.key)
        result = await original(bridge, saver, config, request)
        entered.set()
        assert await asyncio.to_thread(release.wait, 10)
        return result

    monkeypatch.setattr(AttestedStateControls, "_update_owned", held)
    async with client_for(controls_runtime) as client:
        body = await command(client, handle)
        actor = authority(services, "alice")
        task = asyncio.create_task(controller.update(handle, actor, request_id="1" * 64, **body))
        try:
            assert await asyncio.to_thread(entered.wait, 15)
            task.cancel()
            await asyncio.sleep(0.02)
            task.cancel()
            assert controller._slots.locked() and not task.done()
            assert handle._execution_lease is not None
            probe = await client.get(f"/v1/invocations/{handle.invocation_id}/state-updates")
            assert probe.status_code == 429, probe.text
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert handle._execution_lease is None and not controller._slots.locked()
        retry = await controller.update(handle, actor, request_id="1" * 64, **body)
        assert retry["state"] == "completed" and calls == [retry["operation_id"]]
        assert controller.bindings == {} and controller.coordinator._backends == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["permission", "binding"])
async def test_withdrawn_policy_or_binding_rejects_http_update_before_control_intent(
    controls_runtime, change
):
    services, _engine, handle, _plan = controls_runtime
    async with client_for(controls_runtime) as client:
        body = await command(client, handle)
        if change == "permission":
            services.config.execution_authorization.policy.rules[0].actions.remove("state.update")
        else:
            services.config.native_state_updates = None
        response = await client.post(
            f"/v1/invocations/{handle.invocation_id}/state-updates",
            json=body,
            headers={"Idempotency-Key": "denied"},
        )
        assert response.status_code in {400, 403, 409, 422}, response.text
        assert services.native_state_controller.coordinator.requests(handle) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_configuration_only_app_invokes_updates_and_resumes_with_public_requests(
    tmp_path, monkeypatch, backend
):
    monkeypatch.setenv("OWA_NATIVE_STATE_TOKEN", "controlled-state-owner")
    examples = Path(__file__).resolve().parents[2] / "examples"
    config = RuntimeConfig.model_validate(
        yaml.safe_load((examples / "native-state-updates.yaml").read_text(encoding="utf-8"))
    )
    if backend == "postgresql":
        datasource = os.getenv("OWA_TEST_POSTGRES_URL")
        if not datasource:
            pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
        config.persistence.datasource = datasource
    mounted = tmp_path / "native-state-updates-workflow.yaml"
    mounted.write_bytes((examples / mounted.name).read_bytes())
    config.workflow.path = str(mounted)
    config.knowledge.reload.mode = "manual"
    services = RuntimeServices(
        config, model=FakeModel(), database_root=tmp_path if backend == "sqlite" else None
    )
    engine = LangGraphWorkflowEngine()
    app = create_app(config=config, services=services, engine=engine)
    try:
        async with app.router.lifespan_context(app):
            await engine._timer_dispatcher.stop()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer controlled-state-owner"},
            ) as client:
                created = await client.post("/v1/invoke", json={"input": {}})
                assert created.status_code == 200, created.text
                assert created.json()["status"] == "waiting"
                path = "/v1/invocations/" + created.json()["invocation_id"]
                current = await client.get(path + "/state")
                assert current.status_code == 200, current.text
                updated = await client.post(
                    path + "/state-updates",
                    json={
                        "base_reference": current.json()["items"][0]["reference"],
                        "task_reference": "/do/0/first",
                        "values": {"value": 7},
                    },
                    headers={"Idempotency-Key": "config-only"},
                )
                assert updated.status_code == 200, updated.text
                resumed = await client.post(path + "/resume", json={"input": None})
                assert resumed.status_code == 200, resumed.text
                assert resumed.json()["output"] == {"value": 8}, resumed.text
                assert services.model.calls == []
        assert services.native_state_controller is None
        assert services.effect_journal._state_updates is None
    finally:
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_failed_startup_unregisters_control_and_common_verifier(
    tmp_path, monkeypatch, backend
):
    config = configuration()
    config.native_state_updates = NativeStateUpdateConfig()
    if backend == "postgresql":
        datasource = os.getenv("OWA_TEST_POSTGRES_URL")
        if not datasource:
            pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
        config.persistence.datasource = datasource
    services = RuntimeServices(
        config, model=FakeModel(), database_root=tmp_path if backend == "sqlite" else None
    )
    engine = LangGraphWorkflowEngine()
    original = LangGraphStateController.open

    async def interrupted(controller):
        await original(controller)
        raise RuntimeError("controlled failure after native proof setup")

    monkeypatch.setattr(LangGraphStateController, "open", interrupted)
    try:
        with pytest.raises(RuntimeError, match="controlled failure"):
            await engine.initialize(services)
        assert services.native_state_controller is None
        assert services.effect_journal._state_updates is None
        assert engine.state_controller is None
        assert CAPABILITY not in engine.capabilities().native_extensions
    finally:
        await engine.shutdown()
        services.close()
