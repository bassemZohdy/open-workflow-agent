"""Executable audit findings; strict xfails retain their staged repair owners.

These are desired-behavior assertions, not passing implementation certificates.
Remove the corresponding xfail when its milestone implements the behavior.
"""

from __future__ import annotations

import base64
from typing import Any

import httpx
import pytest
from engine_cases import engine_cases
from open_workflow_agent.errors import ExpressionError, UnsupportedWorkflowFeature
from open_workflow_agent.events import InMemoryEventBus
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.workflow import compile_workflow


def _workflow(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "document": {
            "dsl": "1.0.3",
            "namespace": "audit",
            "name": "known-gaps",
            "version": "1.0.0",
        },
        "do": [{"operation": task}],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
async def test_noncompetitive_fork_preserves_ordered_object_results(
    services, engine_name, engine_type
):
    plan = compile_workflow(
        _workflow(
            {
                "fork": {
                    "branches": [
                        {"first": {"set": {"shared": "first"}}},
                        {"second": {"set": {"shared": "second"}}},
                    ]
                }
            }
        )
    )
    engine = engine_type()
    await engine.initialize(services)
    handle = services.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    try:
        result = await engine.invoke(plan, handle, {"original": True})
        assert result.status == "completed", result.error
        assert result.output == [{"shared": "first"}, {"shared": "second"}]
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="S3-03: OpenAPI must resolve operation from its description",
)
async def test_openapi_resolves_operation_server_path_and_parameters():
    requests: list[tuple[str, str]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, str(request.url)))
        if request.method == "GET" and request.url.path == "/openapi.json":
            return httpx.Response(
                200,
                json={
                    "openapi": "3.0.3",
                    "info": {"title": "Audit", "version": "1.0.0"},
                    "servers": [{"url": "https://api.test"}],
                    "paths": {
                        "/items/{itemId}": {
                            "get": {
                                "operationId": "getItem",
                                "parameters": [
                                    {
                                        "name": "itemId",
                                        "in": "path",
                                        "required": True,
                                        "schema": {"type": "string"},
                                    }
                                ],
                                "responses": {"200": {"description": "Item"}},
                            }
                        }
                    },
                },
            )
        return httpx.Response(200, json={"id": "42"})

    client = HttpClient(transport=httpx.MockTransport(respond))
    output = await ProtocolServices(client).call(
        "openapi",
        {
            "document": {"endpoint": "https://description.test/openapi.json"},
            "operationId": "getItem",
            "parameters": {"itemId": "42"},
        },
    )
    assert output == {"id": "42"}
    assert requests == [
        ("GET", "https://description.test/openapi.json"),
        ("GET", "https://api.test/items/42"),
    ]


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="S3-03: OWS HTTP raw output must preserve binary as base64",
)
async def test_http_raw_output_preserves_binary_as_base64():
    content = b"\x00\xffbinary"
    client = HttpClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=content))
    )
    result = await ProtocolServices(client).call(
        "http", {"endpoint": "https://api.test/binary", "output": "raw"}
    )
    assert result == base64.b64encode(content).decode("ascii")


@pytest.mark.asyncio
async def test_event_envelope_preserves_explicit_null_data():
    bus = InMemoryEventBus()
    event = await bus.publish({"type": "audit.null", "data": None}, default_source="urn:audit")
    assert "data" in event.as_dict()
    assert event.as_dict()["data"] is None


@pytest.mark.parametrize("variable", ["dateTime", "authorization", "secrets"])
def test_unavailable_global_descriptors_fail_admission(variable):
    with pytest.raises(ExpressionError) as error:
        compile_workflow(_workflow({"set": {"value": "${ $" + variable + " }"}}))
    assert error.value.details["reference"] == "/do/0/operation/set"


@pytest.mark.parametrize(
    "protocol,payload",
    [
        (
            "grpc",
            {
                "proto": {"endpoint": "https://description.test/service.proto"},
                "service": {"name": "Audit", "host": "api.test", "port": 443},
                "method": "GetItem",
                "arguments": {"id": "42"},
            },
        ),
        (
            "asyncapi",
            {
                "document": {"endpoint": "https://description.test/asyncapi.json"},
                "operation": "publishItem",
                "message": {"payload": {"id": "42"}},
            },
        ),
        (
            "asyncapi",
            {
                "document": {"endpoint": "https://description.test/asyncapi.json"},
                "operation": "receiveItem",
                "subscription": {"consume": {"amount": 1}},
            },
        ),
    ],
)
def test_unimplemented_protocol_parents_fail_admission(protocol, payload):
    with pytest.raises(UnsupportedWorkflowFeature, match="not enabled") as error:
        compile_workflow(_workflow({"call": protocol, "with": payload}))
    assert error.value.details["reference"] == "/do/0/operation"
