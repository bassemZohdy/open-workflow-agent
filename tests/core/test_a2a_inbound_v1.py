"""V1 admission and authoritative Task projection, without business side effects."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from open_workflow_agent.a2a import A2A_PROTOCOL_VERSION
from open_workflow_agent.a2a_streaming import a2a_sse_stream
from open_workflow_agent.a2a_tasks import project_a2a_task
from open_workflow_agent.api import create_app
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.effects import EffectIdentity
from open_workflow_agent.engine import PortableWorkflowEngine
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.streaming import StreamLimits
from open_workflow_agent.workflow import compile_workflow


def message(**values):
    return {"messageId": "request-1", "role": "ROLE_USER", "parts": [{"text": "hello"}], **values}


def runtime(tmp_path, binding):
    config = RuntimeConfig.model_validate(
        {"model": {"provider": "fake"}, "a2a": {"enabled": True, "transport": binding}}
    )
    model = FakeModel({"response": "known"})
    services = RuntimeServices(config, model=model, database_root=tmp_path)
    engine = PortableWorkflowEngine()
    return create_app(config=config, services=services, engine=engine), services, model


def shell(services, *, status="running", output=None):
    plan = compile_workflow()
    handle = services.invocations.create(
        engine="portable",
        session_id="original-context",
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    if status != "running":
        services.invocations.update(handle, status=status, output=output)
    return handle


@pytest.mark.asyncio
@pytest.mark.parametrize("binding", ["jsonrpc", "http_json"])
@pytest.mark.parametrize(
    "invalid",
    ["missing_id", "empty_id", "bad_role", "bad_parts", "bad_context", "bad_task", "bad_immediate"],
)
async def test_invalid_v1_send_rejects_before_invocation_or_model(tmp_path, binding, invalid):
    app, services, model = runtime(tmp_path, binding)
    value = message()
    configuration = {}
    if invalid == "missing_id":
        del value["messageId"]
    elif invalid == "empty_id":
        value["messageId"] = ""
    elif invalid == "bad_role":
        value["role"] = "user"
    elif invalid == "bad_parts":
        value["parts"] = [{"text": "hello", "data": {"secret": "never echo"}}]
    elif invalid == "bad_context":
        value["contextId"] = 7
    elif invalid == "bad_task":
        value["taskId"] = ["not-an-id"]
    else:
        configuration = {"returnImmediately": "true"}
    params = {"message": value, "configuration": configuration}
    async with app.router.lifespan_context(app):
        before = services.invocations.connection.execute(
            "SELECT COUNT(*) FROM invocations"
        ).fetchone()[0]
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/a2a" if binding == "jsonrpc" else "/a2a/message:send",
                headers={"A2A-Version": A2A_PROTOCOL_VERSION},
                json={"jsonrpc": "2.0", "id": "call-1", "method": "SendMessage", "params": params}
                if binding == "jsonrpc"
                else params,
            )
        assert "error" in response.json()
        assert "never echo" not in response.text
        assert (
            services.invocations.connection.execute("SELECT COUNT(*) FROM invocations").fetchone()[
                0
            ]
            == before
        )
        assert model.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("binding", ["jsonrpc", "http_json"])
async def test_resume_context_mismatch_has_no_delivery_or_input_mutation(tmp_path, binding):
    app, services, model = runtime(tmp_path, binding)
    handle = shell(services, status="waiting")
    before = services.invocations.get(handle.invocation_id)._persisted_payload
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            params = {"message": message(taskId=handle.invocation_id, contextId="foreign-context")}
            response = await client.post(
                "/a2a" if binding == "jsonrpc" else "/a2a/message:send",
                headers={"A2A-Version": "1.0"},
                json={"jsonrpc": "2.0", "id": "call-1", "method": "SendMessage", "params": params}
                if binding == "jsonrpc"
                else params,
            )
        assert "error" in response.json()
        assert services.invocations.get(handle.invocation_id)._persisted_payload == before
        assert (
            services.invocations.connection.execute(
                "SELECT 1 FROM invocation_inputs WHERE invocation_id=?", (handle.invocation_id,)
            ).fetchone()
            is None
        )
        assert model.calls == []


def test_completed_null_is_an_explicit_artifact(tmp_path):
    _app, services, _model = runtime(tmp_path, "jsonrpc")
    handle = shell(services, status="completed", output=None)
    task = project_a2a_task(handle, services=services)
    assert task["artifacts"][0]["parts"] == [{"data": None, "mediaType": "application/json"}]
    services.close()


@pytest.mark.asyncio
async def test_subscription_reads_another_services_terminal_commit_without_local_event(tmp_path):
    _app, services, _model = runtime(tmp_path, "jsonrpc")
    handle = shell(services)
    other = RuntimeServices(services.config, model=FakeModel(), database_root=tmp_path)
    stream = a2a_sse_stream(
        services.lifecycle_events,
        services.invocations,
        handle.invocation_id,
        services=services,
        limits=StreamLimits(timeout_seconds=2),
    )
    first = json.loads((await anext(stream)).decode().split("data: ", 1)[1])
    assert first["task"]["status"]["state"] == "TASK_STATE_WORKING"
    worker_handle = other.invocations.get(handle.invocation_id)
    other.invocations.update(worker_handle, status="completed", output=None)
    frames = []

    async def drain():
        async for frame in stream:
            frames.append(json.loads(frame.decode().split("data: ", 1)[1]))

    await asyncio.wait_for(drain(), timeout=3)
    assert frames[0]["artifactUpdate"]["artifact"]["parts"][0]["data"] is None
    assert frames[-1]["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert "final" not in frames[-1]["statusUpdate"]
    other.close()
    services.close()


def test_real_retained_timer_is_automatic_work_without_mutating_wait_evidence(tmp_path):
    _app, services, _model = runtime(tmp_path, "jsonrpc")
    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "a2a",
                "name": "automatic",
                "version": "1.0.0",
            },
            "do": [{"pause": {"wait": {"seconds": 600}}}],
        }
    )
    handle = services.invocations.create(
        engine="portable",
        session_id="timer-context",
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    lease = services.execution_ownership.acquire(handle, seconds=30)
    handle._execution_lease = lease
    services.recovery_definitions.admit(plan, handle, {"original": None}, execution_lease=lease)
    identity = EffectIdentity(
        handle.invocation_id, plan.fingerprint, "/do/0/pause", (("/do/0/pause", 1),), "wait:arm:v1"
    )
    registration = services.timers.arm(
        identity, 600, workflow_source=plan.source, execution_lease=lease
    )
    services.invocations.update(handle, status="waiting")
    services.execution_ownership.release(lease)
    before = services.timers.get(identity)
    task = project_a2a_task(handle, services=services)
    assert task["status"] == {"state": "TASK_STATE_WORKING"}
    assert services.timers.get(identity) == before == registration
    assert services.invocations.read_input(handle) == {"original": None}
    assert services.invocations.get(handle.invocation_id).status == "waiting"
    services.close()


@pytest.mark.asyncio
async def test_initial_delivery_callback_requires_current_skill_and_original_owner(tmp_path):
    source = {
        "document": {"dsl": "1.0.3", "namespace": "a2a", "name": "bound", "version": "1.0.0"},
        "do": [{"finish": {"set": {"done": True}}}],
    }
    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "workflow": {"catalog": [source]},
            "a2a": {"enabled": True, "skills": [{"id": "bound", "workflow": "bound"}]},
        }
    )
    model = FakeModel()
    services = RuntimeServices(config, model=model, database_root=tmp_path)
    engine = PortableWorkflowEngine()
    app = create_app(config=config, services=services, engine=engine)
    async with app.router.lifespan_context(app):
        callback = engine._initial_delivery_dispatcher._authorize
        assert callback is not None
        await engine._initial_delivery_dispatcher.stop()
        plan = app.state.a2a_skill_plans["bound"]
        handle = services.invocations.create(
            engine="portable",
            session_id="bound-context",
            user_id=None,
            workflow_name=plan.name,
            workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
            owner_principal="anonymous",
        )
        await engine.prepare_initial_delivery(
            plan, handle, {"question": "original"}, skill_id="bound"
        )
        assert callback(handle, "bound", plan)
        foreign = services.invocations.create(
            engine="portable",
            session_id="foreign-context",
            user_id=None,
            workflow_name=plan.name,
            workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
            owner_principal="foreign",
        )
        assert not callback(foreign, "bound", plan)
        assert not callback(handle, "unregistered", plan)
        del app.state.a2a_skill_plans["bound"]
        assert not callback(handle, "bound", plan)
        assert services.invocations.read_input(handle) == {"question": "original"}
        assert services.invocations.get(handle.invocation_id).status == "running"
        assert model.calls == []
    services.close()


@pytest.mark.parametrize("installation", ["missing", "wrong"])
def test_unqualified_sdk_rejects_inbound_before_exposure_or_mutation(
    tmp_path, monkeypatch, installation
):
    from importlib.metadata import PackageNotFoundError

    from fastapi import FastAPI
    from open_workflow_agent import _a2a_types
    from open_workflow_agent.a2a import a2a_capabilities, build_agent_card, mount_a2a
    from open_workflow_agent.errors import ToolError

    config = RuntimeConfig.model_validate({"model": {"provider": "fake"}, "a2a": {"enabled": True}})
    model = FakeModel()
    services = RuntimeServices(config, model=model, database_root=tmp_path)
    handle = shell(services)
    before = services.invocations.get(handle.invocation_id)
    engine = PortableWorkflowEngine()

    def installed_version(name):
        assert name == "a2a-sdk"
        if installation == "missing":
            raise PackageNotFoundError(name)
        return "0.3.0-unreviewed"

    monkeypatch.setattr(_a2a_types, "version", installed_version)
    with pytest.raises(ToolError, match="requires a2a-sdk 1.2.2"):
        create_app(config=config, services=services, engine=engine)
    with pytest.raises(ToolError, match="requires a2a-sdk 1.2.2"):
        build_agent_card(config.a2a, url="https://example.test/a2a", workflow_name="default")
    app = FastAPI()
    routes_before = list(app.routes)
    middleware_before = list(app.user_middleware)

    async def unreachable(*args, **kwargs):
        raise AssertionError("business callback must not run")

    with pytest.raises(ToolError, match="requires a2a-sdk 1.2.2"):
        mount_a2a(
            app,
            config.a2a,
            config.security,
            start_task=unreachable,
            resolve_resume_target=unreachable,
            resume_task=unreachable,
        )
    assert app.routes == routes_before
    assert app.user_middleware == middleware_before
    capabilities = a2a_capabilities(config.a2a)
    assert capabilities["enabled"] is True
    assert capabilities["effectiveEnabled"] is False
    assert capabilities["sdkAvailable"] is False
    assert capabilities["sdkVersion"] is None
    assert capabilities["requiredSdkVersion"] == "1.2.2"
    assert capabilities["conformanceClaim"] is False
    assert capabilities["card"] is None
    assert capabilities["tasks"] is False
    assert capabilities["streaming"] is False
    assert capabilities["taskOperations"] == capabilities["streamingOperations"] == []
    assert "unreviewed" not in json.dumps(capabilities)
    assert services.invocations.get(handle.invocation_id) == before
    assert (
        services.invocations.connection.execute("SELECT COUNT(*) FROM invocations").fetchone()[0]
        == 1
    )
    assert (
        services.invocations.connection.execute(
            "SELECT COUNT(*) FROM invocation_inputs"
        ).fetchone()[0]
        == 0
    )
    assert model.calls == []
    assert not hasattr(engine, "services")
    services.close()
