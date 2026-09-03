"""A2A v1 streaming/resubscription over the common lifecycle infrastructure.

Covers the bounded A2A-7 profile: ``SendStreamingMessage`` /
``SubscribeToTask`` (JSON-RPC) and ``message:stream`` / ``tasks/{id}:subscribe``
(HTTP+JSON), protocol-native frame translation, and disconnect safety.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import httpx
import pytest
from open_workflow_agent.a2a import A2A_PROTOCOL_VERSION as V1
from open_workflow_agent.api import create_app
from open_workflow_agent.approvals import APPROVAL_DECISION_EVENT, APPROVAL_REQUEST_EVENT
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.services import RuntimeServices

HEADERS = {"a2a-version": V1}


def _message(text: str = "hello", **extra: object) -> dict[str, object]:
    message: dict[str, object] = {"role": "ROLE_USER", "parts": [{"text": text}]}
    message.update(extra)
    return message


def _make_app(tmp_path, config: RuntimeConfig):
    services = RuntimeServices(
        config, model=FakeModel({"response": "stream-reply"}), database_root=tmp_path
    )
    app = create_app(config=config, services=services)
    return app, services


async def _drain_sse(response: httpx.Response, timeout: float = 15.0) -> list[dict[str, object]]:
    """Collect SSE data frames until the server closes the stream."""

    frames: list[dict[str, object]] = []

    async def collect() -> None:
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                frames.append(json.loads(line.removeprefix("data: ")))

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
            await done.wait()
            return {"type": "http.disconnect"}
        body_sent.set()
        return {"type": "http.request", "body": body, "more_body": False}

    def parse() -> list[dict[str, object]]:
        raw = b"".join(received)
        for line in raw.split(b"\n"):
            if line.startswith(b"data: "):
                frame = json.loads(line.removeprefix(b"data: ").decode("utf-8"))
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
                frames = await _drain_sse(response)

    assert any("task" in frame for frame in frames), frames
    updates = _updates(frames)
    assert updates, frames
    assert [str(update["status"]["state"]) for update in updates][-1] == "TASK_STATE_COMPLETED"
    terminal = [update for update in updates if update["final"] is True]
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
                frames = await _drain_sse(response)

    artifact_positions = [i for i, frame in enumerate(frames) if "artifactUpdate" in frame]
    terminal_positions = [
        i
        for i, frame in enumerate(frames)
        if "statusUpdate" in frame and frame["statusUpdate"]["final"]
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
            app, method="POST", path="/a2a", body=request, headers=headers
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
    terminal = [update for update in _updates(frames) if update["final"] is True]
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
                frames = await _drain_sse(response)
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
                            payload = json.loads(line.removeprefix("data: "))
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
