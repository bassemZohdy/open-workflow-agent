from __future__ import annotations

import asyncio
import copy
import json
import os
import re
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
from native_stream_test_support import (
    authority,
    configuration,
    enable_lineage,
    subgraph_waiting_workflow,
    workflow,
)
from open_workflow_agent_langgraph import LangGraphWorkflowEngine


@pytest.fixture(params=["sqlite", "postgresql"])
async def stream_runtime(request, tmp_path, monkeypatch):
    for actor in ("alice", "bob"):
        monkeypatch.setenv("OWA_NATIVE_STREAM_TEST_" + actor.upper(), "controlled-stream-" + actor)
    backend, options = request.param if isinstance(request.param, tuple) else (request.param, {})
    options = dict(options)
    lineage = options.pop("lineage", False)
    state = options.pop("state", False)
    state_denied = options.pop("state_denied", False)
    allow_emit = options.pop("allow_emit", False)
    config = configuration(**{"max_events": 4, **options})
    if allow_emit:
        config.execution_authorization.policy.rules[0].actions.append("event.emit")
    if lineage:
        enable_lineage(config)
        config.execution_authorization.policy.rules[0].actions.extend(
            ["state.inspect", "state.replay"]
        )
    elif state or state_denied:
        from open_workflow_agent.config import NativeStateConfig

        config.native_state = NativeStateConfig(max_native_bytes=4_194_304, timeout_seconds=30)
        if not state_denied:
            config.execution_authorization.policy.rules[0].actions.append("state.inspect")
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
@pytest.mark.parametrize(
    "stream_runtime", [("sqlite", {"max_events": 64}), ("postgresql", {"max_events": 64})],
    indirect=True,
)
@pytest.mark.parametrize("deep", [False, True])
async def test_subgraph_stream_projects_native_child_updates(stream_runtime, deep):
    from open_workflow_agent.workflow import compile_workflow

    services, _ = stream_runtime
    source = copy.deepcopy(workflow(nested=True).source)
    if deep:
        source["do"][0]["outer"]["do"].append(
            {"nested": {"do": [{"grand": {"set": {"value": "${ .value }"}}}]}}
        )
    source["document"]["metadata"]["owa"]["nativeExtensions"].append(
        {"name": "langgraph.streams.subgraphs", "version": "1.0.0"}
    )
    owner, plan = await invoke(stream_runtime, plan=compile_workflow(source))
    async with client_for(stream_runtime) as client:
        observed = await client.get(
            f"/v1/invocations/{owner.invocation_id}/stream", params={"mode": "subgraphs"}
        )
        assert observed.status_code == 200, observed.text
        events = frames(observed)
        assert [(item["scope"], item["task_reference"]) for item in events] == (
            [("/do/0/outer/do", "/do/0/outer/do/0/child")]
            + ([
                ("/do/0/outer/do/1/nested/do", "/do/0/outer/do/1/nested/do/0/grand"),
                ("/do/0/outer/do", "/do/0/outer/do/1/nested"),
            ] if deep else [])
        )
        assert all(
            item["data"] == {"mode": "updates", "data": {"value": "alice"}} for item in events
        )
        assert re.search(r"task_[0-9a-f]{64}", observed.text) is None
        assert "owa-scope" not in observed.text
        replay = await client.get(
            f"/v1/invocations/{owner.invocation_id}/stream", params={"mode": "subgraphs"},
            headers={"Last-Event-ID": events[0]["id"]},
        )
        assert [item["id"] for item in frames(replay)] == [item["id"] for item in events[1:]]
        async with client_for(stream_runtime, "bob") as other:
            assert (await other.get(
                f"/v1/invocations/{owner.invocation_id}/stream", params={"mode": "subgraphs"}
            )).status_code == 404
        assert services.invocations.get(owner.invocation_id).output == {"done": True}
        assert len(services.model.calls) == 1
    from open_workflow_agent_langgraph import native
    from open_workflow_agent_langgraph.graph import LangGraphScopeExecutor

    path = services.engine_database_path("langgraph", create_namespace=False)
    saver_type = (
        native.AsyncPostgresSaver if path.startswith("postgres") else native.AsyncSqliteSaver
    )
    executor = LangGraphScopeExecutor(plan, None, owner.invocation_id)
    async with saver_type.from_conn_string(path) as saver:
        for scope in plan.scopes:
            checkpoint = await saver.aget_tuple(executor.checkpoint_config(scope.reference, 1))
            assert checkpoint is not None, scope.reference
            assert checkpoint.config["configurable"]["checkpoint_ns"] == ""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stream_runtime", [("sqlite", {"max_events": 64}), ("postgresql", {"max_events": 64})],
    indirect=True,
)
@pytest.mark.parametrize("deep", [False, True])
async def test_subgraph_stream_resume_preserves_child_checkpoint_and_effect(stream_runtime, deep):
    from open_workflow_agent.workflow import compile_workflow

    services, engine = stream_runtime
    source = copy.deepcopy(workflow().source)
    source["document"]["metadata"]["owa"]["nativeExtensions"].append(
        {"name": "langgraph.streams.subgraphs", "version": "1.0.0"}
    )
    tasks = [
            {"call": {"call": "llm:1.0.0@default"}},
            {"pause": {"wait": {"seconds": 300}}},
            {"childlast": {"set": {"child": True}}},
    ]
    source["do"] = [
        {"outer": {"do": [{"inner": {"do": tasks}}] if deep else tasks}},
        {"last": {"set": {"done": True}}},
    ]
    owner, plan = await invoke(stream_runtime, plan=compile_workflow(source))
    assert services.invocations.get(owner.invocation_id).status == "waiting"
    path = f"/v1/invocations/{owner.invocation_id}/stream"
    async with client_for(stream_runtime) as client:
        paused = await client.get(path, params={"mode": "subgraphs"})
        assert paused.status_code == 200, paused.text
        before = frames(paused)
        scope = "/do/0/outer/do/0/inner/do" if deep else "/do/0/outer/do"
        assert [item["task_reference"] for item in before] == [scope + "/0/call"]
        with authority_scope(authority(services, "alice")):
            result = await engine.resume(owner, {}, plan)
        assert result.status == "completed", result.error
        resumed = await client.get(
            path, params={"mode": "subgraphs"}, headers={"Last-Event-ID": before[-1]["id"]}
        )
        assert resumed.status_code == 200, resumed.text
        assert [item["task_reference"] for item in frames(resumed)] == [
            scope + "/1/pause", scope + "/2/childlast"
        ] + (["/do/0/outer/do/0/inner"] if deep else [])
    assert len(services.model.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["missing_base", "loop", "fork"])
async def test_subgraph_stream_rejects_unsupported_scope_before_execution(stream_runtime, kind):
    from open_workflow_agent.errors import UnsupportedWorkflowFeature
    from open_workflow_agent.workflow import compile_workflow

    services, engine = stream_runtime
    source = copy.deepcopy(workflow().source)
    extensions = source["document"]["metadata"]["owa"]["nativeExtensions"]
    if kind == "missing_base":
        extensions.clear()
    extensions.append({"name": "langgraph.streams.subgraphs", "version": "1.0.0"})
    if kind == "loop":
        source["do"] = [{"outer": {
            "for": {"each": "item", "in": [1, 2]},
            "do": [{"child": {"set": {"value": 1}}}],
        }}]
    elif kind == "fork":
        source["do"] = [{"parallel": {"fork": {"branches": [
            {"left": {"set": {"value": 1}}}, {"right": {"set": {"value": 2}}}
        ]}}}]
    with pytest.raises(UnsupportedWorkflowFeature):
        await engine.compile(compile_workflow(source))
    assert services.model.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stream_runtime",
    [("sqlite", {"max_events": 64}), ("postgresql", {"max_events": 64})],
    indirect=True,
)
async def test_custom_stream_emits_workflow_declared_payload_through_native_writer(
    stream_runtime, monkeypatch
):
    from open_workflow_agent.workflow import compile_workflow
    from open_workflow_agent_langgraph import graph as native_graph

    services, _ = stream_runtime
    writers = []
    original_writer = native_graph.get_stream_writer

    def track_writer():
        writers.append(True)
        return original_writer()

    monkeypatch.setattr(native_graph, "get_stream_writer", track_writer)
    source = copy.deepcopy(workflow().source)
    metadata = source["document"]["metadata"]["owa"]
    metadata["nativeExtensions"].append({"name": "langgraph.streams.custom", "version": "1.0.0"})
    metadata["nativeExtensionData"] = {
        "langgraph.streams.custom@1.0.0": {
            "events": [
                {"task": "/do/0", "name": "prepared", "data": {"from": "task_output"}},
                {"task": "/do/2", "name": "finished", "data": {"literal": {"label": "ready"}}},
            ]
        }
    }
    owner, _ = await invoke(stream_runtime, plan=compile_workflow(source))
    async with client_for(stream_runtime) as client:
        caps = (await client.get("/v1/capabilities")).json()
        assert "langgraph.streams.custom@1.0.0" in caps["nativeExtensions"]
        assert (
            caps["features"]["nativeStreaming"]["customModeProfile"]
            == "langgraph.streams.custom@1.0.0"
        )
        response = await client.get(
            f"/v1/invocations/{owner.invocation_id}/stream", params={"mode": "custom"}
        )
        assert response.status_code == 200, response.text
        events = frames(response)
        assert [(item["task_reference"], item["data"]) for item in events] == [
            ("/do/0/first", {"name": "prepared", "data": {"value": "alice"}}),
            ("/do/2/last", {"name": "finished", "data": {"label": "ready"}}),
        ]
        assert all(item["scope"] == "/do" and item["mode"] == "custom" for item in events)
        replay = await client.get(
            f"/v1/invocations/{owner.invocation_id}/stream",
            params={"mode": "custom"},
            headers={"Last-Event-ID": events[0]["id"]},
        )
        assert [item["id"] for item in frames(replay)] == [events[1]["id"]]
        async with client_for(stream_runtime, "bob") as other:
            denied = await other.get(
                f"/v1/invocations/{owner.invocation_id}/stream", params={"mode": "custom"}
            )
            assert denied.status_code == 404
        assert len(services.model.calls) == 1
        assert len(writers) == 2
        assert services.invocations.get(owner.invocation_id).output == {"done": True}


@pytest.mark.asyncio
async def test_optional_custom_profile_does_not_emit_custom_events(stream_runtime, monkeypatch):
    from open_workflow_agent.workflow import compile_workflow
    from open_workflow_agent_langgraph import graph as native_graph

    source = copy.deepcopy(workflow().source)
    source["document"]["metadata"]["owa"]["nativeExtensions"].append(
        {"name": "langgraph.streams.custom", "version": "1.0.0", "required": False}
    )

    def forbidden_writer():
        raise AssertionError("unexpected writer")

    monkeypatch.setattr(native_graph, "get_stream_writer", forbidden_writer)
    owner, _ = await invoke(stream_runtime, plan=compile_workflow(source))
    async with client_for(stream_runtime) as client:
        observed = await client.get(
            f"/v1/invocations/{owner.invocation_id}/stream", params={"mode": "custom"}
        )
        assert observed.status_code == 200, observed.text
        assert frames(observed) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stream_runtime", "failure"),
    [((backend, {"max_events": 64}), "writer") for backend in ("sqlite", "postgresql")]
    + [
        ((backend, {"max_events": 64, "max_event_bytes": 1024}), "frame")
        for backend in ("sqlite", "postgresql")
    ],
    indirect=["stream_runtime"],
)
async def test_custom_observation_failure_does_not_change_workflow_execution(
    stream_runtime, failure, monkeypatch
):
    from open_workflow_agent.workflow import compile_workflow

    services, _ = stream_runtime
    source = copy.deepcopy(workflow().source)
    metadata = source["document"]["metadata"]["owa"]
    metadata["nativeExtensions"].append({"name": "langgraph.streams.custom", "version": "1.0.0"})
    data = "ready" if failure == "writer" else "x" * 60_000
    metadata["nativeExtensionData"] = {
        "langgraph.streams.custom@1.0.0": {
            "events": [
                {"task": "/do/0", "name": "progress", "data": {"literal": data}},
                {"task": "/do/2", "name": "after", "data": {"literal": "later"}},
            ]
        }
    }
    attempts = []
    if failure == "writer":
        from open_workflow_agent_langgraph import graph as native_graph

        def unavailable_writer():
            attempts.append(True)
            raise RuntimeError("controlled observation failure")

        monkeypatch.setattr(native_graph, "get_stream_writer", unavailable_writer)
    owner, _ = await invoke(stream_runtime, plan=compile_workflow(source))
    assert services.invocations.get(owner.invocation_id).status == "completed"
    assert services.invocations.get(owner.invocation_id).output == {"done": True}
    assert len(services.model.calls) == 1
    if failure == "writer":
        assert len(attempts) == 1
    async with client_for(stream_runtime) as client:
        observed = await client.get(
            f"/v1/invocations/{owner.invocation_id}/stream", params={"mode": "custom"}
        )
        assert observed.status_code == 409, observed.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stream_runtime",
    [
        ("sqlite", {"max_events": 64, "allow_emit": True}),
        ("postgresql", {"max_events": 64, "allow_emit": True}),
    ],
    indirect=True,
)
async def test_custom_observation_does_not_publish_or_duplicate_business_events(stream_runtime):
    from open_workflow_agent.workflow import compile_workflow

    services, _ = stream_runtime
    source = copy.deepcopy(workflow().source)
    metadata = source["document"]["metadata"]["owa"]
    metadata["nativeExtensions"].append({"name": "langgraph.streams.custom", "version": "1.0.0"})
    metadata["nativeExtensionData"] = {
        "langgraph.streams.custom@1.0.0": {
            "events": [
                {
                    "task": "/do/0",
                    "name": "diagnostic",
                    "data": {"literal": {"kind": "observation"}},
                }
            ]
        }
    }
    source["do"] = [
        {
            "publish": {
                "emit": {
                    "event": {
                        "with": {
                            "id": "business-event",
                            "type": "case.created",
                            "data": {"kind": "business"},
                        }
                    }
                }
            }
        },
        {"last": {"set": {"done": True}}},
    ]
    owner, _ = await invoke(stream_runtime, plan=compile_workflow(source))
    assert services.event_store is not None
    rows = services.event_store.connection.execute(
        "SELECT identity FROM event_outbox WHERE invocation_id = ?", (owner.invocation_id,)
    ).fetchall()
    assert len(rows) == 1
    async with client_for(stream_runtime) as client:
        observed = await client.get(
            f"/v1/invocations/{owner.invocation_id}/stream", params={"mode": "custom"}
        )
        assert observed.status_code == 200, observed.text
        events = frames(observed)
        assert len(events) == 1
        assert events[0]["data"] == {"name": "diagnostic", "data": {"kind": "observation"}}
    assert len(services.model.calls) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stream_runtime",
    [("sqlite", {"max_events": 32}), ("postgresql", {"max_events": 32})],
    indirect=True,
)
async def test_custom_stream_replays_after_resume_without_repeating_effect(stream_runtime):
    services, engine = stream_runtime
    owner, plan = await invoke(stream_runtime, plan=workflow(waiting=True, custom_stream=True))
    path = f"/v1/invocations/{owner.invocation_id}/stream"
    async with client_for(stream_runtime) as client:
        paused = await client.get(path, params={"mode": "custom"})
        assert paused.status_code == 200, paused.text
        before = frames(paused)
        assert [item["data"] for item in before] == [{"name": "before", "data": "start"}]
        with authority_scope(authority(services, "alice")):
            result = await engine.resume(owner, {}, plan)
        assert result.status == "completed", result.error
        resumed = await client.get(
            path, params={"mode": "custom"}, headers={"Last-Event-ID": before[-1]["id"]}
        )
        assert resumed.status_code == 200, resumed.text
        assert [item["data"] for item in frames(resumed)] == [
            {"name": "after", "data": {"done": True}}
        ]
    assert len(services.model.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stream_runtime",
    [("sqlite", {"max_events": 128}), ("postgresql", {"max_events": 128})],
    indirect=True,
)
@pytest.mark.parametrize("required", [True, False])
async def test_debug_stream_projects_timestamped_task_and_root_checkpoint_events(
    stream_runtime, required
):
    services, _ = stream_runtime
    owner, _ = await invoke(stream_runtime, plan=workflow(debug_stream=required, nested=True))
    path = f"/v1/invocations/{owner.invocation_id}/stream"
    async with client_for(stream_runtime) as client:
        caps = (await client.get("/v1/capabilities")).json()
        assert "langgraph.streams.debug@1.0.0" in caps["nativeExtensions"]
        assert (
            caps["features"]["nativeStreaming"]["debugModeProfile"]
            == "langgraph.streams.debug@1.0.0"
        )
        response = await client.get(path, params={"mode": "debug"})
        assert response.status_code == 200, response.text
        events = frames(response)
        if required:
            assert {item["data"]["type"] for item in events} == {"task", "checkpoint"}
            assert all(item["mode"] == "debug" and "timestamp" in item["data"] for item in events)
            assert any(
                item["task_reference"].endswith("/child")
                for item in events
                if item["task_reference"]
            )
            assert all(
                item["scope"] == "/do" and item["task_reference"] is None
                for item in events
                if item["data"]["type"] == "checkpoint"
            )
            assert all(
                set(item["data"])
                == (
                    {"type", "timestamp", "status"}
                    if item["data"]["type"] == "task"
                    else {"type", "timestamp"}
                )
                for item in events
            )
            assert "alice" not in response.text
            async with client_for(stream_runtime, "bob") as other:
                assert (await other.get(path, params={"mode": "debug"})).status_code == 404
        else:
            assert events == []
        assert len(services.model.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stream_runtime",
    [("sqlite", {"max_events": 128}), ("postgresql", {"max_events": 128})],
    indirect=True,
)
@pytest.mark.parametrize("outcome", ["interrupted", "faulted"])
async def test_debug_stream_preserves_status_without_private_error_or_interrupt(
    stream_runtime, outcome
):
    services, engine = stream_runtime
    plan = workflow(debug_stream=True, waiting=outcome == "interrupted")
    if outcome == "faulted":
        services.model.failures = 1
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
        result = await engine.invoke(plan, owner, {"value": "private input"})
    assert result.status == ("waiting" if outcome == "interrupted" else "faulted")
    async with client_for(stream_runtime) as client:
        response = await client.get(
            f"/v1/invocations/{owner.invocation_id}/stream", params={"mode": "debug"}
        )
        assert response.status_code == 200, response.text
        events = frames(response)
        tasks = [item for item in events if item["data"]["type"] == "task"]
        assert tasks[-1]["data"]["status"] == outcome
        assert (
            "private input" not in response.text
            and "controlled fake model failure" not in response.text
        )


@pytest.mark.asyncio
async def test_debug_stream_requires_base_profile_before_invocation(stream_runtime):
    from open_workflow_agent.errors import UnsupportedWorkflowFeature

    services, engine = stream_runtime
    plan = workflow(required=False, debug_stream=True)
    before = services.invocations.connection.execute("SELECT COUNT(*) FROM invocations").fetchone()[
        0
    ]
    with pytest.raises(UnsupportedWorkflowFeature):
        await engine.compile(plan)
    app = create_app(config=services.config, services=services, engine=engine)
    app.state.plan = plan
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer controlled-stream-alice"},
    ) as client:
        response = await client.post("/v1/invoke", json={"input": {"value": "alice"}})
        assert response.status_code == UnsupportedWorkflowFeature.status_code, response.text
    assert (
        services.invocations.connection.execute("SELECT COUNT(*) FROM invocations").fetchone()[0]
        == before
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stream_runtime",
    [("sqlite", {"max_events": 64}), ("postgresql", {"max_events": 64})],
    indirect=True,
)
async def test_debug_stream_replays_after_resume_without_repeating_effect(stream_runtime):
    services, engine = stream_runtime
    owner, plan = await invoke(stream_runtime, plan=workflow(waiting=True, debug_stream=True))
    path = f"/v1/invocations/{owner.invocation_id}/stream"
    async with client_for(stream_runtime) as client:
        paused = await client.get(path, params={"mode": "debug"})
        assert paused.status_code == 200, paused.text
        cursor = frames(paused)[-1]["id"]
        with authority_scope(authority(services, "alice")):
            result = await engine.resume(owner, {}, plan)
        assert result.status == "completed", result.error
        resumed = await client.get(
            path, params={"mode": "debug"}, headers={"Last-Event-ID": cursor}
        )
        assert resumed.status_code == 200, resumed.text
        events = frames(resumed)
        assert any(
            item["task_reference"] == "/do/3/last" and item["data"].get("status") == "completed"
            for item in events
        )
        assert all(item["id"].partition(":")[0] == cursor.partition(":")[0] for item in events)
    assert len(services.model.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stream_runtime",
    [
        ("sqlite", {"max_events": 64, "state": True}),
        ("postgresql", {"max_events": 64, "state": True}),
    ],
    indirect=True,
)
@pytest.mark.parametrize("required", [True, False])
async def test_checkpoint_stream_references_root_history_only_with_opt_in(stream_runtime, required):
    from open_workflow_agent.workflow import compile_workflow

    services, _ = stream_runtime
    source = copy.deepcopy(workflow(nested=True).source)
    declarations = source["document"]["metadata"]["owa"]["nativeExtensions"]
    declarations.extend(
        [
            {"name": "langgraph.state.read", "version": "1.0.0"},
            {"name": "langgraph.streams.checkpoints", "version": "1.0.0", "required": required},
        ]
    )
    owner, _ = await invoke(stream_runtime, plan=compile_workflow(source))
    async with client_for(stream_runtime) as client:
        caps = (await client.get("/v1/capabilities")).json()
        assert "langgraph.streams.checkpoints@1.0.0" in caps["nativeExtensions"]
        assert (
            caps["features"]["nativeStreaming"]["checkpointModeProfile"]
            == "langgraph.streams.checkpoints@1.0.0"
        )
        response = await client.get(
            f"/v1/invocations/{owner.invocation_id}/stream", params={"mode": "checkpoints"}
        )
        assert response.status_code == 200, response.text
        events = frames(response)
        if required:
            assert events and all(
                item["scope"] == "/do" and item["task_reference"] is None for item in events
            )
            assert all(set(item["data"]) == {"reference"} for item in events)
            history = await client.get(f"/v1/invocations/{owner.invocation_id}/states")
            assert history.status_code == 200, history.text
            references = {item["reference"] for item in history.json()["items"]}
            assert {item["data"]["reference"] for item in events} <= references
            selected = await client.get(
                f"/v1/invocations/{owner.invocation_id}/state",
                params={"reference": events[-1]["data"]["reference"]},
            )
            assert selected.status_code == 200, selected.text
            assert selected.json()["items"][0]["reference"] == events[-1]["data"]["reference"]
            assert len(services.model.calls) == 1
            async with client_for(stream_runtime, "bob") as other:
                denied = await other.get(
                    f"/v1/invocations/{owner.invocation_id}/stream", params={"mode": "checkpoints"}
                )
                assert denied.status_code == 404
        else:
            assert events == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stream_runtime", "missing"),
    [
        ((backend, {"state": True}), missing)
        for backend in ("sqlite", "postgresql")
        for missing in ("base", "read")
    ]
    + [((backend, {}), "installation") for backend in ("sqlite", "postgresql")],
    indirect=["stream_runtime"],
)
async def test_checkpoint_stream_fails_admission_when_dependencies_are_missing(
    stream_runtime, missing
):
    from open_workflow_agent.errors import UnsupportedWorkflowFeature
    from open_workflow_agent.workflow import compile_workflow

    services, engine = stream_runtime
    if missing == "installation":
        assert "langgraph.streams.checkpoints@1.0.0" not in engine.capabilities().native_extensions
    source = copy.deepcopy(workflow(required=missing != "base").source)
    declarations = source["document"]["metadata"]["owa"]["nativeExtensions"]
    declarations.extend(
        [
            {"name": "langgraph.state.read", "version": "1.0.0", "required": missing != "read"},
            {"name": "langgraph.streams.checkpoints", "version": "1.0.0"},
        ]
    )
    plan = compile_workflow(source)
    before = services.invocations.connection.execute("SELECT COUNT(*) FROM invocations").fetchone()[
        0
    ]
    with pytest.raises(UnsupportedWorkflowFeature):
        await engine.compile(plan)
    app = create_app(config=services.config, services=services, engine=engine)
    app.state.plan = plan
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer controlled-stream-alice"},
    ) as client:
        if missing == "installation":
            caps = (await client.get("/v1/capabilities")).json()
            assert "checkpoints" not in caps["features"]["nativeStreaming"]["modes"]
        response = await client.post("/v1/invoke", json={"input": {"value": "alice"}})
        assert response.status_code == UnsupportedWorkflowFeature.status_code, response.text
    assert (
        services.invocations.connection.execute("SELECT COUNT(*) FROM invocations").fetchone()[0]
        == before
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stream_runtime",
    [
        ("sqlite", {"state_denied": True, "max_events": 32}),
        ("postgresql", {"state_denied": True, "max_events": 32}),
    ],
    indirect=True,
)
async def test_checkpoint_reference_does_not_grant_state_inspection(stream_runtime):
    owner, _ = await invoke(stream_runtime, plan=workflow(checkpoint_stream=True))
    async with client_for(stream_runtime) as client:
        observed = await client.get(
            f"/v1/invocations/{owner.invocation_id}/stream", params={"mode": "checkpoints"}
        )
        assert observed.status_code == 200, observed.text
        reference = frames(observed)[-1]["data"]["reference"]
        denied = await client.get(
            f"/v1/invocations/{owner.invocation_id}/state", params={"reference": reference}
        )
        assert denied.status_code == 403, denied.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stream_runtime",
    [("sqlite", {"max_events": 32}), ("postgresql", {"max_events": 32})],
    indirect=True,
)
@pytest.mark.parametrize("required", [True, False])
async def test_task_stream_requires_opt_in_and_projects_only_public_status(
    stream_runtime, required
):
    services, _ = stream_runtime
    owner, _ = await invoke(stream_runtime, plan=workflow(task_stream=required))
    path = f"/v1/invocations/{owner.invocation_id}/stream"
    async with client_for(stream_runtime) as client:
        caps = (await client.get("/v1/capabilities")).json()
        assert "langgraph.streams.tasks@1.0.0" in caps["nativeExtensions"]
        assert (
            caps["features"]["nativeStreaming"]["taskModeProfile"]
            == "langgraph.streams.tasks@1.0.0"
        )
        response = await client.get(path, params={"mode": "tasks"})
        assert response.status_code == 200, response.text
        events = frames(response)
        all_events = frames(await client.get(path))
        if required:
            assert [(event["task_reference"], event["data"]) for event in events] == [
                (f"/do/{index}/{name}", {"status": status})
                for index, name in enumerate(("first", "answer", "last"))
                for status in ("started", "completed")
            ]
            assert all(event["mode"] == "tasks" and event["scope"] == "/do" for event in events)
            assert all(set(event["data"]) == {"status"} for event in events)
            assert {event["mode"] for event in all_events} == {"values", "updates", "tasks"}
            replay = await client.get(
                path, params={"mode": "tasks"}, headers={"Last-Event-ID": events[0]["id"]}
            )
            assert [event["id"] for event in frames(replay)] == [
                event["id"] for event in events[1:]
            ]
            assert (await client_forbidden_task_stream(stream_runtime, owner.invocation_id)) == 404
        else:
            assert events == []
            assert {event["mode"] for event in all_events} == {"values", "updates"}
        assert len(services.model.calls) == 1


async def client_forbidden_task_stream(runtime, invocation_id):
    async with client_for(runtime, "bob") as client:
        response = await client.get(
            f"/v1/invocations/{invocation_id}/stream", params={"mode": "tasks"}
        )
        return response.status_code


@pytest.mark.asyncio
async def test_task_stream_without_required_base_is_rejected_before_invocation(stream_runtime):
    from open_workflow_agent.errors import UnsupportedWorkflowFeature

    services, engine = stream_runtime
    before = services.invocations.connection.execute("SELECT COUNT(*) FROM invocations").fetchone()[
        0
    ]
    with pytest.raises(UnsupportedWorkflowFeature):
        await engine.compile(workflow(required=False, task_stream=True))
    app = create_app(config=services.config, services=services, engine=engine)
    app.state.plan = workflow(required=False, task_stream=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer controlled-stream-alice"},
    ) as client:
        response = await client.post("/v1/invoke", json={"input": {"value": "alice"}})
        assert response.status_code == UnsupportedWorkflowFeature.status_code, response.text
    assert (
        services.invocations.connection.execute("SELECT COUNT(*) FROM invocations").fetchone()[0]
        == before
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stream_runtime",
    [("sqlite", {"max_events": 32}), ("postgresql", {"max_events": 32})],
    indirect=True,
)
async def test_task_stream_marks_suspension_without_exposing_native_interrupt(stream_runtime):
    owner, _ = await invoke(stream_runtime, plan=workflow(waiting=True, task_stream=True))
    async with client_for(stream_runtime) as client:
        response = await client.get(
            f"/v1/invocations/{owner.invocation_id}/stream", params={"mode": "tasks"}
        )
        assert response.status_code == 200, response.text
        events = frames(response)
        assert (events[-1]["task_reference"], events[-1]["data"]) == (
            "/do/2/pause",
            {"status": "interrupted"},
        )
        assert all(set(item["data"]) == {"status"} for item in events)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stream_runtime",
    [("sqlite", {"max_events": 32}), ("postgresql", {"max_events": 32})],
    indirect=True,
)
async def test_task_stream_marks_failure_without_exposing_error_details(stream_runtime):
    services, engine = stream_runtime
    services.model.failures = 1
    plan = workflow(task_stream=True)
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
        result = await engine.invoke(plan, owner, {"value": "private input"})
    assert result.status == "faulted"
    async with client_for(stream_runtime) as client:
        response = await client.get(
            f"/v1/invocations/{owner.invocation_id}/stream", params={"mode": "tasks"}
        )
        assert response.status_code == 200, response.text
        events = frames(response)
        assert (events[-1]["task_reference"], events[-1]["data"]) == (
            "/do/1/answer",
            {"status": "faulted"},
        )
        assert all(set(item["data"]) == {"status"} for item in events)
        assert (
            "private input" not in response.text
            and "controlled fake model failure" not in response.text
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stream_runtime",
    [("sqlite", {"max_events": 64}), ("postgresql", {"max_events": 64})],
    indirect=True,
)
async def test_task_stream_reports_nested_public_scope(stream_runtime):
    owner, _ = await invoke(stream_runtime, plan=workflow(task_stream=True, nested=True))
    async with client_for(stream_runtime) as client:
        response = await client.get(
            f"/v1/invocations/{owner.invocation_id}/stream", params={"mode": "tasks"}
        )
        assert response.status_code == 200, response.text
        events = frames(response)
        child = [item for item in events if item["task_reference"].endswith("/child")]
        assert [item["data"]["status"] for item in child] == ["started", "completed"]
        assert all(item["scope"].startswith("/do/") for item in child)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stream_runtime",
    [("sqlite", {"max_events": 32}), ("postgresql", {"max_events": 32})],
    indirect=True,
)
async def test_task_stream_resume_keeps_cursor_and_does_not_repeat_effect(stream_runtime):
    services, engine = stream_runtime
    owner, plan = await invoke(stream_runtime, plan=workflow(waiting=True, task_stream=True))
    path = f"/v1/invocations/{owner.invocation_id}/stream"
    async with client_for(stream_runtime) as client:
        paused = await client.get(path, params={"mode": "tasks"})
        assert paused.status_code == 200, paused.text
        cursor = frames(paused)[-1]["id"]
        with authority_scope(authority(services, "alice")):
            result = await engine.resume(owner, {}, plan)
        assert result.status == "completed", result.error
        resumed = await client.get(
            path, params={"mode": "tasks"}, headers={"Last-Event-ID": cursor}
        )
        assert resumed.status_code == 200, resumed.text
        events = frames(resumed)
        assert (events[-1]["task_reference"], events[-1]["data"]) == (
            "/do/3/last",
            {"status": "completed"},
        )
        assert all(item["id"].partition(":")[0] == cursor.partition(":")[0] for item in events)
    assert len(services.model.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stream_runtime",
    [
        ("sqlite", {"max_events": 32, "lineage": True}),
        ("postgresql", {"max_events": 32, "lineage": True}),
    ],
    indirect=True,
)
async def test_task_stream_observes_replay_target_without_repeating_retained_effects(
    stream_runtime,
):
    from open_workflow_agent.workflow import compile_workflow

    services, _ = stream_runtime
    source = copy.deepcopy(
        workflow(
            task_stream=True, checkpoint_stream=True, debug_stream=True, custom_stream=True
        ).source
    )
    metadata = source["document"]["metadata"]["owa"]
    metadata["nativeExtensions"].extend(
        [
            {"name": "langgraph.state.replay", "version": "1.0.0"},
        ]
    )
    metadata["nativeExtensionData"]["langgraph.state.replay@1.0.0"] = {
        "authorizationPolicyRef": services.config.execution_authorization.id,
        "replayEffectPolicy": "reexecute",
    }
    owner, _ = await invoke(stream_runtime, plan=compile_workflow(source))
    async with client_for(stream_runtime) as client:
        history = await client.get(f"/v1/invocations/{owner.invocation_id}/states")
        assert history.status_code == 200, history.text
        reference = next(
            item["reference"]
            for item in history.json()["items"]
            if item["root_task_visits"] == {"/do/0/first": 1, "/do/1/answer": 1}
        )
        response = await client.post(
            f"/v1/invocations/{owner.invocation_id}/state-replays",
            headers={"Idempotency-Key": "streamed-replay"},
            json={"reference": reference, "acknowledge_reexecution": True},
        )
        assert response.status_code == 202, response.text
        target = response.json()["invocation_id"]
        async with asyncio.timeout(180):
            while services.invocations.get(target).status not in {
                "completed",
                "faulted",
                "cancelled",
            }:
                await asyncio.sleep(0.1)
        assert services.invocations.get(target).status == "completed"
        observed = await client.get(f"/v1/invocations/{target}/stream", params={"mode": "tasks"})
        assert observed.status_code == 200, observed.text
        events = frames(observed)
        assert [(item["task_reference"], item["data"]["status"]) for item in events] == [
            ("/do/2/last", "started"),
            ("/do/2/last", "completed"),
        ]
        checkpoint_response = await client.get(
            f"/v1/invocations/{target}/stream", params={"mode": "checkpoints"}
        )
        assert checkpoint_response.status_code == 200, checkpoint_response.text
        checkpoint_events = frames(checkpoint_response)
        assert checkpoint_events
        target_state = await client.get(
            f"/v1/invocations/{target}/state",
            params={"reference": checkpoint_events[-1]["data"]["reference"]},
        )
        assert target_state.status_code == 200, target_state.text
        debug_response = await client.get(
            f"/v1/invocations/{target}/stream", params={"mode": "debug"}
        )
        assert debug_response.status_code == 200, debug_response.text
        assert [
            (item["task_reference"], item["data"]["status"])
            for item in frames(debug_response)
            if item["data"]["type"] == "task"
        ] == [("/do/2/last", "started"), ("/do/2/last", "completed")]
        custom_response = await client.get(
            f"/v1/invocations/{target}/stream", params={"mode": "custom"}
        )
        assert custom_response.status_code == 200, custom_response.text
        assert [(item["task_reference"], item["data"]) for item in frames(custom_response)] == [
            ("/do/2/last", {"name": "after", "data": {"done": True}})
        ]
        assert len(services.model.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stream_runtime",
    [("sqlite", {"max_events": 64}), ("postgresql", {"max_events": 64})],
    indirect=True,
)
async def test_task_stream_remains_available_during_native_send_fanout(stream_runtime):
    from open_workflow_agent.workflow import compile_workflow

    services, _ = stream_runtime
    source = copy.deepcopy(workflow(task_stream=True, debug_stream=True).source)
    source["document"]["metadata"]["owa"]["nativeExtensions"].append(
        {"name": "langgraph.send", "version": "1.0.0"}
    )
    source["do"] = [
        {
            "fan": {
                "fork": {
                    "branches": [{"left": {"set": {"left": 1}}}, {"right": {"set": {"right": 2}}}]
                }
            }
        },
        {"last": {"set": {"done": True}}},
    ]
    owner, _ = await invoke(stream_runtime, plan=compile_workflow(source))
    async with client_for(stream_runtime) as client:
        response = await client.get(
            f"/v1/invocations/{owner.invocation_id}/stream", params={"mode": "tasks"}
        )
        assert response.status_code == 200, response.text
        events = frames(response)
        assert (events[0]["task_reference"], events[0]["data"]) == (
            "/do/0/fan",
            {"status": "started"},
        )
        assert (events[-1]["task_reference"], events[-1]["data"]) == (
            "/do/1/last",
            {"status": "completed"},
        )
        assert '"reason":"completed"' in response.text
        assert all(set(item["data"]) == {"status"} for item in events)
        for name in ("left", "right"):
            assert [
                item["data"]["status"]
                for item in events
                if item["task_reference"].endswith("/" + name)
            ] == ["started", "completed"]
        debug = await client.get(
            f"/v1/invocations/{owner.invocation_id}/stream", params={"mode": "debug"}
        )
        assert debug.status_code == 200, debug.text
        for name in ("left", "right"):
            assert [
                item["data"]["status"]
                for item in frames(debug)
                if item["task_reference"] and item["task_reference"].endswith("/" + name)
            ] == ["started", "completed"]
        assert len(services.model.calls) == 0


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
        assert (await client.get(path, params={"mode": "messages"})).status_code == 422
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
    ("stream_runtime", "task_stream", "checkpoint_stream", "debug_stream", "custom_stream"),
    [
        ((backend, {"max_events": 32}), tasks, False, False, False)
        for backend in ("sqlite", "postgresql")
        for tasks in (False, True)
    ]
    + [
        ((backend, {"max_events": 32, "state": True}), False, True, False, False)
        for backend in ("sqlite", "postgresql")
    ]
    + [
        ((backend, {"max_events": 32}), False, False, True, False)
        for backend in ("sqlite", "postgresql")
    ]
    + [
        ((backend, {"max_events": 32}), False, False, False, True)
        for backend in ("sqlite", "postgresql")
    ],
    indirect=["stream_runtime"],
)
async def test_actual_process_exit_loses_observation_replay_but_not_native_resume(
    stream_runtime, tmp_path, task_stream, checkpoint_stream, debug_stream, custom_stream
):
    services, engine = stream_runtime
    config_path = tmp_path / "worker-config.json"
    result_path = tmp_path / "worker-result.json"
    config_path.write_text(
        json.dumps(
            {
                "config": services.config.model_dump(mode="json", exclude={"sandbox"}),
                "root": str(services.database_root) if services.database_root is not None else None,
                "task_stream": task_stream,
                "checkpoint_stream": checkpoint_stream,
                "debug_stream": debug_stream,
                "custom_stream": custom_stream,
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
            result = await engine.resume(
                owner,
                {},
                workflow(
                    waiting=True,
                    task_stream=True if task_stream else None,
                    checkpoint_stream=True if checkpoint_stream else None,
                    debug_stream=True if debug_stream else None,
                    custom_stream=custom_stream,
                ),
            )
        assert result.status == "completed", result.error
        assert (
            await client.get(path, headers={"Last-Event-ID": previous["cursor"]})
        ).status_code == 409
        current = await client.get(path)
        assert current.status_code == 200, current.text
        assert [item for item in frames(current) if item["mode"] == "values"][-1]["data"] == {
            "done": True
        }
        assert frames(current)[0]["id"].partition(":")[0] != previous["cursor"].partition(":")[0]
        if task_stream:
            task_events = frames(await client.get(path, params={"mode": "tasks"}))
            assert task_events[-1]["data"] == {"status": "completed"}
        if checkpoint_stream:
            checkpoint_events = frames(await client.get(path, params={"mode": "checkpoints"}))
            assert checkpoint_events
            selected = await client.get(
                f"/v1/invocations/{owner.invocation_id}/state",
                params={"reference": checkpoint_events[-1]["data"]["reference"]},
            )
            assert selected.status_code == 200, selected.text
        if debug_stream:
            debug_events = frames(await client.get(path, params={"mode": "debug"}))
            assert any(item["data"].get("status") == "completed" for item in debug_events)
        if custom_stream:
            custom_events = frames(await client.get(path, params={"mode": "custom"}))
            assert [item["data"] for item in custom_events] == [
                {"name": "after", "data": {"done": True}}
            ]
    assert services.model.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stream_runtime", [("sqlite", {"max_events": 64}), ("postgresql", {"max_events": 64})],
    indirect=True,
)
async def test_subgraph_stream_survives_actual_process_exit(stream_runtime, tmp_path):
    services, engine = stream_runtime
    config_path = tmp_path / "worker-config.json"
    result_path = tmp_path / "worker-result.json"
    config_path.write_text(json.dumps({
        "config": services.config.model_dump(mode="json", exclude={"sandbox"}),
        "root": str(services.database_root) if services.database_root is not None else None,
        "subgraph_stream": True,
    }), encoding="utf-8")
    completed = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, str(Path(__file__).with_name("native_stream_restart_worker.py")),
         str(config_path), str(result_path)],
        capture_output=True, text=True, timeout=60, cwd=Path(__file__).resolve().parents[2],
    )
    assert completed.returncode == 0, completed.stderr[-4000:]
    previous = json.loads(result_path.read_text(encoding="utf-8"))
    owner = services.invocations.get(previous["invocation_id"])
    assert owner.status == "waiting"
    path = f"/v1/invocations/{owner.invocation_id}/stream"
    async with client_for(stream_runtime) as client:
        absent = await client.get(path, headers={"Last-Event-ID": previous["cursor"]})
        assert absent.status_code == 409
        with authority_scope(authority(services, "alice")):
            result = await engine.resume(owner, {}, subgraph_waiting_workflow())
        assert result.status == "completed", result.error
        observed = await client.get(path, params={"mode": "subgraphs"})
        assert observed.status_code == 200, observed.text
        assert [item["task_reference"] for item in frames(observed)] == [
            "/do/0/outer/do/1/pause", "/do/0/outer/do/2/childlast"
        ]
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
