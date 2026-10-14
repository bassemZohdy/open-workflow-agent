"""Independent official A2A1.2.2 v1 server on ASGI and actual loopback sockets."""

from __future__ import annotations

import asyncio
import json
import socket
import ssl
import threading
import time
from contextlib import asynccontextmanager

import httpx
import uvicorn
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.server.routes.jsonrpc_routes import create_jsonrpc_routes
from a2a.types import a2a_pb2 as types
from a2a.utils.errors import TaskNotFoundError, UnsupportedOperationError
from google.protobuf import json_format
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route


def peer_card(endpoint):
    return types.AgentCard(
        name="official-a2a-peer",
        description="Independent typed local peer",
        version="1.0.0",
        supported_interfaces=[
            types.AgentInterface(url=endpoint, protocol_binding="JSONRPC", protocol_version="1.0")
        ],
        capabilities=types.AgentCapabilities(streaming=True, extended_agent_card=True),
        default_input_modes=["text/plain", "application/json"],
        default_output_modes=["text/plain", "application/json"],
        skills=[
            types.AgentSkill(
                id="echo", name="Echo", description="Echo bounded input", tags=["fixture"]
            )
        ],
    )


@asynccontextmanager
async def sdk_a2a_fixture(
    *,
    network=False,
    outcome="task",
    send_handler=None,
    response_mutation=None,
    tls=None,
    require_client_cert=True,
):
    calls = []
    listener = socket.socket() if network else None
    if listener is not None:
        listener.bind(("127.0.0.1", 0))
        scheme = "https" if tls is not None else "http"
        endpoint = f"{scheme}://127.0.0.1:{listener.getsockname()[1]}/a2a"
    else:
        endpoint = "https://a2a.test/a2a"
    card = peer_card(endpoint)

    class Peer(RequestHandler):
        def __init__(self):
            self.tasks = {
                "task-id": types.Task(
                    id="task-id",
                    context_id="context-id",
                    status=types.TaskStatus(state=types.TASK_STATE_WORKING),
                )
            }

        async def on_message_send(self, params, context):
            if send_handler is not None:
                return await send_handler(params, context)
            if outcome == "message":
                return types.Message(
                    message_id="peer-response",
                    role=types.ROLE_AGENT,
                    context_id=params.message.context_id or "context-id",
                    task_id=params.message.task_id,
                    parts=params.message.parts,
                )
            task = types.Task(
                id=params.message.task_id or "peer-" + params.message.message_id,
                context_id=params.message.context_id or "context-id",
                status=types.TaskStatus(state=types.TASK_STATE_COMPLETED),
                artifacts=[types.Artifact(artifact_id="result", parts=params.message.parts)],
            )
            self.tasks[task.id] = task
            return task

        async def on_get_task(self, params, context):
            if params.id not in self.tasks:
                raise TaskNotFoundError()
            return self.tasks[params.id]

        async def on_list_tasks(self, params, context):
            return types.ListTasksResponse(tasks=list(self.tasks.values()))

        async def on_cancel_task(self, params, context):
            task = await self.on_get_task(params, context)
            cancelled = types.Task()
            cancelled.CopyFrom(task)
            cancelled.status.state = types.TASK_STATE_CANCELED
            self.tasks[params.id] = cancelled
            return cancelled

        async def on_message_send_stream(self, params, context):
            result = await self.on_message_send(params, context)
            if isinstance(result, types.Message):
                yield result
                return
            initial = types.Task()
            initial.CopyFrom(result)
            initial.status.state = types.TASK_STATE_WORKING
            initial.ClearField("artifacts")
            yield initial
            for artifact in result.artifacts:
                yield types.TaskArtifactUpdateEvent(
                    task_id=result.id,
                    context_id=result.context_id,
                    artifact=artifact,
                    last_chunk=True,
                )
            yield types.TaskStatusUpdateEvent(
                task_id=result.id, context_id=result.context_id, status=result.status
            )

        async def on_subscribe_to_task(self, params, context):
            task = await self.on_get_task(params, context)
            yield task
            yield types.TaskStatusUpdateEvent(
                task_id=task.id,
                context_id=task.context_id,
                status=types.TaskStatus(state=types.TASK_STATE_COMPLETED),
            )

        async def on_get_extended_agent_card(self, params, context):
            return card

        async def on_create_task_push_notification_config(self, params, context):
            raise UnsupportedOperationError()

        async def on_get_task_push_notification_config(self, params, context):
            raise UnsupportedOperationError()

        async def on_list_task_push_notification_configs(self, params, context):
            raise UnsupportedOperationError()

        async def on_delete_task_push_notification_config(self, params, context):
            raise UnsupportedOperationError()

    peer = Peer()

    async def discovery(request):
        return JSONResponse(json_format.MessageToDict(card))

    app = Starlette(
        routes=[
            *create_jsonrpc_routes(peer, "/a2a", enable_v0_3_compat=False),
            Route("/.well-known/agent-card.json", discovery),
        ]
    )

    async def capture(scope, receive, send):
        if scope["type"] != "http":
            return await app(scope, receive, send)
        if scope["method"] != "POST":
            calls.append(
                {
                    "method": scope["method"],
                    "path": scope["path"],
                    "headers": dict(scope["headers"]),
                }
            )
            return await app(scope, receive, send)
        body = bytearray()
        original_receive = receive
        recorded = False

        async def recorded_receive():
            nonlocal recorded
            frame = await original_receive()
            if frame["type"] == "http.request":
                body.extend(frame.get("body", b""))
                if not frame.get("more_body", False) and not recorded:
                    # Record the complete request before a streaming response can
                    # finish on the client. The ASGI call may still be returning
                    # response events on another thread at that point.
                    recorded = True
                    calls.append(
                        {
                            "body": json.loads(body),
                            "headers": dict(scope["headers"]),
                            "method": "POST",
                        }
                    )
            return frame

        if response_mutation is None:
            await app(scope, recorded_receive, send)
            return
        frames = []

        async def recorded_send(frame):
            frames.append(frame)

        await app(scope, recorded_receive, recorded_send)
        content = b"".join(
            frame.get("body", b"") for frame in frames if frame["type"] == "http.response.body"
        )
        changed = json.dumps(response_mutation(json.loads(content))).encode()
        start = next(frame for frame in frames if frame["type"] == "http.response.start")
        headers = [
            (key, value) for key, value in start["headers"] if key.lower() != b"content-length"
        ]
        await send(
            {**start, "headers": [*headers, (b"content-length", str(len(changed)).encode())]}
        )
        await send({"type": "http.response.body", "body": changed, "more_body": False})

    if not network:
        yield httpx.ASGITransport(app=capture), calls, endpoint
        return
    server_config = {
        "host": "127.0.0.1",
        "log_level": "critical",
        "timeout_graceful_shutdown": 2,
    }
    if tls is not None:
        server_config.update(
            ssl_certfile=str(tls.certificates["localhost"][0]),
            ssl_keyfile=str(tls.certificates["localhost"][1]),
            ssl_ca_certs=str(tls.trusted_clients_bundle),
            ssl_cert_reqs=ssl.CERT_REQUIRED if require_client_cert else ssl.CERT_NONE,
        )
    server = uvicorn.Server(uvicorn.Config(capture, **server_config))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    try:
        while not server.started:
            assert thread.is_alive() and time.monotonic() < deadline
            await asyncio.sleep(0.02)
        yield None, calls, endpoint
    finally:
        server.should_exit = True
        await asyncio.to_thread(thread.join, 5)
        if listener is not None:
            listener.close()
        assert not thread.is_alive(), "official A2A fixture failed to stop"
