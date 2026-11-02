"""Actual pinned SDK stream driving, with no public capability advertisement."""

from __future__ import annotations

import json
import os
from uuid import uuid4

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.native_streams import NativeStreamLimits, NativeStreamLog
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow, generate_default_workflow

pytest.importorskip("langgraph.checkpoint.sqlite")
from open_workflow_agent_langgraph import LangGraphWorkflowEngine  # noqa: E402

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize(
    "damage", ["segment", "identity", "depth", "payload", "unexpected_occurrence"]
)
async def test_subgraph_projection_fails_closed_on_invalid_sdk_namespace(damage):
    from native_stream_test_support import workflow
    from open_workflow_agent_langgraph.graph import LangGraphScopeExecutor, _node_name

    source = workflow(nested=True).source
    source["document"]["metadata"]["owa"]["nativeExtensions"].append(
        {"name": "langgraph.streams.subgraphs", "version": "1.0.0"}
    )
    plan = compile_workflow(source)
    log = NativeStreamLog("invocation-a")
    executor = LangGraphScopeExecutor(plan, None, "invocation-a", stream_observer=log)
    parent = plan.scopes[0].tasks[0]
    child = next(scope for scope in plan.scopes if scope.reference != "/do").tasks[0]
    segment = _node_name(parent) + ":" + str(uuid4())
    namespace = (segment,)
    payload = {_node_name(child): {}}
    if damage == "segment":
        namespace = ("private-invalid-namespace",)
    elif damage == "identity":
        namespace = ("task_" + "f" * 64 + ":" + str(uuid4()),)
    elif damage == "depth":
        namespace = (segment, segment)
    elif damage == "unexpected_occurrence":
        namespace = (segment, "1")
    else:
        payload = {"unknown-private-node": {}}
    executor._observe_subgraph_update(namespace, payload)
    with pytest.raises(BufferError):
        log.subscribe()


@pytest.mark.parametrize("suffix", ["0", "01", "32", "-1", "private", "9" * 100])
async def test_loop_subgraph_projection_rejects_invalid_occurrence_namespace(suffix):
    from native_stream_test_support import workflow
    from open_workflow_agent_langgraph.graph import LangGraphScopeExecutor, _node_name

    source = workflow().source
    source["document"]["metadata"]["owa"]["nativeExtensions"].append(
        {"name": "langgraph.streams.subgraphs", "version": "1.0.0"}
    )
    source["do"] = [{"loop": {
        "for": {"each": "turn", "in": [0, 1]},
        "do": [{"item": {"set": {"turn": "${ $turn }"}}}],
    }}]
    plan = compile_workflow(source)
    log = NativeStreamLog("invocation-a")
    executor = LangGraphScopeExecutor(plan, None, "invocation-a", stream_observer=log)
    parent = plan.scopes[0].tasks[0]
    child = next(scope for scope in plan.scopes if scope.reference != "/do").tasks[0]
    namespace = (_node_name(parent) + ":" + str(uuid4()), suffix)
    executor._observe_subgraph_update(namespace, {_node_name(child): {}})
    with pytest.raises(BufferError):
        log.subscribe()


@pytest.mark.parametrize(("scope_suffix", "suffix"), [("try", "1"), ("catch/do", "2")])
async def test_try_subgraph_projection_rejects_wrong_occurrence(scope_suffix, suffix):
    from native_stream_test_support import try_subgraph_waiting_workflow
    from open_workflow_agent_langgraph.graph import LangGraphScopeExecutor, _node_name

    plan = try_subgraph_waiting_workflow(fault=True)
    log = NativeStreamLog("invocation-a")
    executor = LangGraphScopeExecutor(plan, None, "invocation-a", stream_observer=log)
    parent = plan.scopes[0].tasks[0]
    scope = next(item for item in plan.scopes if item.reference.endswith("/" + scope_suffix))
    namespace = (_node_name(parent) + ":" + str(uuid4()), suffix)
    snapshot = {
        "data": {"value": 1}, "outputs": {}, "variables": {}, "context": {}, "jump_to": None,
        "stop": False, "scope_exit": False, "transition_selected": False, "ended": False,
        "end_output": None, "end_context": None, "task_visits": {}, "execution_path": [],
    }
    executor._observe_subgraph_update(
        namespace, {_node_name(scope.tasks[0]): {"snapshot": json.dumps(snapshot)}}
    )
    with pytest.raises(BufferError):
        log.subscribe()


async def test_task_projection_drops_sdk_data_and_fails_closed_on_unknown_shapes():
    from open_workflow_agent_langgraph.graph import LangGraphScopeExecutor, _node_name

    plan = compile_workflow(generate_default_workflow())
    log = NativeStreamLog("invocation-a")
    executor = LangGraphScopeExecutor(plan, None, "invocation-a", stream_observer=log)
    scope = plan.scopes[0]
    name = _node_name(scope.tasks[0])
    executor._observe_scope_event(
        "tasks",
        {"id": "private-id", "name": name, "input": {"secret": "value"}, "triggers": ["private"]},
        scope,
    )
    executor._observe_scope_event(
        "tasks",
        {
            "id": "private-id",
            "name": name,
            "result": {"secret": "value"},
            "error": None,
            "interrupts": [],
        },
        scope,
    )
    frames, subscriber = log.subscribe()
    events = [json.loads(frame.payload) for frame in frames]
    assert [event["data"] for event in events] == [{"status": "started"}, {"status": "completed"}]
    assert "private" not in str(events) and "secret" not in str(events)
    subscriber.close()
    executor._observe_scope_event("tasks", {"name": name, "input": {}, "result": {}}, scope)
    with pytest.raises(BufferError):
        log.subscribe()


@pytest.mark.parametrize("damage", [None, "thread", "namespace", "checkpoint"])
async def test_checkpoint_projection_never_exposes_native_identity_or_data(damage):
    from open_workflow_agent_langgraph.graph import LangGraphScopeExecutor
    from open_workflow_agent_langgraph.native_state import state_reference

    plan = compile_workflow(generate_default_workflow())
    log = NativeStreamLog("invocation-a")
    executor = LangGraphScopeExecutor(plan, None, "invocation-a", stream_observer=log)
    config = executor.checkpoint_config("/do", 1)["configurable"]
    checkpoint_id = str(uuid4())
    config["checkpoint_id"] = checkpoint_id
    if damage == "thread":
        config["thread_id"] = "other-private-thread"
    elif damage == "namespace":
        config["checkpoint_ns"] = "other-private-namespace"
    elif damage == "checkpoint":
        config["checkpoint_id"] = "not-a-checkpoint-id"
    executor._observe_scope_event(
        "checkpoints",
        {"config": {"configurable": config}, "values": {"secret": "value"}, "tasks": ["private"]},
        plan.scopes[0],
    )
    if damage is not None:
        with pytest.raises(BufferError):
            log.subscribe()
    else:
        frames, subscriber = log.subscribe()
        assert len(frames) == 1
        event = json.loads(frames[0].payload)
        assert event["data"] == {
            "reference": state_reference("invocation-a", plan.fingerprint, checkpoint_id)
        }
        assert (
            checkpoint_id not in str(event)
            and "secret" not in str(event)
            and "private" not in str(event)
        )
        subscriber.close()


@pytest.mark.parametrize("damage", ["kind", "phase", "thread", "timestamp"])
async def test_debug_projection_fails_closed_for_malformed_sdk_events(damage):
    from open_workflow_agent_langgraph.graph import LangGraphScopeExecutor, _node_name

    plan = compile_workflow(generate_default_workflow())
    log = NativeStreamLog("invocation-a")
    executor = LangGraphScopeExecutor(plan, None, "invocation-a", stream_observer=log)
    scope = plan.scopes[0]
    payload = {
        "type": "task",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "payload": {"name": _node_name(scope.tasks[0]), "input": {"secret": True}},
    }
    if damage == "kind":
        payload["type"] = "unexpected"
    elif damage == "phase":
        payload["type"] = "task_result"
    elif damage == "thread":
        payload["type"] = "checkpoint"
        payload["payload"] = {
            "config": {
                "configurable": {
                    "thread_id": "foreign",
                    "checkpoint_ns": "",
                    "checkpoint_id": str(uuid4()),
                }
            }
        }
    elif damage == "timestamp":
        payload["timestamp"] = "not-a-time"
    executor._observe_scope_event("debug", payload, scope)
    with pytest.raises(BufferError):
        log.subscribe()


@pytest.mark.parametrize("damage", ["task", "name", "extra"])
async def test_custom_projection_rejects_native_events_without_declared_binding(damage):
    from open_workflow_agent_langgraph.graph import LangGraphScopeExecutor

    source = generate_default_workflow()
    source["document"]["metadata"] = {
        "owa": {
            "nativeExtensions": [
                {"name": "langgraph.streams", "version": "1.0.0"},
                {"name": "langgraph.streams.custom", "version": "1.0.0"},
            ],
            "nativeExtensionData": {
                "langgraph.streams.custom@1.0.0": {
                    "events": [{"task": "/do/0", "name": "progress", "data": {"literal": "safe"}}]
                }
            },
        }
    }
    plan = compile_workflow(source)
    log = NativeStreamLog("invocation-a")
    executor = LangGraphScopeExecutor(plan, None, "invocation-a", stream_observer=log)
    payload = {"task": "/do/0/respond", "name": "progress", "data": "safe"}
    if damage == "task":
        payload["task"] = "/do/0/other"
    elif damage == "name":
        payload["name"] = "other"
    else:
        payload["native"] = "private"
    executor._observe_scope_event("custom", payload, plan.scopes[0])
    with pytest.raises(BufferError):
        log.subscribe()


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("observation", ["normal", "closed", "oversized"])
async def test_native_stream_driver_preserves_effects_and_hides_private_state(
    tmp_path, monkeypatch, backend, observation
):
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    config = RuntimeConfig()
    if datasource:
        config.persistence.datasource = datasource
    model = FakeModel({"reply": "x" * 200 if observation == "oversized" else "done"})
    services = RuntimeServices(config, model=model, database_root=None if datasource else tmp_path)
    engine = LangGraphWorkflowEngine()
    try:
        await engine.initialize(services)
        workflow = generate_default_workflow()
        workflow["do"] = [
            {"prepare": {"set": {"request": "coffee"}}},
            {"answer": {"call": "llm:1.0.0@default"}},
            {"finish": {"set": {"ok": True}}},
        ]
        plan = compile_workflow(workflow)
        handle = services.invocations.create(
            engine="langgraph",
            session_id="shared-session",
            user_id="correlation-user",
            workflow_name=plan.name,
            workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
        )
        log = NativeStreamLog(
            handle.invocation_id,
            NativeStreamLimits(max_event_bytes=320) if observation == "oversized" else None,
        )
        _, subscription = log.subscribe()
        if observation == "closed":
            log.close()
        original = engine.native.execute_plan

        async def observed(*args, **kwargs):
            kwargs["_stream_observer"] = log
            return await original(*args, **kwargs)

        monkeypatch.setattr(engine.native, "execute_plan", observed)
        result = await engine.invoke(plan, handle, {"start": "input"})
        assert result.status == "completed", result.error
        assert result.output == {"ok": True}
        assert len(model.calls) == 1
        assert engine.capabilities().streaming is False
        assert not any("stream" in name for name in engine.capabilities().native_extensions)
        if observation == "normal":
            frames, replay = log.subscribe()
            values = [json.loads(frame.payload) for frame in frames]
            assert {value["mode"] for value in values} == {"values", "updates"}
            assert [value["data"] for value in values if value["mode"] == "values"][-1] == {
                "ok": True
            }
            updates = [value for value in values if value["mode"] == "updates"]
            assert [value["task_reference"] for value in updates] == [
                "/do/0/prepare",
                "/do/1/answer",
                "/do/2/finish",
            ]
            assert all(
                set(value)
                == {"schema", "id", "invocation_id", "mode", "scope", "task_reference", "data"}
                for value in values
            )
            assert all(value["scope"] == "/do" for value in values)
            assert all(value["invocation_id"] == handle.invocation_id for value in values)
            replay.close()
        elif observation == "oversized":
            with pytest.raises(BufferError, match="frame_limit"):
                await subscription.receive(timeout=1)
            with pytest.raises(BufferError):
                log.subscribe()
        subscription.close()
        log.close()
    finally:
        await engine.shutdown()
        services.close()


async def test_private_observer_identity_is_checked_before_native_execution(services):
    from open_workflow_agent.errors import InvocationStateError

    engine = LangGraphWorkflowEngine()
    await engine.initialize(services)
    try:
        with pytest.raises(InvocationStateError, match="another invocation"):
            await engine.native.execute_plan(
                engine.executor,
                compile_workflow(),
                {},
                metadata={},
                invocation_id="one",
                _stream_observer=NativeStreamLog("two"),
            )
        assert services.model.calls == []
    finally:
        await engine.shutdown()
