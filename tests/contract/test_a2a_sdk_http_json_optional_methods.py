"""Public A2A HTTP+JSON SDK operations outside the bounded profile fail closed."""

from __future__ import annotations

import asyncio
import os
import socket
from contextlib import asynccontextmanager

import httpx
import pytest
import uvicorn
from a2a.client.transports.rest import RestTransport
from a2a.types.a2a_pb2 import AgentCard, ListTasksRequest
from a2a.utils.errors import MethodNotFoundError
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
                    assert not runner.done(), "optional-operation fixture did not start"
                    await asyncio.sleep(0.01)
            yield f"http://127.0.0.1:{listener.getsockname()[1]}"
        finally:
            server.should_exit = True
            await asyncio.wait_for(runner, timeout=10)


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"], ids=["sqlite", "postgresql"])
async def test_public_http_json_list_tasks_is_rejected_before_workflow_invocation(
    tmp_path, backend
):
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "persistence": {"datasource": datasource},
            "workflow": {
                "definition": {
                    "document": {
                        "dsl": "1.0.3",
                        "namespace": "a2a",
                        "name": "optional-method",
                        "version": "1.0.0",
                    },
                    "do": [{"result": {"set": {"value": None}}}],
                }
            },
            "a2a": {"enabled": True, "transport": "http_json"},
        }
    )
    model = FakeModel()
    services = RuntimeServices(
        config,
        model=model,
        database_root=None if datasource else tmp_path,
    )
    assert services.effect_journal.connection.kind == backend
    app = create_app(config=config, services=services)
    try:
        async with listening(app) as base:
            async with httpx.AsyncClient(timeout=10, headers={"A2A-Version": "1.0"}) as http:
                card_response = await http.get(base + "/.well-known/agent-card.json")
                assert card_response.status_code == 200
                card = ParseDict(card_response.json(), AgentCard(), ignore_unknown_fields=False)
                assert card.supported_interfaces[0].protocol_binding == "HTTP+JSON"
                peer = RestTransport(http, card, base + "/a2a")
                invocation_count_before = services.invocations.connection.execute(
                    "SELECT COUNT(*) FROM invocations"
                ).fetchone()[0]

                with pytest.raises(MethodNotFoundError) as failure:
                    await peer.list_tasks(ListTasksRequest())

                assert str(failure.value) == f"Resource not found: {base}/a2a/tasks"
                assert (
                    services.invocations.connection.execute(
                        "SELECT COUNT(*) FROM invocations"
                    ).fetchone()[0]
                    == invocation_count_before
                )
                assert model.calls == []
    finally:
        services.close()
