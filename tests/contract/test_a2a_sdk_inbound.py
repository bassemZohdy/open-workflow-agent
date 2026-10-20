"""An independent official SDK client observes actual native OWA Tasks over sockets."""

from __future__ import annotations

import asyncio
import json
import os
import socket
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
import uvicorn
from a2a.client.transports.jsonrpc import JsonRpcTransport
from a2a.client.transports.rest import RestTransport
from a2a.types.a2a_pb2 import (
    AgentCard,
    CancelTaskRequest,
    GetExtendedAgentCardRequest,
    GetTaskRequest,
    ListTasksRequest,
    SendMessageRequest,
    SubscribeToTaskRequest,
)
from a2a.utils.errors import MethodNotFoundError
from engine_cases import engine_cases
from google.protobuf.json_format import MessageToDict, ParseDict
from open_workflow_agent.api import create_app
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.services import RuntimeServices


@asynccontextmanager
async def listening(app):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        server = uvicorn.Server(
            uvicorn.Config(app, log_level="critical", timeout_graceful_shutdown=2)
        )
        runner = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(10):
                while not server.started:
                    assert not runner.done(), "official peer fixture did not start"
                    await asyncio.sleep(0.01)
            yield f"http://127.0.0.1:{listener.getsockname()[1]}"
        finally:
            server.should_exit = True
            await asyncio.wait_for(runner, timeout=10)


@pytest.mark.asyncio
@pytest.mark.usefixtures("explicit_workflow_delivery")
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize(
    ("binding", "mode"),
    [
        ("jsonrpc", "complete"),
        ("http_json", "complete"),
        ("jsonrpc", "cancel_stream"),
        ("http_json", "cancel_stream"),
        ("jsonrpc", "list_tasks_rejected"),
        ("http_json", "list_tasks_rejected"),
        ("jsonrpc", "extended_card"),
    ],
)
@pytest.mark.parametrize(
    "engine_name,engine_type", [case for case in engine_cases() if case[0] in {"adk", "langgraph"}]
)
async def test_official_sdk_client_discovers_sends_and_reads_native_task(
    tmp_path, binding, backend, mode, engine_name, engine_type
):
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    engine = engine_type()
    available = (
        engine.native.available if engine_name == "adk" else engine.native.task_nodes_available
    )
    if not available:
        pytest.skip("actual independent native environment is required")
    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "persistence": {"datasource": datasource},
            "workflow": {
                "definition": {
                    "document": {
                        "dsl": "1.0.3",
                        "namespace": "a2a",
                        "name": "sdk-inbound",
                        "version": "1.0.0",
                    },
                    "do": (
                        [{"pause": {"wait": {"seconds": 600}}}] if mode == "cancel_stream" else []
                    )
                    + [{"result": {"set": {"value": None}}}],
                }
            },
            "a2a": {"enabled": True, "transport": binding},
        }
    )
    model = FakeModel()
    services = RuntimeServices(config, model=model, database_root=None if datasource else tmp_path)
    assert services.effect_journal.connection.kind == backend
    native_path = services.engine_database_path(engine_name, create_namespace=False)
    if datasource:
        assert native_path.startswith(("postgresql://", "postgresql+"))
        assert services.database_root is None
    else:
        assert Path(native_path).parent == tmp_path
    app = create_app(config=config, services=services, engine=engine)
    acknowledged_task_id = None

    def diagnostic(task_id):
        handle = services.invocations.get(task_id)
        dispatcher = engine._initial_delivery_dispatcher
        return json.dumps(
            {
                "status": handle.status if handle is not None else "absent",
                "error": handle.error if handle is not None else None,
                "dispatcher_errors": [
                    {"type": type(error).__name__, "code": getattr(error, "code", None)}
                    for error in tuple(dispatcher._errors)[-4:]
                ]
                if dispatcher is not None
                else [],
                "dispatcher_workers": len(dispatcher._workers) if dispatcher is not None else 0,
                "active": task_id in engine._active,
                "ownership_active": task_id in engine._ownership_tasks,
            },
            sort_keys=True,
        )[:2048]

    @asynccontextmanager
    async def bounded_task():
        try:
            async with asyncio.timeout(15):
                yield
        except TimeoutError as error:
            error.add_note(
                diagnostic(acknowledged_task_id) if acknowledged_task_id else "no Task ACK"
            )
            raise

    try:
        async with listening(app) as base:
            async with httpx.AsyncClient(timeout=10, headers={"A2A-Version": "1.0"}) as http:
                card_response = await http.get(base + "/.well-known/agent-card.json")
                assert card_response.status_code == 200
                card = ParseDict(card_response.json(), AgentCard(), ignore_unknown_fields=False)
                assert card.supported_interfaces[0].protocol_version == "1.0"
                peer = (JsonRpcTransport if binding == "jsonrpc" else RestTransport)(
                    http, card, base + "/a2a"
                )
                request = ParseDict(
                    {
                        "message": {
                            "messageId": "sdk-original",
                            "role": "ROLE_USER",
                            "contextId": "sdk-context",
                            "parts": [{"text": "original"}],
                        },
                        "configuration": {"returnImmediately": True},
                    },
                    SendMessageRequest(),
                    ignore_unknown_fields=False,
                )
                async with bounded_task():
                    if mode == "extended_card":
                        extended = await peer.get_extended_agent_card(GetExtendedAgentCardRequest())
                        assert isinstance(extended, AgentCard)
                        assert extended.name == card.name
                        assert extended.version == card.version
                        assert len(extended.supported_interfaces) == 1
                        interface = extended.supported_interfaces[0]
                        assert interface.protocol_binding == "JSONRPC"
                        assert interface.protocol_version == "1.0"
                        return
                    if mode == "list_tasks_rejected":
                        invocation_count_before = services.invocations.connection.execute(
                            "SELECT COUNT(*) FROM invocations"
                        ).fetchone()[0]
                        with pytest.raises(MethodNotFoundError):
                            await peer.list_tasks(ListTasksRequest())
                        assert (
                            services.invocations.connection.execute(
                                "SELECT COUNT(*) FROM invocations"
                            ).fetchone()[0]
                            == invocation_count_before
                        )
                        assert model.calls == []
                        return
                    if mode == "cancel_stream":
                        updates = peer.send_message_streaming(request)
                        first = await anext(updates)
                        assert first.HasField("task")
                        task_id = first.task.id
                        acknowledged_task_id = task_id
                        assert first.task.context_id == "sdk-context"
                        subscription = peer.subscribe(SubscribeToTaskRequest(id=task_id))
                        initial = await anext(subscription)
                        assert initial.HasField("task") and initial.task.id == task_id

                        async def drain(stream):
                            return [MessageToDict(frame) async for frame in stream]

                        rest = asyncio.create_task(drain(updates))
                        subscribed = asyncio.create_task(drain(subscription))
                        try:
                            while services.invocations.get(task_id).status != "waiting":
                                assert services.invocations.get(task_id).status not in {
                                    "completed",
                                    "faulted",
                                    "cancelled",
                                }, diagnostic(task_id)
                                await asyncio.sleep(0.025)
                            before = services.invocations.get(task_id)
                            assert (
                                MessageToDict(await peer.get_task(GetTaskRequest(id=task_id)))[
                                    "status"
                                ]["state"]
                                == "TASK_STATE_WORKING"
                            )
                            cancelled = await peer.cancel_task(CancelTaskRequest(id=task_id))
                            assert (
                                MessageToDict(cancelled)["status"]["state"] == "TASK_STATE_CANCELED"
                            )
                            assert MessageToDict(
                                await peer.cancel_task(CancelTaskRequest(id=task_id))
                            ) == MessageToDict(cancelled)
                            for frames in await asyncio.gather(rest, subscribed):
                                assert (
                                    frames[-1]["statusUpdate"]["status"]["state"]
                                    == "TASK_STATE_CANCELED"
                                )
                                assert frames[-1]["statusUpdate"]["taskId"] == task_id
                            # An explicit resubscription after terminal completion is
                            # a read of the common Task snapshot, not a replay of the
                            # native workflow or of the earlier live event sequence.
                            terminal_resubscription = peer.subscribe(
                                SubscribeToTaskRequest(id=task_id)
                            )
                            terminal_frames = [
                                MessageToDict(frame) async for frame in terminal_resubscription
                            ]
                            assert len(terminal_frames) == 1
                            assert terminal_frames[0]["task"]["id"] == task_id
                            assert terminal_frames[0]["task"]["contextId"] == "sdk-context"
                            assert (
                                terminal_frames[0]["task"]["status"]["state"]
                                == "TASK_STATE_CANCELED"
                            )
                            assert "statusUpdate" not in terminal_frames[0]
                            assert "artifactUpdate" not in terminal_frames[0]
                            assert (
                                before.workflow_fingerprint
                                == services.invocations.get(task_id).workflow_fingerprint
                            )
                            assert services.invocations.read_input(before) == {
                                "question": "original"
                            }
                            assert model.calls == []
                        finally:
                            rest.cancel()
                            subscribed.cancel()
                            await asyncio.gather(rest, subscribed, return_exceptions=True)
                        return
                    response = await peer.send_message(request)
                    assert response.HasField("task") and not response.HasField("message")
                    task_id = response.task.id
                    acknowledged_task_id = task_id
                    assert response.task.context_id == "sdk-context"
                    while True:
                        task = await peer.get_task(GetTaskRequest(id=task_id))
                        value = MessageToDict(task)
                        if value["status"]["state"] == "TASK_STATE_COMPLETED":
                            break
                        assert services.invocations.get(task_id).status not in {
                            "faulted",
                            "cancelled",
                        }, diagnostic(task_id)
                        await asyncio.sleep(0.025)
                assert value["artifacts"][0]["parts"][0]["data"] == {"value": None}
                handle = services.invocations.get(task_id)
                assert handle.invocation_id == task_id and handle.session_id == "sdk-context"
                assert services.invocations.read_input(handle) == {"question": "original"}
                assert handle.status == "completed" and model.calls == []
                # SDK get remains the immutable common projection after a terminal cancel attempt.
                with pytest.raises(Exception) as failure:
                    await peer.cancel_task(CancelTaskRequest(id=task_id))
                assert type(failure.value).__name__ == "TaskNotCancelableError"
                again = await peer.get_task(GetTaskRequest(id=task_id))
                assert MessageToDict(again) == value
    finally:
        services.close()
