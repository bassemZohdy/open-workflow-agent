from __future__ import annotations

import json

import httpx
import pytest
from open_workflow_agent.errors import ToolError
from open_workflow_agent.protocols import EnvironmentAuthentication, HttpClient, ProtocolServices


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
