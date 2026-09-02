from __future__ import annotations

import json
import socket

import httpx
import pytest
from open_workflow_agent.errors import ToolError, UnsupportedWorkflowFeature
from open_workflow_agent.protocols import (
    A2A_PROTOCOL_VERSION,
    MCP_PROTOCOL_VERSION,
    HttpClient,
    ProtocolServices,
    _PinnedNetworkBackend,
    resolve_public_addresses_async,
)
from open_workflow_agent.security import SecurityConfig
from open_workflow_agent.workflow import compile_workflow


def _bearer_security(monkeypatch: pytest.MonkeyPatch, *, token: str = "secret") -> SecurityConfig:
    monkeypatch.setenv("OWA_PROTOCOL_TEST_TOKEN", token)
    return SecurityConfig.model_validate(
        {
            "profiles": {
                "outbound": {"type": "bearer", "token": {"from_env": "OWA_PROTOCOL_TEST_TOKEN"}}
            }
        }
    )


@pytest.mark.asyncio
async def test_protocol_services_build_mcp_json_rpc_request():
    seen: dict[str, object] = {}
    headers: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        headers.update(request.headers)
        return httpx.Response(200, json={"result": {"ok": True}})

    services = ProtocolServices(HttpClient(transport=httpx.MockTransport(handler)))
    result = await services.call(
        "mcp", {"endpoint": "https://mcp.test", "name": "lookup", "arguments": {"q": "x"}}
    )
    assert result["result"]["ok"] is True
    assert seen["method"] == "tools/call"
    assert headers["mcp-protocol-version"] == MCP_PROTOCOL_VERSION
    assert headers["mcp-method"] == "tools/call"
    assert headers["mcp-name"] == "lookup"


@pytest.mark.asyncio
async def test_official_protocol_shapes_and_operation_headers(monkeypatch):
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    security = _bearer_security(monkeypatch)
    from open_workflow_agent.security import ProfileAuthentication

    services = ProtocolServices(
        HttpClient(
            transport=httpx.MockTransport(handler),
            authentication=ProfileAuthentication(security, "outbound"),
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
    assert request.headers["Mcp-Protocol-Version"] == MCP_PROTOCOL_VERSION
    assert request.headers["Mcp-Method"] == "tools/call"
    assert request.headers["Mcp-Name"] == "lookup"
    assert json.loads(request.content)["params"]["name"] == "lookup"


def test_protocol_security_profile_fails_closed(monkeypatch):
    with pytest.raises(ToolError, match="runtime security configuration"):
        ProtocolServices(security_profile="outbound")
    security = _bearer_security(monkeypatch)
    with pytest.raises(ValueError, match="unknown security profile"):
        ProtocolServices(security=security, security_profile="missing")
    services = ProtocolServices(security=security, security_profile="outbound")
    assert services.http.authentication.headers("https://mcp.test") == {
        "Authorization": "Bearer secret"
    }
    unauthenticated = ProtocolServices()
    assert unauthenticated.http.authentication is None


@pytest.mark.asyncio
async def test_protocol_baselines_reject_legacy_versions_and_streaming_aliases():
    services = ProtocolServices(
        HttpClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True}))
        )
    )
    with pytest.raises(ToolError, match="unsupported MCP protocol version"):
        await services.call(
            "mcp",
            {
                "endpoint": "https://mcp.test",
                "protocolVersion": "2025-11-25",
                "method": "tools/list",
            },
        )
    with pytest.raises(ToolError, match="unsupported A2A protocol version"):
        await services.call(
            "a2a",
            {
                "server": "https://agent.test/a2a",
                "protocolVersion": "0.3",
                "method": "SendMessage",
                "message": {"role": "ROLE_USER", "parts": [{"text": "hello"}]},
            },
        )
    with pytest.raises(ToolError, match="unsupported A2A method"):
        await services.call(
            "a2a",
            {
                "server": "https://agent.test/a2a",
                "method": "message/send",
                "message": {"role": "ROLE_USER", "parts": [{"text": "hello"}]},
            },
        )
    with pytest.raises(ToolError, match="unsupported A2A method"):
        await services.call(
            "a2a",
            {
                "server": "https://agent.test/a2a",
                "method": "SendStreamingMessage",
                "message": {"role": "ROLE_USER", "parts": [{"text": "hello"}]},
            },
        )


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
            "method": "SendMessage",
            "message": {"role": "ROLE_USER", "parts": [{"text": "hello"}]},
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

    mcp_request = seen[1]
    assert mcp_request.headers["Mcp-Protocol-Version"] == MCP_PROTOCOL_VERSION
    assert mcp_request.headers["Mcp-Method"] == "tools/list"

    a2a_request = seen[2]
    assert a2a_request.headers["A2A-Version"] == A2A_PROTOCOL_VERSION
    a2a_body = json.loads(a2a_request.content)
    assert a2a_body["method"] == "SendMessage"
    assert a2a_body["params"]["message"]["role"] == "ROLE_USER"


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


@pytest.mark.asyncio
async def test_public_address_resolution_rejects_private_dns_results(monkeypatch):
    def fake_getaddrinfo(*_args: object, **_kwargs: object) -> list[tuple[object, ...]]:
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    assert await resolve_public_addresses_async("https://service.test") == ("93.184.216.34",)

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
    )
    with pytest.raises(ToolError, match="disallowed IP"):
        await resolve_public_addresses_async("https://service.test")


@pytest.mark.asyncio
async def test_pinned_network_backend_connects_only_to_approved_addresses():
    class RecordingDelegate:
        def __init__(self) -> None:
            self.calls: list[tuple[str, int, dict[str, object]]] = []

        async def connect_tcp(self, host: str, port: int, **kwargs: object) -> object:
            self.calls.append((host, port, kwargs))
            return "stream"

    delegate = RecordingDelegate()
    backend = _PinnedNetworkBackend(
        delegate,
        hostname="Service.TEST.",
        port=443,
        addresses=("93.184.216.34",),
    )

    assert (
        await backend.connect_tcp("service.test", 443, server_hostname="service.test") == "stream"
    )
    assert delegate.calls == [("93.184.216.34", 443, {"server_hostname": "service.test"})]
    with pytest.raises(OSError, match="unexpected network destination"):
        await backend.connect_tcp("other.test", 443)
