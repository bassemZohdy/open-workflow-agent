"""Official A2A SDK malformed streaming requests fail before native admission."""

from __future__ import annotations

import asyncio
import os
import socket
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from a2a.client.transports.jsonrpc import JsonRpcTransport
from a2a.client.transports.rest import RestTransport
from a2a.types.a2a_pb2 import AgentCard, SendMessageRequest
from a2a.utils.errors import ContentTypeNotSupportedError, InvalidParamsError
from engine_cases import engine_cases
from google.protobuf.json_format import ParseDict
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
                    assert not runner.done(), "A2A fixture server did not start"
                    await asyncio.sleep(0.01)
            yield f"http://127.0.0.1:{listener.getsockname()[1]}"
        finally:
            server.should_exit = True
            await asyncio.wait_for(runner, timeout=10)


@pytest.mark.asyncio
@pytest.mark.usefixtures("explicit_workflow_delivery")
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("binding", ["jsonrpc", "http_json"])
@pytest.mark.parametrize(
    "engine_name,engine_type", [case for case in engine_cases() if case[0] in {"adk", "langgraph"}]
)
async def test_sdk_malformed_streaming_message_rejects_before_admission(
    tmp_path, backend, binding, engine_name, engine_type
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
                        "name": "malformed-stream-admission",
                        "version": "1.0.0",
                    },
                    "do": [],
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
    try:
        async with listening(app) as base:
            async with httpx.AsyncClient(timeout=10, headers={"A2A-Version": "1.0"}) as http:
                card_response = await http.get(base + "/.well-known/agent-card.json")
                assert card_response.status_code == 200
                card = ParseDict(card_response.json(), AgentCard(), ignore_unknown_fields=False)
                peer = (JsonRpcTransport if binding == "jsonrpc" else RestTransport)(
                    http, card, base + "/a2a"
                )

                async def reject_before_admission(
                    parts: list[dict[str, Any]], expected_error: type[Exception]
                ) -> None:
                    request = ParseDict(
                        {
                            "message": {
                                "messageId": "malformed-stream-request",
                                "role": "ROLE_USER",
                                "contextId": "malformed-stream-context",
                                "parts": parts,
                            }
                        },
                        SendMessageRequest(),
                        ignore_unknown_fields=False,
                    )
                    invocation_count = services.invocations.connection.execute(
                        "SELECT COUNT(*) FROM invocations"
                    ).fetchone()[0]
                    effect_count = services.effect_journal.connection.execute(
                        "SELECT COUNT(*) FROM workflow_effects"
                    ).fetchone()[0]
                    effect_invocation_count = services.effect_journal.connection.execute(
                        "SELECT COUNT(*) FROM workflow_effect_invocations"
                    ).fetchone()[0]
                    delivery_count = services.initial_deliveries.connection.execute(
                        "SELECT COUNT(*) FROM initial_invocation_deliveries"
                    ).fetchone()[0]
                    subscribers = set(services.lifecycle_events._subscribers)
                    stream = peer.send_message_streaming(request)
                    with pytest.raises(expected_error) as rejected:
                        await anext(stream)
                    assert "malformed-stream-request" not in str(rejected.value)
                    assert "must-not-be-admitted" not in str(rejected.value)
                    assert (
                        services.invocations.connection.execute(
                            "SELECT COUNT(*) FROM invocations"
                        ).fetchone()[0]
                        == invocation_count
                    )
                    assert (
                        services.effect_journal.connection.execute(
                            "SELECT COUNT(*) FROM workflow_effects"
                        ).fetchone()[0]
                        == effect_count
                    )
                    assert (
                        services.effect_journal.connection.execute(
                            "SELECT COUNT(*) FROM workflow_effect_invocations"
                        ).fetchone()[0]
                        == effect_invocation_count
                    )
                    assert (
                        services.initial_deliveries.connection.execute(
                            "SELECT COUNT(*) FROM initial_invocation_deliveries"
                        ).fetchone()[0]
                        == delivery_count
                    )
                    assert set(services.lifecycle_events._subscribers) == subscribers
                    assert model.calls == []

                # Keep the hosted selection matrix compact while exercising
                # both a malformed DTO and a valid but unsupported Part shape.
                await reject_before_admission([{}], InvalidParamsError)
                await reject_before_admission(
                    [{"data": {"secret": "must-not-be-admitted"}}],
                    ContentTypeNotSupportedError,
                )
    finally:
        services.close()
