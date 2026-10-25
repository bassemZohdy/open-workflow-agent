"""A2A v1 streaming/resubscription over the common lifecycle infrastructure.

Covers the bounded A2A-7 profile: ``SendStreamingMessage`` /
``SubscribeToTask`` (JSON-RPC) and ``message:stream`` / ``tasks/{id}:subscribe``
(HTTP+JSON), protocol-native frame translation, and disconnect safety.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from types import SimpleNamespace

import httpx
import pytest
from open_workflow_agent.a2a import A2A_PROTOCOL_VERSION as V1
from open_workflow_agent.a2a_streaming import a2a_sse_stream
from open_workflow_agent.api import create_app
from open_workflow_agent.approvals import APPROVAL_DECISION_EVENT, APPROVAL_REQUEST_EVENT
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.observability import WorkflowEvent
from open_workflow_agent.persistence import ExecutionHandle
from open_workflow_agent.services import RuntimeServices

HEADERS = {"a2a-version": V1}


def _message(text: str = "hello", **extra: object) -> dict[str, object]:
    message: dict[str, object] = {
        "messageId": "fixture-message",
        "role": "ROLE_USER",
        "parts": [{"text": text}],
    }
    message.update(extra)
    return message


def _make_app(tmp_path, config: RuntimeConfig):
    services = RuntimeServices(
        config, model=FakeModel({"response": "stream-reply"}), database_root=tmp_path
    )
    app = create_app(config=config, services=services)
    return app, services


def _stream_result(value: object, *, request_id: str | int | None) -> dict[str, object]:
    """Validate binding/correlation before exposing a streamed result."""
    assert isinstance(value, dict), value
    if request_id is not None:
        assert set(value) == {"jsonrpc", "id", "result"}, value
        assert value["jsonrpc"] == "2.0", value
        assert type(value["id"]) is type(request_id) and value["id"] == request_id, value
        value = value["result"]
        assert isinstance(value, dict), value
    else:
        assert not {"jsonrpc", "id", "result"}.intersection(value), value
    assert len(value) == 1 and set(value) <= {
        "task",
        "message",
        "statusUpdate",
        "artifactUpdate",
    }, value
    return value


async def _drain_sse(
    response: httpx.Response, timeout: float = 15.0, *, request_id: str | int | None = None
) -> list[dict[str, object]]:
    """Collect SSE data frames until the server closes the stream."""

    frames: list[dict[str, object]] = []

    async def collect() -> None:
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                frames.append(
                    _stream_result(json.loads(line.removeprefix("data: ")), request_id=request_id)
                )

    try:
        await asyncio.wait_for(collect(), timeout)
    except TimeoutError as exc:
        raise AssertionError(f"stream did not terminate; frames={frames}") from exc
    return frames


def _updates(frames: list[dict[str, object]]) -> list[dict[str, object]]:
    return [frame["statusUpdate"] for frame in frames if "statusUpdate" in frame]


async def _stream_asgi(
    app: object,
    *,
    method: str,
    path: str,
    body: bytes,
    headers: list[tuple[bytes, bytes]],
    request_id: str | int | None = None,
    disconnect: asyncio.Event | None = None,
) -> tuple[asyncio.Task[None], Callable[[], list[dict[str, object]]]]:
    """Run one ASGI request as a live task with incremental body access.

    httpx's ASGITransport buffers the whole response, which cannot exercise a
    server that streams while the client acts concurrently. This harness calls
    the ASGI application directly and exposes the frames received so far.
    """

    received: list[bytes] = []
    parsed: list[dict[str, object]] = []
    done = asyncio.Event()
    body_sent = asyncio.Event()

    async def receive() -> dict[str, object]:
        # The first call delivers the request body; later calls (Starlette's
        # disconnect listener) must genuinely suspend or the loop would
        # busy-spin without ever yielding to the event loop.
        if body_sent.is_set():
            await (disconnect or done).wait()
            return {"type": "http.disconnect"}
        body_sent.set()
        return {"type": "http.request", "body": body, "more_body": False}

    def parse() -> list[dict[str, object]]:
        raw = b"".join(received)
        for line in raw.split(b"\n"):
            if line.startswith(b"data: "):
                frame = _stream_result(
                    json.loads(line.removeprefix(b"data: ").decode("utf-8")), request_id=request_id
                )
                if frame not in parsed:
                    parsed.append(frame)
        return parsed

    async def send(message: dict[str, object]) -> None:
        if message["type"] == "http.response.body":
            received.append(message.get("body", b""))
            parse()
            if not message.get("more_body", False):
                done.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "scheme": "http",
        "server": ("test", 80),
        "client": ("test", 123),
        "headers": headers,
    }
    app_task = asyncio.create_task(app(scope, receive, send))  # type: ignore[operator]
    return app_task, parse


@pytest.mark.asyncio
async def test_streaming_message_emits_protocol_frames_until_terminal(tmp_path) -> None:
    config = RuntimeConfig.model_validate({"a2a": {"enabled": True}})
    app, _services = _make_app(tmp_path, config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.stream(
                "POST",
                "/a2a",
                headers={**HEADERS, "Accept": "text/event-stream"},
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "SendStreamingMessage",
                    "params": {"message": _message()},
                },
            ) as response:
                assert response.status_code == 200
                assert response.headers["content-type"].startswith("text/event-stream")
                frames = await _drain_sse(response, request_id=1)

    assert any("task" in frame for frame in frames), frames
    updates = _updates(frames)
    assert updates, frames
    assert [str(update["status"]["state"]) for update in updates][-1] == "TASK_STATE_COMPLETED"
    terminal = [
        update
        for update in updates
        if update["status"]["state"]
        in {"TASK_STATE_COMPLETED", "TASK_STATE_FAILED", "TASK_STATE_CANCELED"}
    ]
    assert [str(update["status"]["state"]) for update in terminal] == ["TASK_STATE_COMPLETED"]

    task_frame = next(frame["task"] for frame in frames if "task" in frame)
    assert isinstance(task_frame["id"], str) and task_frame["id"]
    assert isinstance(task_frame["contextId"], str) and task_frame["contextId"]
    for update in updates:
        assert update["taskId"] == task_frame["id"]
        assert update["contextId"] == task_frame["contextId"]


@pytest.mark.asyncio
async def test_streaming_message_completes_with_artifact_update(tmp_path) -> None:
    config = RuntimeConfig.model_validate({"a2a": {"enabled": True}})
    app, _services = _make_app(tmp_path, config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.stream(
                "POST",
                "/a2a",
                headers=HEADERS,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "SendStreamingMessage",
                    "params": {"message": _message()},
                },
            ) as response:
                frames = await _drain_sse(response, request_id=1)

    artifact_positions = [i for i, frame in enumerate(frames) if "artifactUpdate" in frame]
    terminal_positions = [
        i
        for i, frame in enumerate(frames)
        if "statusUpdate" in frame
        and frame["statusUpdate"]["status"]["state"]
        in {"TASK_STATE_COMPLETED", "TASK_STATE_FAILED", "TASK_STATE_CANCELED"}
    ]
    assert artifact_positions and terminal_positions
    assert artifact_positions[0] < terminal_positions[-1]
    artifact_update = frames[artifact_positions[0]]["artifactUpdate"]
    assert artifact_update["lastChunk"] is True
    assert artifact_update["artifact"]["parts"][0]["data"] == {"response": "stream-reply"}


@pytest.mark.asyncio
async def test_streaming_message_waits_through_input_required(tmp_path) -> None:
    config = RuntimeConfig.model_validate(
        {
            "approvals": {"enabled": True, "operator_security_profile": "operator"},
            "security": {
                "profiles": {
                    "operator": {"type": "bearer", "token": {"from_env": "OWA_TEST_OPERATOR"}}
                }
            },
            "workflow": {
                "catalog": [
                    {
                        "document": {
                            "dsl": "1.0.3",
                            "namespace": "a2a-stream",
                            "name": "approval-flow",
                            "version": "1.0.0",
                        },
                        "do": [
                            {
                                "approval": {
                                    "listen": {
                                        "to": {
                                            "one": {
                                                "with": {
                                                    "type": APPROVAL_DECISION_EVENT,
                                                    "subject": "approval-a2a-stream-1",
                                                }
                                            }
                                        },
                                        "read": "data",
                                    }
                                }
                            },
                            {"finish": {"set": {"approved": True}}},
                        ],
                    }
                ]
            },
            "a2a": {
                "enabled": True,
                "skills": [{"id": "approve", "workflow": "approval-flow"}],
            },
        }
    )
    app, services = _make_app(tmp_path, config)
    async with app.router.lifespan_context(app):
        request = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "SendStreamingMessage",
                "params": {"message": _message(metadata={"skillId": "approve"})},
            }
        ).encode()
        headers = [(b"a2a-version", V1.encode()), (b"content-type", b"application/json")]
        app_task, parse = await _stream_asgi(
            app, method="POST", path="/a2a", body=request, headers=headers, request_id=1
        )

        try:
            # Wait until the workflow reports input-required on the live stream.
            for _ in range(200):
                frames = parse()
                if any(
                    "statusUpdate" in frame
                    and frame["statusUpdate"]["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
                    for frame in frames
                ):
                    break
                await asyncio.sleep(0.05)
            else:
                raise AssertionError(f"never saw INPUT_REQUIRED; frames={parse()}")

            await services.event_bus.publish(
                {
                    "id": "approval-a2a-stream-1",
                    "subject": "approval-a2a-stream-1",
                    "type": APPROVAL_REQUEST_EVENT,
                    "data": {"question": "continue?"},
                },
                default_source="urn:a2a-stream",
            )
            await services.approvals.decide(
                "approval-a2a-stream-1",
                decision="approved",
                operator_id="operator-1",
                value={"approved": True},
                operation_key="approval-a2a-stream-1",
            )

            await asyncio.wait_for(app_task, timeout=15)
        finally:
            if not app_task.done():
                app_task.cancel()
                await asyncio.gather(app_task, return_exceptions=True)

    frames = parse()
    states = [str(update["status"]["state"]) for update in _updates(frames)]
    assert "TASK_STATE_INPUT_REQUIRED" in states
    assert states[-1] == "TASK_STATE_COMPLETED"
    terminal = [
        update
        for update in _updates(frames)
        if update["status"]["state"]
        in {"TASK_STATE_COMPLETED", "TASK_STATE_FAILED", "TASK_STATE_CANCELED"}
    ]
    assert [str(update["status"]["state"]) for update in terminal] == ["TASK_STATE_COMPLETED"]


@pytest.mark.asyncio
async def test_resubscribe_streams_existing_task_and_rejects_unknown(tmp_path) -> None:
    config = RuntimeConfig.model_validate({"a2a": {"enabled": True}})
    app, services = _make_app(tmp_path, config)
    async with app.router.lifespan_context(app):
        handle = services.invocations.create(
            engine="adk",
            session_id="ctx-resub",
            user_id=None,
            workflow_name="flow",
            workflow_version="1.0.0",
            workflow_fingerprint="fingerprint",
        )
        services.invocations.update(handle, status="completed", output={"done": True})

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.stream(
                "POST",
                "/a2a",
                headers=HEADERS,
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "SubscribeToTask",
                    "params": {"id": handle.invocation_id},
                },
            ) as response:
                assert response.status_code == 200
                frames = await _drain_sse(response, request_id=2)
            assert len(frames) == 1
            task = frames[0]["task"]
            assert task["id"] == handle.invocation_id
            assert task["status"]["state"] == "TASK_STATE_COMPLETED"
            assert task["contextId"] == "ctx-resub"

            missing = await client.post(
                "/a2a",
                headers=HEADERS,
                json={
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "SubscribeToTask",
                    "params": {"id": "missing"},
                },
            )
            assert missing.json()["error"]["code"] == -32001


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("mismatched-context", -32602),
        ("matching-context-async-resume", -32004),
        ("unknown-task", -32001),
        ("foreign-task", -32001),
    ],
)
async def test_stream_resume_rejects_existing_task_references_without_side_effects(
    tmp_path, monkeypatch, case, expected_code
) -> None:
    if case == "foreign-task":
        monkeypatch.setenv("OWA_TEST_STREAM_OWNER", "stream-token")
        config = RuntimeConfig.model_validate(
            {
                "a2a": {"enabled": True, "security_profile": "partner"},
                "security": {
                    "profiles": {
                        "partner": {
                            "type": "bearer",
                            "token": {"from_env": "OWA_TEST_STREAM_OWNER"},
                            "principal": "requester",
                        }
                    }
                },
            }
        )
    else:
        config = RuntimeConfig.model_validate({"a2a": {"enabled": True}})
    app, services = _make_app(tmp_path, config)
    async with app.router.lifespan_context(app):
        handle = None
        if case != "unknown-task":
            handle = services.invocations.create(
                engine="adk",
                session_id="ctx-existing",
                user_id=None,
                workflow_name="flow",
                workflow_version="1.0.0",
                workflow_fingerprint="fingerprint",
                owner_principal="someone-else" if case == "foreign-task" else None,
            )
            services.invocations.update(handle, status="waiting")

        invocations_created = []
        original_create = services.invocations.create

        def record_create(**kwargs):
            invocations_created.append(kwargs)
            return original_create(**kwargs)

        monkeypatch.setattr(services.invocations, "create", record_create)
        side_effects = []
        engine = app.state.engine

        async def unexpected_engine_action(*_args, **_kwargs):
            side_effects.append("engine")
            raise AssertionError("stream resume must not invoke or resume a workflow")

        monkeypatch.setattr(engine, "invoke", unexpected_engine_action)
        monkeypatch.setattr(engine, "resume", unexpected_engine_action)

        def unexpected_subscription(*_args, **_kwargs):
            side_effects.append("subscription")
            raise AssertionError("rejected stream resume must not subscribe to task events")

        monkeypatch.setattr(services.lifecycle_events, "subscribe", unexpected_subscription)
        count_before = services.invocations.connection.execute(
            "SELECT COUNT(*) FROM invocations"
        ).fetchone()[0]
        subscriber_ids_before = set(services.lifecycle_events._subscribers)
        message = _message(taskId=handle.invocation_id if handle else "missing-task")
        if case == "mismatched-context":
            message["contextId"] = "different-context"
        elif case == "matching-context-async-resume":
            message["contextId"] = "ctx-existing"
        params: dict[str, object] = {"message": message}
        if case == "matching-context-async-resume":
            params["configuration"] = {"returnImmediately": True}
        headers = HEADERS
        if case == "foreign-task":
            headers = {**HEADERS, "Authorization": "Bearer stream-token"}

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/a2a",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "SendStreamingMessage",
                    "params": params,
                },
            )

        assert response.status_code == 200
        assert response.json()["error"]["code"] == expected_code
        assert invocations_created == [] and side_effects == []
        assert (
            services.invocations.connection.execute("SELECT COUNT(*) FROM invocations").fetchone()[
                0
            ]
            == count_before
        )
        assert set(services.lifecycle_events._subscribers) == subscriber_ids_before


@pytest.mark.asyncio
async def test_streaming_requires_authorization(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OWA_TEST_A2A_BEARER", "stream-token")
    config = RuntimeConfig.model_validate(
        {
            "a2a": {
                "enabled": True,
                "security_profile": "partner",
                "authorization": {"rules": [{"actions": ["tasks.get"], "resources": ["tasks"]}]},
            },
            "security": {
                "profiles": {
                    "partner": {
                        "type": "bearer",
                        "token": {"from_env": "OWA_TEST_A2A_BEARER"},
                        "principal": "partner",
                        "roles": ["partners"],
                    }
                }
            },
        }
    )
    app, _services = _make_app(tmp_path, config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {**HEADERS, "Authorization": "Bearer stream-token"}
            forbidden = await client.post(
                "/a2a",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "SendStreamingMessage",
                    "params": {"message": _message()},
                },
            )
            assert forbidden.status_code == 403


@pytest.mark.asyncio
async def test_http_json_stream_and_subscribe_endpoints(tmp_path) -> None:
    config = RuntimeConfig.model_validate({"a2a": {"enabled": True, "transport": "http_json"}})
    app, _services = _make_app(tmp_path, config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.stream(
                "POST",
                "/a2a/message:stream",
                headers=HEADERS,
                json={"message": _message()},
            ) as response:
                assert response.status_code == 200
                assert response.headers["content-type"].startswith("text/event-stream")
                frames = await _drain_sse(response)
            assert frames
            task_id = str(frames[0]["task"]["id"])

            async with client.stream(
                "POST",
                f"/a2a/tasks/{task_id}:subscribe",
                headers=HEADERS,
            ) as response:
                assert response.status_code == 200
                subscribe_frames = await _drain_sse(response)
            assert subscribe_frames
            assert "task" in subscribe_frames[0]


@pytest.mark.asyncio
async def test_stream_disconnect_does_not_cancel_invocation(tmp_path) -> None:
    config = RuntimeConfig.model_validate({"a2a": {"enabled": True}})
    app, _services = _make_app(tmp_path, config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            task_id: list[str] = []

            async def open_and_abort() -> None:
                async with client.stream(
                    "POST",
                    "/a2a",
                    headers=HEADERS,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "SendStreamingMessage",
                        "params": {"message": _message()},
                    },
                ) as response:
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            payload = _stream_result(
                                json.loads(line.removeprefix("data: ")), request_id=1
                            )
                            task_id.append(str(payload["task"]["id"]))
                            break
                    # Abrupt disconnect once the task id is known.
                assert task_id

            await open_and_abort()

            # The invocation must still run to completion.
            result: dict[str, object] | None = None
            for _ in range(60):
                state = await client.post(
                    "/a2a",
                    headers=HEADERS,
                    json={
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "GetTask",
                        "params": {"id": task_id[0]},
                    },
                )
                result = state.json()["result"]
                if result["status"]["state"] == "TASK_STATE_COMPLETED":
                    break
                await asyncio.sleep(0.05)
            assert result is not None
            assert result["status"]["state"] == "TASK_STATE_COMPLETED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "event_type", "a2a_state"),
    [
        ("faulted", "WorkflowFaulted", "TASK_STATE_FAILED"),
        ("cancelled", "WorkflowCancelled", "TASK_STATE_CANCELED"),
    ],
)
async def test_subscribed_stream_emits_one_error_or_cancellation_terminal(
    tmp_path, status: str, event_type: str, a2a_state: str
) -> None:
    config = RuntimeConfig.model_validate({"a2a": {"enabled": True}})
    app, services = _make_app(tmp_path, config)
    async with app.router.lifespan_context(app):
        handle = services.invocations.create(
            engine="adk",
            session_id="ctx-terminal-stream",
            user_id=None,
            workflow_name="flow",
            workflow_version="1.0.0",
            workflow_fingerprint="fingerprint",
        )
        services.invocations.update(handle, status="running")
        request = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 9,
                "method": "SubscribeToTask",
                "params": {"id": handle.invocation_id},
            }
        ).encode()
        headers = [(b"a2a-version", V1.encode()), (b"content-type", b"application/json")]
        app_task, parse = await _stream_asgi(
            app,
            method="POST",
            path="/a2a",
            body=request,
            headers=headers,
            request_id=9,
        )

        try:
            for _ in range(200):
                frames = parse()
                if any("task" in frame for frame in frames):
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError(f"stream did not publish its task snapshot: {parse()}")

            current = services.invocations.get(handle.invocation_id)
            assert current is not None
            changes: dict[str, object] = {"status": status}
            if status == "faulted":
                changes["error"] = {
                    "code": "fixture_failure",
                    "message": "private implementation detail",
                }
            services.invocations.update(current, **changes)
            services.lifecycle_events.emit(
                WorkflowEvent(
                    event_type=event_type,
                    invocation_id=handle.invocation_id,
                    session_id=handle.session_id,
                    workflow_name=handle.workflow_name,
                    workflow_version=handle.workflow_version,
                    engine=handle.engine,
                    status=status,
                )
            )
            await asyncio.wait_for(app_task, timeout=5)
        finally:
            if not app_task.done():
                app_task.cancel()
                await asyncio.gather(app_task, return_exceptions=True)

    frames = parse()
    updates = _updates(frames)
    terminal = [
        update
        for update in updates
        if update["status"]["state"]
        in {"TASK_STATE_COMPLETED", "TASK_STATE_FAILED", "TASK_STATE_CANCELED"}
    ]
    assert [update["status"]["state"] for update in terminal] == [a2a_state]
    assert not any("artifactUpdate" in frame for frame in frames)
    assert "private implementation detail" not in repr(frames)


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["jsonrpc", "http_json"])
async def test_stream_disconnect_during_host_drain_keeps_working_projection(
    tmp_path, transport: str
) -> None:
    entered = asyncio.Event()
    cancellation_suppressed = asyncio.Event()
    release_provider = asyncio.Event()
    provider_returned = asyncio.Event()

    async def slow_model(_prompt):
        entered.set()
        try:
            await release_provider.wait()
        except asyncio.CancelledError:
            cancellation_suppressed.set()
            await asyncio.shield(release_provider.wait())
        provider_returned.set()
        return {"response": "late-result"}

    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "workflow": {
                "catalog": [
                    {
                        "document": {
                            "dsl": "1.0.3",
                            "namespace": "a2a-stream-drain",
                            "name": "slow",
                            "version": "1.0.0",
                        },
                        "do": [
                            {
                                "wait": {
                                    "call": "llm:1.0.0@default",
                                    "with": {"prompt": "wait through stream disconnect"},
                                }
                            }
                        ],
                    }
                ]
            },
            "a2a": {
                "enabled": True,
                "transport": transport,
                "skills": [{"id": "slow", "workflow": "slow"}],
            },
        }
    )
    services = RuntimeServices(config, model=FakeModel(slow_model), database_root=tmp_path)
    app = create_app(config=config, services=services)
    stream_disconnected = asyncio.Event()
    stream_task: asyncio.Task[None] | None = None
    shutdown: asyncio.Task[None] | None = None

    try:
        async with app.router.lifespan_context(app):
            client_transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=client_transport, base_url="http://test"
            ) as client:
                if transport == "jsonrpc":
                    started = await client.post(
                        "/a2a",
                        headers=HEADERS,
                        json={
                            "jsonrpc": "2.0",
                            "id": 41,
                            "method": "SendMessage",
                            "params": {
                                "message": _message(metadata={"skillId": "slow"}),
                                "configuration": {"returnImmediately": True},
                            },
                        },
                    )
                    assert started.status_code == 200, started.text
                    task_id = str(started.json()["result"]["task"]["id"])
                    body = json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 42,
                            "method": "SubscribeToTask",
                            "params": {"id": task_id},
                        }
                    ).encode()
                    stream_path = "/a2a"
                    request_id: str | int | None = 42
                else:
                    started = await client.post(
                        "/a2a/message:send",
                        headers=HEADERS,
                        json={
                            "message": _message(metadata={"skillId": "slow"}),
                            "configuration": {"returnImmediately": True},
                        },
                    )
                    assert started.status_code == 200, started.text
                    task_id = str(started.json()["task"]["id"])
                    body = b"{}"
                    stream_path = f"/a2a/tasks/{task_id}:subscribe"
                    request_id = None

                await asyncio.wait_for(entered.wait(), timeout=30)
                stream_headers = [
                    (b"a2a-version", V1.encode()),
                    (b"content-type", b"application/json"),
                ]
                stream_task, parse = await _stream_asgi(
                    app,
                    method="POST",
                    path=stream_path,
                    body=body,
                    headers=stream_headers,
                    request_id=request_id,
                    disconnect=stream_disconnected,
                )

                for _ in range(200):
                    frames = parse()
                    if any("task" in frame for frame in frames):
                        break
                    await asyncio.sleep(0.01)
                else:
                    raise AssertionError(f"stream did not publish its task snapshot: {parse()}")

                snapshot = next(frame["task"] for frame in parse() if "task" in frame)
                assert snapshot["id"] == task_id
                assert snapshot["status"]["state"] == "TASK_STATE_WORKING"

                shutdown = asyncio.create_task(app.state.engine.shutdown())
                await asyncio.wait_for(cancellation_suppressed.wait(), timeout=5)
                assert not shutdown.done()
                assert not any(
                    update["status"]["state"]
                    in {"TASK_STATE_COMPLETED", "TASK_STATE_FAILED", "TASK_STATE_CANCELED"}
                    for update in _updates(parse())
                )

                # The observer disconnects while the provider still owns the
                # business work; release it only after the stream has closed.
                stream_disconnected.set()
                await asyncio.wait_for(stream_task, timeout=5)
                frames_after_disconnect = parse()
                assert not any(
                    update["status"]["state"]
                    in {"TASK_STATE_COMPLETED", "TASK_STATE_FAILED", "TASK_STATE_CANCELED"}
                    for update in _updates(frames_after_disconnect)
                )

                release_provider.set()
                await asyncio.wait_for(shutdown, timeout=5)
                await asyncio.wait_for(provider_returned.wait(), timeout=1)
                current = services.invocations.get(task_id)
                assert current is not None and current.status == "running"

                if transport == "jsonrpc":
                    projected = await client.post(
                        "/a2a",
                        headers=HEADERS,
                        json={
                            "jsonrpc": "2.0",
                            "id": 43,
                            "method": "GetTask",
                            "params": {"id": task_id},
                        },
                    )
                    task_projection = projected.json()["result"]
                else:
                    projected = await client.get(f"/a2a/tasks/{task_id}", headers=HEADERS)
                    task_projection = projected.json()
                assert projected.status_code == 200
                assert task_projection["status"]["state"] == "TASK_STATE_WORKING"
    finally:
        stream_disconnected.set()
        release_provider.set()
        if stream_task is not None and not stream_task.done():
            stream_task.cancel()
            await asyncio.gather(stream_task, return_exceptions=True)
        if shutdown is not None and not shutdown.done():
            await asyncio.gather(shutdown, return_exceptions=True)
        services.close()


class _BacklogSink:
    def __init__(self, backlog: list[object]) -> None:
        self.backlog = backlog
        self.subscription = _ClosableSubscription()

    def subscribe(self, *, last_event_id: str | None, queue_size: int):
        assert last_event_id is None
        assert queue_size > 0
        return self.backlog, self.subscription


class _ClosableSubscription:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True

    async def receive(self, *, timeout: float):
        raise TimeoutError


def _stream_handle(*, status: str = "running", session_id: str = "ctx-stream"):
    return ExecutionHandle(
        invocation_id="stream-task",
        engine="langgraph",
        engine_execution_reference="native-ref",
        user_id=None,
        session_id=session_id,
        workflow_name="stream-flow",
        workflow_version="1.0.0",
        workflow_fingerprint="fixture-fingerprint",
        status=status,
        output={"answer": "done"} if status == "completed" else None,
    )


@pytest.mark.asyncio
async def test_stream_handles_missing_task_and_disappearing_resubscription() -> None:
    missing_sink = _BacklogSink([])
    no_invocation = a2a_sse_stream(missing_sink, SimpleNamespace(get=lambda _id: None), "missing")
    assert [frame async for frame in no_invocation] == []
    assert missing_sink.subscription.closed

    backlog = [
        SimpleNamespace(id="other-event", data={"invocation_id": "other-task"}),
        SimpleNamespace(id="task-event", data={"invocation_id": "stream-task"}),
    ]
    sink = _BacklogSink(backlog)
    lookups = iter((_stream_handle(), None))
    invocations = SimpleNamespace(get=lambda _id: next(lookups))
    frames = [frame async for frame in a2a_sse_stream(sink, invocations, "stream-task")]

    # Keep the snapshot while ignoring unrelated events and vanished updates.
    assert len(frames) == 1
    assert b"stream-task:snapshot" in frames[0]
    assert sink.subscription.closed


@pytest.mark.asyncio
async def test_stream_skips_malformed_event_and_closes_after_authoritative_terminal() -> None:
    backlog = [
        SimpleNamespace(id="malformed-event", data=["not", "a", "CloudEvent", "object"]),
        SimpleNamespace(id="terminal-event", data={"invocation_id": "stream-task"}),
    ]
    sink = _BacklogSink(backlog)
    completed = _stream_handle(status="completed")
    lookup_count = 0

    def lookup(_id: str):
        nonlocal lookup_count
        lookup_count += 1
        return _stream_handle() if lookup_count == 1 else completed

    invocations = SimpleNamespace(get=lookup)

    frames = [frame async for frame in a2a_sse_stream(sink, invocations, "stream-task")]
    payloads = [json.loads(frame.split(b"data: ", 1)[1]) for frame in frames]

    # Event contents are hints only: the common invocation record supplies the
    # terminal state, and malformed unrelated data cannot leak a subscription.
    assert payloads[0]["task"]["status"]["state"] == "TASK_STATE_WORKING"
    assert all("task" not in payload for payload in payloads[1:])
    terminal = [payload["statusUpdate"] for payload in payloads if "statusUpdate" in payload]
    assert [update["status"]["state"] for update in terminal] == ["TASK_STATE_COMPLETED"]
    assert any("artifactUpdate" in payload for payload in payloads)
    assert sink.subscription.closed


@pytest.mark.asyncio
async def test_completed_stream_uses_task_id_when_session_context_is_unavailable() -> None:
    event = SimpleNamespace(id="completed-event", data={"invocation_id": "stream-task"})
    sink = _BacklogSink([event])
    completed = _stream_handle(status="completed", session_id="")
    lookups = iter((_stream_handle(), completed, completed, completed))
    invocations = SimpleNamespace(get=lambda _id: next(lookups))

    frames = [frame async for frame in a2a_sse_stream(sink, invocations, "stream-task")]
    payloads = [json.loads(frame.split(b"data: ", 1)[1]) for frame in frames]

    artifact = next(
        payload["artifactUpdate"] for payload in payloads if "artifactUpdate" in payload
    )
    status = next(payload["statusUpdate"] for payload in payloads if "statusUpdate" in payload)
    assert artifact["contextId"] == "stream-task"
    assert status["contextId"] == "stream-task"
    assert sink.subscription.closed
