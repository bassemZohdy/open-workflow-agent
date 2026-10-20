"""V1 admission and authoritative Task projection, without business side effects."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any, cast

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
from open_workflow_agent.observability import WorkflowEvent
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.streaming import StreamLimits
from open_workflow_agent.workflow import WorkflowExecutor, compile_workflow


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


async def stream_or_subscribe(client, binding, operation, task_id, *, context_id=None):
    if binding == "jsonrpc":
        params = (
            {"id": task_id}
            if operation == "subscribe"
            else {
                "message": message(
                    taskId=task_id, **({"contextId": context_id} if context_id else {})
                )
            }
        )
        return await client.post(
            "/a2a",
            headers={"A2A-Version": A2A_PROTOCOL_VERSION},
            json={
                "jsonrpc": "2.0",
                "id": "call-1",
                "method": "SubscribeToTask" if operation == "subscribe" else "SendStreamingMessage",
                "params": params,
            },
        )
    if operation == "subscribe":
        return await client.post(
            f"/a2a/tasks/{task_id}:subscribe", headers={"A2A-Version": A2A_PROTOCOL_VERSION}
        )
    return await client.post(
        "/a2a/message:stream",
        headers={"A2A-Version": A2A_PROTOCOL_VERSION},
        json={
            "message": message(taskId=task_id, **({"contextId": context_id} if context_id else {}))
        },
    )


def forbid_a2a_callbacks(monkeypatch, app, services):
    import open_workflow_agent.a2a as a2a_module

    calls = []

    def fail(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("A2A resume/subscription callback must not run")

    async def fail_async(*args, **kwargs):
        fail(*args, **kwargs)

    monkeypatch.setattr(app.state.engine, "resume", fail_async)
    monkeypatch.setattr(services, "resolve_original_definition", fail)
    monkeypatch.setattr(a2a_module, "a2a_sse_stream", fail)
    return calls


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
async def test_malformed_streaming_message_rejects_without_invocation_or_subscription(
    tmp_path, binding
):
    app, services, model = runtime(tmp_path, binding)
    invalid_message = message(parts=[{"text": "hello", "data": {"secret": "must-not-be-admitted"}}])
    params = {"message": invalid_message}
    invocation_count_before = services.invocations.connection.execute(
        "SELECT COUNT(*) FROM invocations"
    ).fetchone()[0]
    subscriber_ids_before = set(services.lifecycle_events._subscribers)

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/a2a" if binding == "jsonrpc" else "/a2a/message:stream",
                headers={"A2A-Version": A2A_PROTOCOL_VERSION},
                json={
                    "jsonrpc": "2.0",
                    "id": "stream-call-1",
                    "method": "SendStreamingMessage",
                    "params": params,
                }
                if binding == "jsonrpc"
                else params,
            )

        assert "error" in response.json()
        assert "must-not-be-admitted" not in response.text
        assert (
            services.invocations.connection.execute("SELECT COUNT(*) FROM invocations").fetchone()[
                0
            ]
            == invocation_count_before
        )
        assert model.calls == []
        assert set(services.lifecycle_events._subscribers) == subscriber_ids_before
    services.close()


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


@pytest.mark.asyncio
@pytest.mark.parametrize("binding", ["jsonrpc", "http_json"])
async def test_malformed_resume_rejects_before_invocation_or_delivery_mutation(
    tmp_path, monkeypatch, binding
):
    app, services, model = runtime(tmp_path, binding)
    handle = shell(services, status="waiting")
    before = services.invocations.get(handle.invocation_id)._persisted_payload
    invocation_count_before = services.invocations.connection.execute(
        "SELECT COUNT(*) FROM invocations"
    ).fetchone()[0]
    delivery_before = services.initial_deliveries.get(handle)
    inputs_before = services.invocations.connection.execute(
        "SELECT invocation_id,fingerprint,payload FROM invocation_inputs WHERE invocation_id=?",
        (handle.invocation_id,),
    ).fetchall()
    inbox_before = services.event_store.connection.execute(
        "SELECT sequence,scope,source,event_id,envelope,digest,submitted_digest "
        "FROM event_inbox ORDER BY sequence"
    ).fetchall()
    async with app.router.lifespan_context(app):
        callbacks = forbid_a2a_callbacks(monkeypatch, app, services)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            invalid_message = message(
                taskId=handle.invocation_id,
                contextId="original-context",
                parts=[{"text": "hello", "data": {"secret": "must-not-be-admitted"}}],
            )
            params = {"message": invalid_message}
            response = await client.post(
                "/a2a" if binding == "jsonrpc" else "/a2a/message:send",
                headers={"A2A-Version": A2A_PROTOCOL_VERSION},
                json={
                    "jsonrpc": "2.0",
                    "id": "call-1",
                    "method": "SendMessage",
                    "params": params,
                }
                if binding == "jsonrpc"
                else params,
            )
        assert "error" in response.json()
        assert "must-not-be-admitted" not in response.text
        current = services.invocations.get(handle.invocation_id)
        assert current is not None and current.status == "waiting"
        assert current._persisted_payload == before
        assert (
            services.invocations.connection.execute("SELECT COUNT(*) FROM invocations").fetchone()[
                0
            ]
            == invocation_count_before
        )
        assert services.initial_deliveries.get(current) == delivery_before
        assert (
            services.invocations.connection.execute(
                "SELECT invocation_id,fingerprint,payload FROM invocation_inputs "
                "WHERE invocation_id=?",
                (handle.invocation_id,),
            ).fetchall()
            == inputs_before
        )
        assert (
            services.event_store.connection.execute(
                "SELECT sequence,scope,source,event_id,envelope,digest,submitted_digest "
                "FROM event_inbox ORDER BY sequence"
            ).fetchall()
            == inbox_before
        )
        assert model.calls == []
        assert callbacks == []
    services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("binding", ["jsonrpc", "http_json"])
async def test_stream_resume_mismatched_context_rejects_before_resume_or_subscription(
    tmp_path, monkeypatch, binding
):
    app, services, model = runtime(tmp_path, binding)
    handle = shell(services, status="waiting")
    before = services.invocations.get(handle.invocation_id)._persisted_payload
    async with app.router.lifespan_context(app):
        callbacks = forbid_a2a_callbacks(monkeypatch, app, services)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await stream_or_subscribe(
                client,
                binding,
                "stream",
                handle.invocation_id,
                context_id="foreign-context",
            )
        body = response.json()
        assert "error" in body
        assert "context" in response.text
        assert services.invocations.get(handle.invocation_id)._persisted_payload == before
        assert (
            services.invocations.connection.execute(
                "SELECT 1 FROM invocation_inputs WHERE invocation_id=?", (handle.invocation_id,)
            ).fetchone()
            is None
        )
        assert model.calls == []
        assert callbacks == []
    services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("binding", ["jsonrpc", "http_json"])
async def test_stream_resume_with_matching_context_is_sanitized_as_unsupported_without_callbacks(
    tmp_path, monkeypatch, binding
):
    app, services, model = runtime(tmp_path, binding)
    handle = shell(services, status="waiting")
    before = services.invocations.get(handle.invocation_id)._persisted_payload
    async with app.router.lifespan_context(app):
        callbacks = forbid_a2a_callbacks(monkeypatch, app, services)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await stream_or_subscribe(
                client,
                binding,
                "stream",
                handle.invocation_id,
                context_id="original-context",
            )
        body = response.json()
        assert "error" in body
        assert "resume" in response.text.lower() or "unsupported" in response.text.lower()
        assert services.invocations.get(handle.invocation_id)._persisted_payload == before
        assert model.calls == []
        assert callbacks == []
    services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("binding", ["jsonrpc", "http_json"])
@pytest.mark.parametrize("operation", ["stream", "subscribe"])
async def test_unknown_and_foreign_a2a_tasks_share_sanitized_not_found_without_callbacks(
    tmp_path, monkeypatch, binding, operation
):
    app, services, model = runtime(tmp_path, binding)
    foreign = services.invocations.create(
        engine="portable",
        session_id="foreign-context-secret",
        user_id=None,
        workflow_name="foreign-workflow",
        workflow_version="1.0.0",
        workflow_fingerprint="foreign-fingerprint",
        owner_principal="foreign-principal-secret",
    )
    unknown_id = "unknown-task-secret"
    async with app.router.lifespan_context(app):
        callbacks = forbid_a2a_callbacks(monkeypatch, app, services)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            results = []
            for task_id in (unknown_id, foreign.invocation_id):
                response = await stream_or_subscribe(client, binding, operation, task_id)
                results.append((response.status_code, response.json()))
                assert task_id not in response.text
                assert "foreign-principal-secret" not in response.text
                assert "foreign-context-secret" not in response.text
            assert results[0] == results[1]
            assert "not found" in json.dumps(results[0][1]).lower()
        assert model.calls == []
        assert callbacks == []
    services.close()


def test_completed_null_is_an_explicit_artifact(tmp_path):
    _app, services, _model = runtime(tmp_path, "jsonrpc")
    handle = shell(services, status="completed", output=None)
    task = project_a2a_task(handle, services=services)
    assert task["artifacts"][0]["parts"] == [{"data": None, "mediaType": "application/json"}]
    services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("binding", ["jsonrpc", "http_json"])
async def test_public_subscription_reads_another_services_terminal_commit_without_local_event(
    tmp_path, monkeypatch, binding
):
    app, services, model = runtime(tmp_path, binding)
    handle = shell(services)
    other_model = FakeModel()
    other = RuntimeServices(services.config, model=other_model, database_root=tmp_path)
    initial_projection = asyncio.Event()

    import open_workflow_agent.a2a_streaming as streaming_module

    project = streaming_module.project_a2a_task
    initial_tasks = []

    def observe_initial_projection(*args, **kwargs):
        task = project(*args, **kwargs)
        if not initial_tasks:
            initial_tasks.append(task)
            initial_projection.set()
        return task

    monkeypatch.setattr(streaming_module, "project_a2a_task", observe_initial_projection)

    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                request = asyncio.create_task(
                    stream_or_subscribe(client, binding, "subscribe", handle.invocation_id)
                )
                # The public endpoint has opened its stream and projected the initial
                # working Task before the independent worker commits a terminal result.
                await asyncio.wait_for(initial_projection.wait(), timeout=3)
                assert initial_tasks[0]["id"] == handle.invocation_id
                assert initial_tasks[0]["contextId"] == "original-context"
                assert initial_tasks[0]["status"]["state"] == "TASK_STATE_WORKING"

                worker_handle = other.invocations.get(handle.invocation_id)
                assert worker_handle is not None
                other.invocations.update(
                    worker_handle,
                    status="completed",
                    output={"answer": "committed by another worker", "count": 2},
                )

                response = await asyncio.wait_for(request, timeout=5)
                assert response.status_code == 200, response.text
                assert response.headers["content-type"].startswith("text/event-stream")
                frames = [
                    json.loads(line.removeprefix("data: "))
                    for line in response.text.splitlines()
                    if line.startswith("data: ")
                ]

                if binding == "jsonrpc":
                    assert all(
                        frame["jsonrpc"] == "2.0"
                        and frame["id"] == "call-1"
                        and set(frame) == {"jsonrpc", "id", "result"}
                        for frame in frames
                    )
                    events = [frame["result"] for frame in frames]
                else:
                    events = frames

                assert [set(event) for event in events] == [
                    {"task"},
                    {"artifactUpdate"},
                    {"statusUpdate"},
                ]
                snapshot = events[0]["task"]
                assert snapshot["id"] == handle.invocation_id
                assert snapshot["contextId"] == "original-context"
                assert snapshot["status"]["state"] == "TASK_STATE_WORKING"

                artifact = events[1]["artifactUpdate"]
                assert artifact["taskId"] == handle.invocation_id
                assert artifact["contextId"] == "original-context"
                assert artifact["artifact"]["parts"] == [
                    {
                        "data": {"answer": "committed by another worker", "count": 2},
                        "mediaType": "application/json",
                    }
                ]
                terminal = events[2]["statusUpdate"]
                assert terminal["taskId"] == handle.invocation_id
                assert terminal["contextId"] == "original-context"
                assert terminal["status"]["state"] == "TASK_STATE_COMPLETED"
                assert model.calls == []
                assert other_model.calls == []
    finally:
        other.close()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("binding", ["jsonrpc", "http_json"])
async def test_public_subscription_skips_malformed_event_and_closes_after_terminal(
    tmp_path, monkeypatch, binding
):
    import open_workflow_agent.a2a_streaming as streaming_module

    app, services, model = runtime(tmp_path, binding)
    handle = shell(services)
    initial_projection = asyncio.Event()
    project = streaming_module.project_a2a_task

    def observe_initial_projection(*args, **kwargs):
        task = project(*args, **kwargs)
        initial_projection.set()
        return task

    monkeypatch.setattr(streaming_module, "project_a2a_task", observe_initial_projection)

    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                request = asyncio.create_task(
                    stream_or_subscribe(client, binding, "subscribe", handle.invocation_id)
                )
                await asyncio.wait_for(initial_projection.wait(), timeout=3)
                assert len(services.lifecycle_events._subscribers) == 1
                queue = next(iter(services.lifecycle_events._subscribers.values()))

                malformed = WorkflowEvent(
                    "WorkflowStatusChanged",
                    invocation_id=handle.invocation_id,
                    event_id="malformed-event",
                    status="running",
                ).to_cloud_event()
                queue.put_nowait(replace(malformed, data=cast(Any, ["malformed-payload"])))

                current = services.invocations.get(handle.invocation_id)
                assert current is not None
                services.invocations.update(
                    current,
                    status="completed",
                    output={"answer": "terminal after malformed event"},
                )
                services.lifecycle_events.emit(
                    WorkflowEvent(
                        "WorkflowStatusChanged",
                        invocation_id=handle.invocation_id,
                        event_id="terminal-event",
                        status="completed",
                    )
                )

                response = await asyncio.wait_for(request, timeout=5)
                assert response.status_code == 200, response.text
                assert response.headers["content-type"].startswith("text/event-stream")
                frames = [
                    json.loads(line.removeprefix("data: "))
                    for line in response.text.splitlines()
                    if line.startswith("data: ")
                ]
                events = [frame["result"] for frame in frames] if binding == "jsonrpc" else frames
                assert [set(event) for event in events] == [
                    {"task"},
                    {"artifactUpdate"},
                    {"statusUpdate"},
                ]
                assert events[0]["task"]["status"]["state"] == "TASK_STATE_WORKING"
                assert events[1]["artifactUpdate"]["artifact"]["parts"] == [
                    {
                        "data": {"answer": "terminal after malformed event"},
                        "mediaType": "application/json",
                    }
                ]
                assert events[2]["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"
                assert services.lifecycle_events._subscribers == {}
                assert model.calls == []
    finally:
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("binding", ["jsonrpc", "http_json"])
async def test_public_subscription_byte_limit_closes_after_ordered_snapshot_without_cancelling(
    tmp_path, monkeypatch, binding
):
    import open_workflow_agent.a2a as a2a_module
    import open_workflow_agent.a2a_streaming as streaming_module

    monkeypatch.setattr(a2a_module, "StreamLimits", lambda: StreamLimits(max_bytes=1024))
    app, services, model = runtime(tmp_path, binding)
    handle = shell(services)
    initial_projection = asyncio.Event()
    project = streaming_module.project_a2a_task

    def observe_initial_projection(*args, **kwargs):
        task = project(*args, **kwargs)
        initial_projection.set()
        return task

    monkeypatch.setattr(streaming_module, "project_a2a_task", observe_initial_projection)

    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                request = asyncio.create_task(
                    stream_or_subscribe(client, binding, "subscribe", handle.invocation_id)
                )
                await asyncio.wait_for(initial_projection.wait(), timeout=3)

                current = services.invocations.get(handle.invocation_id)
                assert current is not None
                services.invocations.update(
                    current,
                    status="completed",
                    output={"oversized": "x" * 4096},
                )
                services.lifecycle_events.emit(
                    WorkflowEvent(
                        "WorkflowStatusChanged",
                        invocation_id=handle.invocation_id,
                        event_id="oversized-terminal-event",
                        status="completed",
                    )
                )

                response = await asyncio.wait_for(request, timeout=5)
                assert response.status_code == 200, response.text
                assert response.headers["content-type"].startswith("text/event-stream")
                frames = [
                    json.loads(line.removeprefix("data: "))
                    for line in response.text.splitlines()
                    if line.startswith("data: ")
                ]
                if binding == "jsonrpc":
                    assert len(frames) == 1
                    assert frames[0]["jsonrpc"] == "2.0"
                    assert frames[0]["id"] == "call-1"
                    assert set(frames[0]) == {"jsonrpc", "id", "result"}
                    events = [frames[0]["result"]]
                else:
                    events = frames

                assert [set(event) for event in events] == [{"task"}]
                snapshot = events[0]["task"]
                assert snapshot["id"] == handle.invocation_id
                assert snapshot["contextId"] == "original-context"
                assert snapshot["status"]["state"] == "TASK_STATE_WORKING"
                assert services.invocations.get(handle.invocation_id).status == "completed"
                assert services.lifecycle_events._subscribers == {}
                assert model.calls == []
    finally:
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("binding", ["jsonrpc", "http_json"])
@pytest.mark.parametrize(
    ("status", "output", "error", "expected_state"),
    [
        ("completed", {"answer": "durable result", "count": 3}, None, "TASK_STATE_COMPLETED"),
        ("completed", None, None, "TASK_STATE_COMPLETED"),
        (
            "faulted",
            None,
            {"code": "workflow_execution_error", "message": "private engine detail"},
            "TASK_STATE_FAILED",
        ),
    ],
)
async def test_public_get_task_projects_another_services_terminal_commit_read_only(
    tmp_path, monkeypatch, binding, status, output, error, expected_state
):
    """Public GetTask reads a terminal SQLite commit without dispatching work."""
    _source_app, source, source_model = runtime(tmp_path, binding)
    handle = shell(source)
    source.invocations.update(handle, status=status, output=output, error=error)

    reader_model = FakeModel()
    reader = RuntimeServices(source.config, model=reader_model, database_root=tmp_path)
    app = create_app(config=source.config, services=reader, engine=PortableWorkflowEngine())
    expected = project_a2a_task(reader.invocations.get(handle.invocation_id), services=reader)
    persisted_before = reader.invocations.get(handle.invocation_id)._persisted_payload
    assert expected["id"] == handle.invocation_id
    assert expected["contextId"] == "original-context"
    assert expected["status"]["state"] == expected_state
    if status == "completed":
        assert expected["artifacts"] == [
            {
                "artifactId": f"{handle.invocation_id}:output",
                "name": "workflow-output",
                "parts": [
                    {
                        "data": output,
                        "mediaType": "application/json",
                    }
                ],
            }
        ]
    else:
        assert expected["status"]["message"] == {
            "messageId": f"{handle.invocation_id}:error",
            "contextId": "original-context",
            "taskId": handle.invocation_id,
            "role": "ROLE_AGENT",
            "parts": [
                {
                    "text": "workflow failed: workflow_execution_error",
                    "mediaType": "text/plain",
                }
            ],
        }
        assert "private engine detail" not in json.dumps(expected)

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("terminal GetTask must not dispatch or resume workflow work")

    try:
        async with app.router.lifespan_context(app):
            monkeypatch.setattr(app.state.engine, "invoke", forbidden)
            monkeypatch.setattr(app.state.engine, "resume", forbidden)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                responses = []
                for request_id in ("terminal-read-1", "terminal-read-2"):
                    if binding == "jsonrpc":
                        response = await client.post(
                            "/a2a",
                            headers={"A2A-Version": A2A_PROTOCOL_VERSION},
                            json={
                                "jsonrpc": "2.0",
                                "id": request_id,
                                "method": "GetTask",
                                "params": {"id": handle.invocation_id},
                            },
                        )
                        assert response.status_code == 200, response.text
                        body = response.json()
                        assert body["id"] == request_id
                        task = body["result"]
                    else:
                        response = await client.get(
                            f"/a2a/tasks/{handle.invocation_id}",
                            headers={"A2A-Version": A2A_PROTOCOL_VERSION},
                        )
                        assert response.status_code == 200, response.text
                        task = response.json()
                    responses.append(task)

                assert responses == [expected, expected]
                assert reader.invocations.get(handle.invocation_id)._persisted_payload == (
                    persisted_before
                )
                assert source.invocations.get(handle.invocation_id)._persisted_payload == (
                    persisted_before
                )
                assert source_model.calls == []
                assert reader_model.calls == []
    finally:
        reader.close()
        source.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("completed", "TASK_STATE_COMPLETED"),
        ("faulted", "TASK_STATE_FAILED"),
        ("cancelled", "TASK_STATE_CANCELED"),
    ],
)
async def test_a2a_stream_of_terminal_task_contains_only_snapshot_and_closes(
    tmp_path, status, expected
):
    _app, services, _model = runtime(tmp_path, "jsonrpc")
    handle = shell(services, status=status, output={"answer": "done"})
    stream = a2a_sse_stream(
        services.lifecycle_events,
        services.invocations,
        handle.invocation_id,
    )

    frame = await anext(stream)
    payload = json.loads(frame.decode().split("data: ", 1)[1])
    with pytest.raises(StopAsyncIteration):
        await anext(stream)

    assert payload["task"]["status"]["state"] == expected
    assert services.lifecycle_events._subscribers == {}
    services.close()


@pytest.mark.asyncio
async def test_a2a_stream_event_limit_keeps_initial_snapshot_and_closes(tmp_path):
    _app, services, _model = runtime(tmp_path, "jsonrpc")
    handle = shell(services)
    stream = a2a_sse_stream(
        services.lifecycle_events,
        services.invocations,
        handle.invocation_id,
        limits=StreamLimits(max_events=1),
        jsonrpc_id="stream-call-1",
    )
    snapshot = await anext(stream)
    current = services.invocations.get(handle.invocation_id)
    services.invocations.update(current, status="completed", output={"answer": "done"})
    services.lifecycle_events.emit(
        WorkflowEvent(
            "WorkflowStatusChanged",
            invocation_id=handle.invocation_id,
            event_id="terminal-event",
            status="completed",
        )
    )

    with pytest.raises(StopAsyncIteration):
        await anext(stream)

    payload = json.loads(snapshot.decode().split("data: ", 1)[1])
    assert payload["jsonrpc"] == "2.0"
    assert payload["id"] == "stream-call-1"
    assert payload["result"]["task"]["status"]["state"] == "TASK_STATE_WORKING"
    assert services.lifecycle_events._subscribers == {}
    services.close()


@pytest.mark.asyncio
async def test_a2a_stream_event_limit_closes_after_terminal_artifact(tmp_path):
    _app, services, _model = runtime(tmp_path, "jsonrpc")
    handle = shell(services)
    stream = a2a_sse_stream(
        services.lifecycle_events,
        services.invocations,
        handle.invocation_id,
        limits=StreamLimits(max_events=2),
    )
    await anext(stream)
    current = services.invocations.get(handle.invocation_id)
    services.invocations.update(current, status="completed", output={"answer": "done"})
    services.lifecycle_events.emit(
        WorkflowEvent(
            "WorkflowStatusChanged",
            invocation_id=handle.invocation_id,
            event_id="terminal-event",
            status="completed",
        )
    )

    artifact = json.loads((await anext(stream)).decode().split("data: ", 1)[1])
    assert artifact["artifactUpdate"]["lastChunk"] is True
    with pytest.raises(StopAsyncIteration):
        await anext(stream)

    assert services.lifecycle_events._subscribers == {}
    services.close()


@pytest.mark.asyncio
async def test_a2a_stream_backpressure_closes_without_leaking_subscription(tmp_path):
    _app, services, _model = runtime(tmp_path, "jsonrpc")
    handle = shell(services)
    stream = a2a_sse_stream(
        services.lifecycle_events,
        services.invocations,
        handle.invocation_id,
        limits=StreamLimits(timeout_seconds=0.05, queue_size=1),
    )
    snapshot = await anext(stream)
    for event_id in ("queued-event", "overflow-event"):
        services.lifecycle_events.emit(
            WorkflowEvent(
                "WorkflowStatusChanged",
                invocation_id=handle.invocation_id,
                event_id=event_id,
                status="running",
            )
        )

    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(stream), timeout=1)

    assert b"TASK_STATE_WORKING" in snapshot
    assert services.lifecycle_events._subscribers == {}
    services.close()


@pytest.mark.asyncio
async def test_a2a_stream_timeout_closes_after_snapshot(tmp_path):
    _app, services, _model = runtime(tmp_path, "jsonrpc")
    handle = shell(services)
    stream = a2a_sse_stream(
        services.lifecycle_events,
        services.invocations,
        handle.invocation_id,
        limits=StreamLimits(timeout_seconds=0.01),
    )
    snapshot = await anext(stream)

    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(stream), timeout=1)

    assert b"TASK_STATE_WORKING" in snapshot
    assert services.lifecycle_events._subscribers == {}
    services.close()


@pytest.mark.asyncio
async def test_a2a_stream_byte_limit_rejects_oversized_snapshot_and_closes(tmp_path):
    _app, services, _model = runtime(tmp_path, "jsonrpc")
    handle = shell(services)
    stream = a2a_sse_stream(
        services.lifecycle_events,
        services.invocations,
        handle.invocation_id,
        limits=StreamLimits(max_bytes=256, timeout_seconds=0.01),
        jsonrpc_id="x" * 512,
    )

    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(stream), timeout=1)

    assert services.lifecycle_events._subscribers == {}
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
async def test_real_timer_wait_projects_authoritative_terminal_task_after_get_task(
    tmp_path, monkeypatch
):
    current = [1000.0]
    original_initialize = WorkflowExecutor.__init__

    def initialize(executor, *args, **kwargs):
        original_initialize(executor, *args, **kwargs)
        executor._absolute_clock = lambda: current[0]

    monkeypatch.setattr(WorkflowExecutor, "__init__", initialize)
    config = RuntimeConfig.model_validate(
        {
            "workflow": {
                "catalog": [
                    {
                        "document": {
                            "dsl": "1.0.3",
                            "namespace": "a2a",
                            "name": "timed",
                            "version": "1.0.0",
                        },
                        "do": [
                            {"pause": {"wait": {"seconds": 60}}},
                            {"finish": {"set": {"timer_fired": True}}},
                        ],
                    }
                ]
            },
            "a2a": {
                "enabled": True,
                "skills": [{"id": "timer", "workflow": "timed"}],
            },
        }
    )
    services = RuntimeServices(config, model=FakeModel(), database_root=tmp_path)
    app = create_app(config=config, services=services, engine=PortableWorkflowEngine())

    async with app.router.lifespan_context(app):
        dispatcher = app.state.engine._timer_dispatcher
        # Hold the polling clock before the persisted due time so the test can
        # drive the actual dispatcher at a deterministic timestamp below.
        dispatcher.clock = lambda: current[0]
        dispatcher.poll_seconds = 3600
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            started = await client.post(
                "/a2a",
                headers={"A2A-Version": A2A_PROTOCOL_VERSION},
                json={
                    "jsonrpc": "2.0",
                    "id": "timer-start",
                    "method": "SendMessage",
                    "params": {
                        "message": message(metadata={"skillId": "timer"}),
                        "configuration": {"returnImmediately": True},
                    },
                },
            )
            assert started.status_code == 200, started.text
            task_id = started.json()["result"]["task"]["id"]

            async with asyncio.timeout(5):
                while True:
                    handle = services.invocations.get(task_id)
                    if (
                        handle is not None
                        and handle.status == "waiting"
                        and task_id not in dispatcher.engine._active
                        and task_id not in dispatcher.engine._ownership_tasks
                    ):
                        break
                    await asyncio.sleep(0.01)

            waiting = await client.post(
                "/a2a",
                headers={"A2A-Version": A2A_PROTOCOL_VERSION},
                json={
                    "jsonrpc": "2.0",
                    "id": "timer-get-waiting",
                    "method": "GetTask",
                    "params": {"id": task_id},
                },
            )
            assert waiting.status_code == 200, waiting.text
            waiting_task = waiting.json()["result"]
            assert waiting_task["status"]["state"] == "TASK_STATE_WORKING"
            assert waiting_task["id"] == task_id
            handle = services.invocations.get(task_id)
            assert handle is not None
            registration = services.timers.pending_for_invocation(
                task_id, handle.workflow_fingerprint
            )[0]
            assert registration.due_at == 1060.0

            current[0] = 1060.0
            assert await dispatcher.tick(now=current[0], invocation_id=task_id) == 1
            async with asyncio.timeout(5):
                while task_id in dispatcher._workers:
                    await asyncio.sleep(0.01)

            completed = await client.post(
                "/a2a",
                headers={"A2A-Version": A2A_PROTOCOL_VERSION},
                json={
                    "jsonrpc": "2.0",
                    "id": "timer-get-completed",
                    "method": "GetTask",
                    "params": {"id": task_id},
                },
            )
            assert completed.status_code == 200, completed.text
            completed_task = completed.json()["result"]
            assert completed_task["status"]["state"] == "TASK_STATE_COMPLETED"
            assert completed_task["id"] == task_id
            assert completed_task["contextId"] == waiting_task["contextId"]
            assert completed_task["artifacts"][0]["parts"][0]["data"] == {"timer_fired": True}

            # A second due scan cannot revise the first terminal projection.
            assert await dispatcher.tick(now=current[0], invocation_id=task_id) == 0
            repeated = await client.post(
                "/a2a",
                headers={"A2A-Version": A2A_PROTOCOL_VERSION},
                json={
                    "jsonrpc": "2.0",
                    "id": "timer-get-repeated",
                    "method": "GetTask",
                    "params": {"id": task_id},
                },
            )
            assert repeated.status_code == 200, repeated.text
            assert repeated.json()["result"] == completed_task

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
