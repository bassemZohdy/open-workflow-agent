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

pytest.importorskip("langgraph")
from native_state_test_support import authority, configuration, workflow
from open_workflow_agent_langgraph import LangGraphWorkflowEngine


@pytest.fixture(params=["sqlite", "postgresql"])
async def state_runtime(request, tmp_path, monkeypatch):
    for actor in ("alice", "bob"):
        monkeypatch.setenv("OWA_NATIVE_STREAM_TEST_" + actor.upper(), "controlled-state-" + actor)
    backend, options = request.param if isinstance(request.param, tuple) else (request.param, {})
    config = configuration(**options)
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
    original_read = engine.state_reader._read

    async def diagnostic(*args, **kwargs):
        try:
            return await original_read(*args, **kwargs)
        except Exception as error:
            context = error.__context__ or error
            print("controlled native-state diagnostic:", type(context).__name__, str(context)[:500])
            raise

    monkeypatch.setattr(engine.state_reader, "_read", diagnostic)
    yield services, engine
    await engine.shutdown()
    services.close()


async def invoke(runtime, *, actor="alice", plan=None, input_data=None):
    services, engine = runtime
    plan = plan or workflow()
    handle = services.invocations.create(
        engine="langgraph",
        session_id="same",
        user_id="same",
        owner_principal=actor,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    with authority_scope(authority(services, actor)):
        result = await engine.invoke(plan, handle, input_data or {"value": actor})
    assert result.status in {"completed", "waiting"}, result.error
    return handle, plan


def client_for(runtime, actor="alice"):
    services, engine = runtime
    config = services.config.model_copy(deep=True)
    config.server.api_security_profile = "actor-" + actor
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=create_app(config=config, services=services, engine=engine)
        ),
        base_url="http://test",
        headers={"Authorization": "Bearer controlled-state-" + actor},
    )


@pytest.mark.asyncio
async def test_current_history_and_selected_state_are_opaque_and_do_not_dispatch(state_runtime):
    services, engine = state_runtime
    handle, _ = await invoke(state_runtime)
    path = f"/v1/invocations/{handle.invocation_id}"
    async with client_for(state_runtime) as client:
        current = await client.get(path + "/state")
        assert current.status_code == 200, current.text
        data = current.json()
        assert data["continuation"] == "not_inspected"
        assert data["items"][0]["data"] == {"done": True}
        reference = data["items"][0]["reference"]
        assert len(reference) == 64 and all(c in "0123456789abcdef" for c in reference)
        page = await client.get(path + "/states", params={"limit": 2})
        assert page.status_code == 200, page.text
        assert page.json()["items"][0] == data["items"][0]
        older = page.json()["next_before"]
        assert older is not None
        next_page = await client.get(path + "/states", params={"limit": 2, "before": older})
        assert next_page.status_code == 200, next_page.text
        assert not {x["reference"] for x in page.json()["items"]} & {
            x["reference"] for x in next_page.json()["items"]
        }
        selected = await client.get(
            path + "/state", params={"reference": next_page.json()["items"][0]["reference"]}
        )
        assert selected.status_code == 200, selected.text
        assert selected.json()["items"][0] == next_page.json()["items"][0]
        complete = await client.get(path + "/states")
        assert complete.status_code == 200, complete.text
        assert any(item["phase"] == "initial" for item in complete.json()["items"])
        caps = (await client.get("/v1/capabilities")).json()
        assert caps["features"]["nativeState"]["updates"] is False
        assert "langgraph.state.read@1.0.0" in caps["nativeExtensions"]
        for private in (
            "thread_id",
            "checkpoint_id",
            "snapshot",
            "receipt_bindings",
            "workflow/input",
        ):
            assert '"' + private + '":' not in current.text
        assert (
            await client.get(path + "/state", params={"thread_id": "caller-selected"})
        ).status_code == 422
    assert len(services.model.calls) == 1
    assert services.invocations.get(handle.invocation_id).status == "completed"
    assert engine.capabilities().streaming is False


@pytest.mark.asyncio
async def test_other_original_principals_cannot_read_values_or_select_cursors(state_runtime):
    services, _ = state_runtime
    alice, _ = await invoke(state_runtime)
    bob, _ = await invoke(state_runtime, actor="bob")
    async with client_for(state_runtime) as a, client_for(state_runtime, "bob") as b:
        a_path = f"/v1/invocations/{alice.invocation_id}"
        b_path = f"/v1/invocations/{bob.invocation_id}"
        mine = await a.get(a_path + "/state")
        assert mine.status_code == 200, mine.text
        reference = mine.json()["items"][0]["reference"]
        assert (await b.get(a_path + "/state", params={"reference": reference})).status_code == 404
        assert (await a.get(b_path + "/states")).status_code == 404
        assert (await b.get(b_path + "/state", params={"reference": reference})).status_code == 409
        assert (await b.get(b_path + "/state")).status_code == 200
    assert len(services.model.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state_runtime",
    [("sqlite", {"max_native_bytes": 1024}), ("postgresql", {"max_native_bytes": 1024})],
    indirect=True,
)
async def test_oversized_native_values_reject_before_sdk_decoding(state_runtime, monkeypatch):
    from open_workflow_agent.storage import is_postgresql_datasource
    from open_workflow_agent_langgraph import native

    services, engine = state_runtime
    handle, _ = await invoke(state_runtime, input_data={"value": "x" * 8192})
    called = []

    async def forbidden(*_args, **_kwargs):
        called.append(True)
        raise AssertionError("state size was not checked before decoding")

    saver = (
        native.AsyncPostgresSaver
        if is_postgresql_datasource(engine.native.database_path)
        else native.AsyncSqliteSaver
    )
    monkeypatch.setattr(saver, "aget_tuple", forbidden)
    async with client_for(state_runtime) as client:
        response = await client.get(f"/v1/invocations/{handle.invocation_id}/state")
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "native_state_unavailable"
        assert "x" * 100 not in response.text
    assert called == []
    assert len(services.model.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state_runtime", [("sqlite", {"denied": True}), ("postgresql", {"denied": True})], indirect=True
)
async def test_original_owner_needs_separate_state_permission(state_runtime):
    services, _ = state_runtime
    handle, _ = await invoke(state_runtime)
    async with client_for(state_runtime) as client:
        for suffix in ("state", "states"):
            response = await client.get(f"/v1/invocations/{handle.invocation_id}/" + suffix)
            assert response.status_code == 403
            assert "alice" not in response.text
    assert len(services.model.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("declared,required", [(False, True), (True, False)])
async def test_absent_and_optional_declarations_do_not_authorize_state_reads(
    state_runtime, declared, required
):
    handle, _ = await invoke(state_runtime, plan=workflow(declared=declared, required=required))
    async with client_for(state_runtime) as client:
        response = await client.get(f"/v1/invocations/{handle.invocation_id}/state")
        assert response.status_code == 422


@pytest.mark.asyncio
async def test_native_reader_enforces_read_only_database_transaction(state_runtime):
    import sqlite3

    from open_workflow_agent_langgraph.native_state_storage import readonly_saver

    services, engine = state_runtime
    await invoke(state_runtime)
    if services.datasource:
        from psycopg.errors import ReadOnlySqlTransaction

        error = ReadOnlySqlTransaction
    else:
        error = sqlite3.OperationalError
    with pytest.raises(error):
        async with readonly_saver(engine.native.database_path, services.config.native_state) as (
            saver,
            _,
        ):
            await saver.conn.execute("CREATE TABLE owa_native_read_must_not_write (value TEXT)")


@pytest.mark.asyncio
async def test_missing_native_database_is_not_created_by_inspection(tmp_path):
    from open_workflow_agent.config import NativeStateConfig
    from open_workflow_agent.native_state import NativeStateUnavailable
    from open_workflow_agent_langgraph.native_state_storage import readonly_saver

    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(NativeStateUnavailable):
        async with readonly_saver(str(missing), NativeStateConfig()):
            raise AssertionError("absent checkpoint storage was opened")
    assert not missing.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state_runtime",
    [
        ("sqlite", {"max_history": 2, "max_page_size": 2}),
        ("postgresql", {"max_history": 2, "max_page_size": 2}),
    ],
    indirect=True,
)
async def test_history_window_is_explicit_and_unknown_selectors_are_rejected(state_runtime):
    services, _ = state_runtime
    handle, _ = await invoke(state_runtime)
    path = f"/v1/invocations/{handle.invocation_id}"
    async with client_for(state_runtime) as client:
        history = await client.get(path + "/states")
        assert history.status_code == 200, history.text
        assert len(history.json()["items"]) == 2
        assert history.json()["history_window_limited"] is True
        assert history.json()["next_before"] is None
        assert (
            await client.get(path + "/state", params={"reference": "0" * 64})
        ).status_code == 409
        assert (await client.get(path + "/states", params={"limit": 3})).status_code == 422
        assert (
            await client.get(
                path + "/state", params=[("reference", "0" * 64), ("reference", "1" * 64)]
            )
        ).status_code == 422
    assert len(services.model.calls) == 1


@pytest.mark.asyncio
async def test_historical_initializer_view_does_not_relax_initial_execution_admission(
    state_runtime,
):
    from open_workflow_agent.errors import InvocationStateError
    from open_workflow_agent_langgraph.graph import LangGraphScopeExecutor
    from open_workflow_agent_langgraph.native_state_storage import checkpoint_index, readonly_saver

    services, engine = state_runtime
    handle, plan = await invoke(state_runtime)
    async with readonly_saver(engine.native.database_path, services.config.native_state) as (
        saver,
        postgres,
    ):
        driver = LangGraphScopeExecutor(
            plan, saver, handle.invocation_id, effect_journal=services.effect_journal
        )
        config = driver.checkpoint_config("/do", 1)
        index, _ = await checkpoint_index(
            saver, config["configurable"]["thread_id"], services.config.native_state, postgres
        )
        config["configurable"]["checkpoint_id"] = index[-1][0]
        initial = await saver.aget_tuple(config)
        driver.validate_historical_initial_projection(initial, {"value": "alice"})
        with pytest.raises(InvocationStateError, match="initial projection"):
            driver.validate_initial_projection(initial, {"value": "alice"})
    assert len(services.model.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state_runtime",
    [("sqlite", {"max_concurrency": 1}), ("postgresql", {"max_concurrency": 1})],
    indirect=True,
)
async def test_repeated_read_cancellation_keeps_slot_until_database_cleanup_finishes(
    state_runtime, monkeypatch
):
    from open_workflow_agent.native_state import NativeStateCapacity, NativeStateQuery

    services, engine = state_runtime
    handle, plan = await invoke(state_runtime)
    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def delayed(*_args, **_kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()

    monkeypatch.setattr(engine.state_reader, "_read", delayed)
    request = asyncio.create_task(engine.state_reader.read(handle, plan, NativeStateQuery()))
    await entered.wait()
    request.cancel()
    await cleaning.wait()
    request.cancel()
    await asyncio.sleep(0)
    assert not request.done()
    with pytest.raises(NativeStateCapacity):
        await engine.state_reader.read(handle, plan, NativeStateQuery())
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await request
    assert not engine.state_reader._slots.locked()
    assert len(services.model.calls) == 1


@pytest.mark.asyncio
async def test_checkpoint_references_and_history_survive_a_fresh_process_without_execution(
    state_runtime, tmp_path
):
    services, _ = state_runtime
    handle, _ = await invoke(state_runtime)
    async with client_for(state_runtime) as client:
        response = await client.get(f"/v1/invocations/{handle.invocation_id}/states")
        assert response.status_code == 200, response.text
        selected = response.json()["items"][1]
    config_path = tmp_path / "read-config.json"
    output_path = tmp_path / "read-result.json"
    config_path.write_text(
        json.dumps(
            {
                "config": services.config.model_dump(mode="json", exclude={"sandbox"}),
                "root": str(services.database_root) if services.database_root is not None else None,
                "invocation_id": handle.invocation_id,
                "reference": selected["reference"],
            }
        ),
        encoding="utf-8",
    )
    completed = await asyncio.to_thread(
        subprocess.run,
        [
            sys.executable,
            str(Path(__file__).with_name("native_state_read_worker.py")),
            str(config_path),
            str(output_path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert completed.returncode == 0, completed.stderr[-4000:]
    observed = json.loads(output_path.read_text(encoding="utf-8"))
    assert observed["selected"] == selected
    assert observed["model_calls"] == 0
    assert selected["reference"] in observed["history"]
    assert len(services.model.calls) == 1


@pytest.mark.asyncio
async def test_declared_channels_are_projected_with_public_root_visits(state_runtime):
    from open_workflow_agent.workflow import compile_workflow

    services, _ = state_runtime
    source = workflow().source
    source["do"] = [{"one": {"set": "${ [1] }"}}, {"two": {"set": "${ [2] }"}}]
    owa = source["document"]["metadata"]["owa"]
    owa["nativeExtensions"].append(
        {"name": "langgraph.channels", "version": "1.0.0", "required": True}
    )
    owa["nativeExtensionData"] = {
        "langgraph.channels@1.0.0": {
            "channels": [
                {
                    "id": "items",
                    "reducer": "append",
                    "initial": ["seed"],
                    "writes": [{"task": "/do/0"}, {"task": "/do/1"}],
                }
            ],
            "reads": [],
        }
    }
    handle, _ = await invoke(state_runtime, plan=compile_workflow(source))
    async with client_for(state_runtime) as client:
        response = await client.get(f"/v1/invocations/{handle.invocation_id}/state")
        assert response.status_code == 200, response.text
        item = response.json()["items"][0]
        assert item["data"] == [2]
        assert item["channels"] == {"items": ["seed", 1, 2]}
        assert item["root_task_visits"] == {"/do/0/one": 1, "/do/1/two": 1}
    assert services.model.calls == []


@pytest.mark.asyncio
async def test_changed_original_policy_rejects_state_inspection(state_runtime):
    services, _ = state_runtime
    handle, _ = await invoke(state_runtime)
    services.config.execution_authorization.version = "changed"
    async with client_for(state_runtime) as client:
        response = await client.get(f"/v1/invocations/{handle.invocation_id}/state")
        assert response.status_code == 403
    assert len(services.model.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state_runtime",
    [("sqlite", {"max_response_bytes": 256}), ("postgresql", {"max_response_bytes": 256})],
    indirect=True,
)
async def test_projection_limit_rejects_without_partial_data(state_runtime):
    handle, _ = await invoke(state_runtime)
    async with client_for(state_runtime) as client:
        response = await client.get(f"/v1/invocations/{handle.invocation_id}/state")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "native_state_unavailable"
        assert '"data"' not in response.text


@pytest.mark.asyncio
async def test_disabled_installation_rejects_required_api_workflow_before_creation(
    state_runtime, tmp_path
):
    from open_workflow_agent.errors import UnsupportedWorkflowFeature

    original, _ = state_runtime
    config = original.config.model_copy(deep=True)
    config.native_state = None
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
            headers={"Authorization": "Bearer controlled-state-alice"},
        ) as client:
            response = await client.post("/v1/invoke", json={"input": {"value": "alice"}})
            assert response.status_code == UnsupportedWorkflowFeature.status_code
            assert (await client.get("/v1/invocations/unknown/state")).status_code == 404
        after = services.invocations.connection.execute(
            "SELECT COUNT(*) FROM invocations"
        ).fetchone()[0]
        assert before == after and services.model.calls == []
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_configuration_only_example_starts_invokes_and_reads_history(
    tmp_path, monkeypatch, backend
):
    from open_workflow_agent.config import RuntimeConfig

    monkeypatch.setenv("OWA_NATIVE_STATE_TOKEN", "controlled-example-state-token")
    root = Path(__file__).resolve().parents[2]
    config = RuntimeConfig.from_file(root / "examples/native-state.yaml")
    config.workflow.path = str(root / "examples/native-state-workflow.yaml")
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
                headers={"Authorization": "Bearer controlled-example-state-token"},
            ) as client,
        ):
            invoked = await client.post("/v1/invoke", json={"input": {"value": "coffee"}})
            assert invoked.status_code == 200, invoked.text
            identifier = invoked.json()["invocation_id"]
            state = await client.get(f"/v1/invocations/{identifier}/state")
            assert state.status_code == 200, state.text
            assert state.json()["items"][0]["data"] == {"done": True}
            history = await client.get(f"/v1/invocations/{identifier}/states")
            assert history.status_code == 200, history.text
            assert any(item["phase"] == "initial" for item in history.json()["items"])
            assert len(services.model.calls) == 1
    finally:
        services.close()
