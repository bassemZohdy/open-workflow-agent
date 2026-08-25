from __future__ import annotations

import json

import httpx
import pytest
from open_workflow_agent.errors import ToolError, UnsupportedWorkflowFeature
from open_workflow_agent.protocols import EnvironmentAuthentication, HttpClient, ProtocolServices
from open_workflow_agent.workflow import compile_workflow


@pytest.mark.asyncio
async def test_protocol_services_build_mcp_json_rpc_request():
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"result": {"ok": True}})

    services = ProtocolServices(HttpClient(transport=httpx.MockTransport(handler)))
    result = await services.call(
        "mcp", {"endpoint": "https://mcp.test", "name": "lookup", "arguments": {"q": "x"}}
    )
    assert result["result"]["ok"] is True
    assert seen["method"] == "tools/call"


@pytest.mark.asyncio
async def test_official_protocol_shapes_and_operation_headers(monkeypatch):
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setenv("OWA_TOKEN", "secret")
    services = ProtocolServices(
        HttpClient(
            authentication=EnvironmentAuthentication(bearer_env="OWA_TOKEN"),
            transport=httpx.MockTransport(handler),
        )
    )
    await services.call(
        "mcp",
        {
            "method": "tools/call",
            "transport": {"http": {"endpoint": "https://mcp.test"}},
            "parameters": {"name": "lookup", "arguments": {"q": "x"}},
            "operationId": "mcp-1",
        },
    )
    request = seen[0]
    assert request.headers["Authorization"] == "Bearer secret"
    assert request.headers["Idempotency-Key"] == "mcp-1"
    assert json.loads(request.content)["params"]["name"] == "lookup"


@pytest.mark.asyncio
async def test_protocol_host_allowlist_and_response_limit():
    services = ProtocolServices(
        HttpClient(
            allowed_hosts={"allowed.test"},
            max_response_bytes=4,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=b"too large")
            ),
        )
    )
    with pytest.raises(ToolError, match="not allowed"):
        await services.call("http", {"endpoint": "https://blocked.test"})
    with pytest.raises(ToolError, match="maximum"):
        await services.call("http", {"endpoint": "https://allowed.test"})


@pytest.mark.asyncio
async def test_protocol_timeout_and_redirects_are_translated():
    async def timeout(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    timeout_services = ProtocolServices(HttpClient(transport=httpx.MockTransport(timeout)))
    with pytest.raises(ToolError, match="HTTP request failed"):
        await timeout_services.call("http", {"endpoint": "https://service.test"})

    redirect_services = ProtocolServices(
        HttpClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(302, headers={"location": "/next"})
            )
        )
    )
    with pytest.raises(ToolError, match="HTTP request failed"):
        await redirect_services.call("http", {"endpoint": "https://service.test"})


@pytest.mark.asyncio
async def test_http_status_errors_expose_filterable_workflow_details():
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"missing": True})

    services = ProtocolServices(HttpClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(ToolError) as raised:
        await services.call("http", {"endpoint": "https://service.test"})
    assert raised.value.details["status"] == 404
    assert raised.value.details["type"].endswith("communication")


@pytest.mark.asyncio
async def test_shared_protocol_contracts_cover_http_mcp_a2a_and_openapi():
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"path": request.url.path})

    services = ProtocolServices(HttpClient(transport=httpx.MockTransport(handler)))
    assert await services.call(
        "http", {"method": "POST", "endpoint": "https://service.test/http", "body": {"ok": 1}}
    ) == {"path": "/http"}
    assert await services.call(
        "mcp",
        {
            "method": "tools/list",
            "transport": {"http": {"endpoint": "https://service.test/mcp"}},
            "parameters": {},
        },
    ) == {"path": "/mcp"}
    assert await services.call(
        "a2a",
        {
            "server": "https://service.test/a2a",
            "method": "message/send",
            "parameters": {"message": "hello"},
        },
    ) == {"path": "/a2a"}
    assert await services.call(
        "openapi",
        {
            "document": {"endpoint": "https://service.test/openapi"},
            "operationId": "lookup",
            "parameters": {"query": "hello"},
        },
    ) == {"path": "/openapi"}
    assert [request.url.path for request in seen] == ["/http", "/mcp", "/a2a", "/openapi"]


@pytest.mark.asyncio
async def test_http_contract_exposes_bounded_output_modes():
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"created": True}, headers={"x-test": "yes"})

    services = ProtocolServices(HttpClient(transport=httpx.MockTransport(handler)))
    assert await services.call("http", {"endpoint": "https://service.test", "output": "raw"}) == (
        '{"created":true}'
    )
    response = await services.call(
        "http", {"endpoint": "https://service.test", "output": "response"}
    )
    assert response["status"] == 201
    assert response["body"] == {"created": True}
    assert response["headers"]["x-test"] == "yes"


def test_unsupported_protocol_transport_fails_during_workflow_validation():
    workflow = {
        "document": {"dsl": "1.0.3", "namespace": "test", "name": "stdio", "version": "1.0.0"},
        "do": [
            {
                "invoke": {
                    "call": "mcp",
                    "with": {
                        "method": "tools/list",
                        "transport": {"stdio": {"command": "mcp-server"}},
                    },
                }
            }
        ],
    }
    with pytest.raises(UnsupportedWorkflowFeature, match="stdio"):
        compile_workflow(workflow)
