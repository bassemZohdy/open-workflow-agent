from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from open_workflow_agent.api import create_app
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.execution_authority import authority_scope
from open_workflow_agent.services import RuntimeServices

pytest.importorskip("langgraph.graph")
from native_stream_test_support import authority, configuration, workflow
from open_workflow_agent_langgraph import LangGraphWorkflowEngine


@pytest.fixture(params=["sqlite", "postgresql"])
async def stream_runtime(request, tmp_path, monkeypatch):
    for actor in ("alice", "bob"):
        monkeypatch.setenv("OWA_NATIVE_STREAM_TEST_" + actor.upper(), "controlled-stream-" + actor)
    backend, options = request.param if isinstance(request.param, tuple) else (request.param, {})
    config = configuration(**{"max_events": 4, **options})
    root = tmp_path
    if backend == "postgresql":
        datasource = os.getenv("OWA_TEST_POSTGRES_URL")
        if not datasource:
            pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
        config.persistence.datasource = datasource
        root = None
    services = RuntimeServices(config, model=FakeModel(), database_root=root)
    engine = LangGraphWorkflowEngine()
    await engine.initialize(services)
    await engine._timer_dispatcher.stop()
    yield services, engine
    await engine.shutdown()
    services.close()


async def invoke(runtime, *, actor="alice", plan=None):
    services, engine = runtime
    plan = plan or workflow()
    owner = services.invocations.create(
        engine="langgraph",
        session_id="same",
        user_id="same",
        owner_principal=actor,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    with authority_scope(authority(services, actor)):
        result = await engine.invoke(plan, owner, {"value": actor})
    assert result.status in {"waiting", "completed"}, result.error
    return owner, plan


def client_for(runtime, actor="alice"):
    services, engine = runtime
    config = services.config.model_copy(deep=True)
    config.server.api_security_profile = "actor-" + actor
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=create_app(config=config, services=services, engine=engine)
        ),
        base_url="http://test",
        headers={"Authorization": "Bearer controlled-stream-" + actor},
    )


def frames(response):
    return [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ") and '"schema"' in line
    ]


@pytest.mark.asyncio
async def test_owner_stream_is_bounded_mode_filtered_and_has_explicit_replay_gaps(stream_runtime):
    services, engine = stream_runtime
    owner, _ = await invoke(stream_runtime)
    path = f"/v1/invocations/{owner.invocation_id}/stream"
    async with client_for(stream_runtime) as client:
        caps = (await client.get("/v1/capabilities")).json()
        assert "langgraph.streams@1.0.0" in caps["nativeExtensions"]
        assert caps["features"]["nativeStreaming"]["disconnectCancelsInvocation"] is False
        response = await client.get(path)
        assert response.status_code == 200, response.text
        retained = frames(response)
        assert len(retained) == 4
        assert {item["mode"] for item in retained} == {"values", "updates"}
        assert retained[-1]["data"] == {"done": True}
        assert '"reason":"completed"' in response.text
        replay = await client.get(path, headers={"Last-Event-ID": retained[0]["id"]})
        assert [item["id"] for item in frames(replay)] == [item["id"] for item in retained[1:]]
        expired = retained[0]["id"].partition(":")[0] + ":1"
        assert (await client.get(path, headers={"Last-Event-ID": expired})).status_code == 409
        selected = await client.get(path, params={"mode": "updates", "max_events": 1})
        assert len(frames(selected)) == 1 and frames(selected)[0]["mode"] == "updates"
        assert '"reason":"event_limit"' in selected.text
        small = await client.get(path, params={"max_bytes": 256})
        assert not frames(small) and '"reason":"byte_limit"' in small.text
        assert (await client.get(path, params={"mode": "debug"})).status_code == 422
    assert len(services.model.calls) == 1
    assert not services.native_streams._consumers


@pytest.mark.asyncio
async def test_same_correlation_principals_cannot_read_each_others_data_or_cursors(stream_runtime):
    services, _ = stream_runtime
    alice, _ = await invoke(stream_runtime)
    bob, _ = await invoke(stream_runtime, actor="bob")
    async with client_for(stream_runtime, "alice") as a, client_for(stream_runtime, "bob") as b:
        a_path = f"/v1/invocations/{alice.invocation_id}/stream"
        b_path = f"/v1/invocations/{bob.invocation_id}/stream"
        mine = await a.get(a_path)
        assert mine.status_code == 200
        cursor = frames(mine)[0]["id"]
        denied = await b.get(a_path, headers={"Last-Event-ID": cursor})
        missing = await b.get("/v1/invocations/missing/stream")
        assert denied.status_code == missing.status_code == 404
        assert denied.json()["error"]["code"] == missing.json()["error"]["code"]
        assert (await a.get(b_path)).status_code == 404
        assert (await b.get(b_path, headers={"Last-Event-ID": cursor})).status_code == 409
        assert (await b.get(b_path)).status_code == 200
        assert (await a.get(a_path, headers={"Authorization": "Bearer invalid"})).status_code == 403
    assert len(services.model.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("declared,required", [(False, True), (True, False)])
async def test_undeclared_and_optional_workflows_have_no_data_observations(
    stream_runtime, declared, required
):
    owner, _ = await invoke(stream_runtime, plan=workflow(declared=declared, required=required))
    async with client_for(stream_runtime) as client:
        response = await client.get(f"/v1/invocations/{owner.invocation_id}/stream")
        assert response.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stream_runtime",
    [("sqlite", {"max_events": 16}), ("postgresql", {"max_events": 16})],
    indirect=True,
)
async def test_paused_stream_finishes_and_authorized_resume_extends_same_incarnation(
    stream_runtime,
):
    services, engine = stream_runtime
    owner, plan = await invoke(stream_runtime, plan=workflow(waiting=True))
    assert owner.status == "waiting"
    path = f"/v1/invocations/{owner.invocation_id}/stream"
    async with client_for(stream_runtime) as client:
        paused = await client.get(path)
        assert paused.status_code == 200 and '"reason":"waiting"' in paused.text
        cursor = frames(paused)[-1]["id"]
        with authority_scope(authority(services, "alice")):
            result = await engine.resume(owner, {}, plan)
        assert result.status == "completed", result.error
        resumed = await client.get(path, headers={"Last-Event-ID": cursor})
        assert resumed.status_code == 200, resumed.text
        assert frames(resumed)[-1]["data"] == {"done": True}
        assert all(
            item["id"].partition(":")[0] == cursor.partition(":")[0] for item in frames(resumed)
        )
    assert len(services.model.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stream_runtime",
    [("sqlite", {"denied": True}), ("postgresql", {"denied": True})],
    indirect=True,
)
async def test_original_owner_still_needs_explicit_stream_permission(stream_runtime):
    services, _ = stream_runtime
    owner, _ = await invoke(stream_runtime)
    async with client_for(stream_runtime) as client:
        response = await client.get(f"/v1/invocations/{owner.invocation_id}/stream")
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "execution_authorization_denied"
        assert "alice" not in response.text
    assert len(services.model.calls) == 1
    assert not services.native_streams._consumers


@pytest.mark.asyncio
async def test_changed_original_policy_rejects_read_without_dispatch_or_mutation(stream_runtime):
    services, _ = stream_runtime
    owner, _ = await invoke(stream_runtime)
    before = services.invocations.get(owner.invocation_id).status
    services.config.execution_authorization.version = "changed"
    async with client_for(stream_runtime) as client:
        response = await client.get(f"/v1/invocations/{owner.invocation_id}/stream")
        assert response.status_code == 403
    assert services.invocations.get(owner.invocation_id).status == before
    assert len(services.model.calls) == 1
    assert not services.native_streams._consumers


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stream_runtime",
    [("sqlite", {"max_subscribers": 1}), ("postgresql", {"max_subscribers": 1})],
    indirect=True,
)
async def test_cancelled_http_observer_releases_capacity_without_cancelling_execution(
    stream_runtime,
):
    services, engine = stream_runtime
    reached, release = asyncio.Event(), asyncio.Event()

    async def model_response(_prompt):
        reached.set()
        await release.wait()
        return {"answer": "done"}

    services.model.response = model_response
    plan = workflow()
    owner = services.invocations.create(
        engine="langgraph",
        session_id="same",
        user_id="same",
        owner_principal="alice",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    with authority_scope(authority(services, "alice")):
        running = asyncio.create_task(engine.invoke(plan, owner, {"value": "alice"}))
    observer = None
    try:
        await asyncio.wait_for(reached.wait(), timeout=30)
        async with client_for(stream_runtime) as client:
            path = f"/v1/invocations/{owner.invocation_id}/stream"
            observer = asyncio.create_task(client.get(path))
            async with asyncio.timeout(5):
                while not services.native_streams._consumers:
                    await asyncio.sleep(0.01)
            assert (await client.get(path)).status_code == 429
            observer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await observer
            assert not services.native_streams._consumers
            assert not running.done()
            independent = await client.get(path, params={"max_events": 1})
            assert independent.status_code == 200
            release.set()
            result = await asyncio.wait_for(running, timeout=30)
            assert result.status == "completed", result.error
    finally:
        release.set()
        if observer is not None:
            observer.cancel()
        await asyncio.wait_for(asyncio.gather(running, return_exceptions=True), timeout=30)
    assert len(services.model.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stream_runtime",
    [("sqlite", {"max_events": 16}), ("postgresql", {"max_events": 16})],
    indirect=True,
)
async def test_actual_process_exit_loses_observation_replay_but_not_native_resume(
    stream_runtime, tmp_path
):
    services, engine = stream_runtime
    config_path = tmp_path / "worker-config.json"
    result_path = tmp_path / "worker-result.json"
    config_path.write_text(
        json.dumps(
            {
                "config": services.config.model_dump(mode="json", exclude={"sandbox"}),
                "root": str(services.database_root) if services.database_root is not None else None,
            }
        ),
        encoding="utf-8",
    )
    completed = await asyncio.to_thread(
        subprocess.run,
        [
            sys.executable,
            str(Path(__file__).with_name("native_stream_restart_worker.py")),
            str(config_path),
            str(result_path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert completed.returncode == 0, completed.stderr[-4000:]
    previous = json.loads(result_path.read_text(encoding="utf-8"))
    owner = services.invocations.get(previous["invocation_id"])
    assert owner.status == "waiting"
    path = f"/v1/invocations/{owner.invocation_id}/stream"
    async with client_for(stream_runtime) as client:
        absent = await client.get(path, headers={"Last-Event-ID": previous["cursor"]})
        assert absent.status_code == 409
        assert services.model.calls == []
        assert services.invocations.get(owner.invocation_id).status == "waiting"
        with authority_scope(authority(services, "alice")):
            result = await engine.resume(owner, {}, workflow(waiting=True))
        assert result.status == "completed", result.error
        assert (
            await client.get(path, headers={"Last-Event-ID": previous["cursor"]})
        ).status_code == 409
        current = await client.get(path)
        assert current.status_code == 200, current.text
        assert frames(current)[-1]["data"] == {"done": True}
        assert frames(current)[0]["id"].partition(":")[0] != previous["cursor"].partition(":")[0]
    assert services.model.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_configuration_only_example_uses_real_startup_invoke_and_stream(
    tmp_path, monkeypatch, backend
):
    from open_workflow_agent.config import RuntimeConfig

    monkeypatch.setenv("OWA_NATIVE_STREAM_TOKEN", "controlled-example-stream-token")
    root = Path(__file__).resolve().parents[2]
    config = RuntimeConfig.from_file(root / "examples/native-stream.yaml")
    config.workflow.path = str(root / "examples/native-stream-workflow.yaml")
    database_root = tmp_path
    if backend == "postgresql":
        datasource = os.getenv("OWA_TEST_POSTGRES_URL")
        if not datasource:
            pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
        config.persistence.datasource = datasource
        database_root = None
    services = RuntimeServices(config, model=FakeModel(), database_root=database_root)
    engine = LangGraphWorkflowEngine()
    app = create_app(config=config, services=services, engine=engine)
    try:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer controlled-example-stream-token"},
            ) as client,
        ):
            invoked = await client.post("/v1/invoke", json={"input": {"value": "coffee"}})
            assert invoked.status_code == 200, invoked.text
            identifier = invoked.json()["invocation_id"]
            observed = await client.get(f"/v1/invocations/{identifier}/stream")
            assert observed.status_code == 200, observed.text
            assert frames(observed)[-1]["data"] == {"done": True}
            assert '"reason":"completed"' in observed.text
            assert len(services.model.calls) == 1
    finally:
        services.close()


@pytest.mark.asyncio
async def test_disabled_installation_rejects_required_api_workflow_before_creation(
    stream_runtime, tmp_path
):
    from open_workflow_agent.errors import UnsupportedWorkflowFeature

    original, _ = stream_runtime
    config = original.config.model_copy(deep=True)
    config.observability.native_streams = None
    services = RuntimeServices(
        config,
        model=FakeModel(),
        database_root=None if config.persistence.datasource else tmp_path / "disabled",
    )
    engine = LangGraphWorkflowEngine()
    await engine.initialize(services)
    app = create_app(config=config, services=services, engine=engine)
    app.state.plan = workflow()
    try:
        before = services.invocations.connection.execute(
            "SELECT COUNT(*) FROM invocations"
        ).fetchone()[0]
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer controlled-stream-alice"},
        ) as client:
            response = await client.post("/v1/invoke", json={"input": {"value": "alice"}})
            assert response.status_code == UnsupportedWorkflowFeature.status_code
            assert response.json()["error"]["code"] == "unsupported_workflow_feature"
            assert (await client.get("/v1/invocations/absent/stream")).status_code == 404
        after = services.invocations.connection.execute(
            "SELECT COUNT(*) FROM invocations"
        ).fetchone()[0]
        assert before == after
        assert services.model.calls == []
    finally:
        await engine.shutdown()
        services.close()
