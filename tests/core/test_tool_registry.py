from __future__ import annotations

import os

import httpx
import pytest
from _a2a_sdk_fixture import peer_card
from google.protobuf import json_format
from open_workflow_agent.config import RuntimeConfig, SandboxConfig, ToolConfig
from open_workflow_agent.errors import ToolError
from open_workflow_agent.openapi_client import OpenApiDescription
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.sandbox import InternalSandboxBackend, SandboxManager
from open_workflow_agent.security import SecurityConfig
from open_workflow_agent.tools import ToolRegistry
from openapi_fixtures import operation_document


class ProtocolStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.authentication = []
        self.selection = []
        self.bound_endpoints = []
        self.http = HttpClient()

    async def call(
        self,
        protocol: str,
        payload: object,
        *,
        _authentication=None,
        _openapi_selection=None,
        _openapi_bound_endpoint=None,
    ) -> object:
        self.calls.append((protocol, payload))
        self.authentication.append(_authentication)
        self.selection.append(_openapi_selection)
        self.bound_endpoints.append(_openapi_bound_endpoint)
        return {"protocol": protocol, "payload": payload}


def test_tool_registry_requires_referenced_security_configuration() -> None:
    with pytest.raises(ToolError, match="runtime security configuration"):
        ToolRegistry.from_config(
            [ToolConfig(type="openapi", name="weather", security_profile="outbound")],
            ProtocolStub(),  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_tool_registry_invokes_configured_mcp_and_openapi_tools(monkeypatch):
    monkeypatch.setenv("OWA_TOOL_TOKEN", "tool-secret")
    security = RuntimeConfig.model_validate(
        {
            "security": {
                "profiles": {
                    "outbound": {
                        "type": "bearer",
                        "token": {"from_env": "OWA_TOOL_TOKEN"},
                    }
                }
            }
        }
    ).security
    protocols = ProtocolStub()
    document = operation_document(
        "https://api.test",
        operation_id="weather",
        path="/weather",
        parameters=(("X-Caller", "header", "string"),),
    )
    description = OpenApiDescription.capture("https://description.test/weather.json", document)
    registry = ToolRegistry.from_config(
        [
            ToolConfig(
                type="mcp",
                name="lookup",
                endpoint="https://mcp.test",
                security_profile="outbound",
                options={"timeout": 3},
            ),
            ToolConfig(
                type="openapi",
                name="weather",
                endpoint="https://api.test/weather",
                security_profile="outbound",
                options={"operationId": "weather"},
                openapi_binding={
                    "id": "weather",
                    "version": "1",
                    "document_uri": description.uri,
                    "document": document,
                    "digest": description.digest,
                },
            ),
        ],
        protocols,  # type: ignore[arg-type]
        security=security,
    )

    assert registry.names() == ("lookup", "weather")
    assert [binding.name for binding in registry.bindings()] == ["lookup", "weather"]
    result = await registry.bindings()[0].invoke(
        {
            "transport": {
                "http": {
                    "headers": {
                        "authorization": "Bearer caller-controlled",
                        "X-Caller": "yes",
                    }
                }
            },
            "query": "q",
        }
    )
    assert result["protocol"] == "mcp"
    mcp_payload = protocols.calls[0][1]
    assert mcp_payload["timeout"] == 3
    assert mcp_payload["endpoint"] == "https://mcp.test"
    assert mcp_payload["transport"]["http"]["headers"] == {
        "authorization": "Bearer caller-controlled",
        "X-Caller": "yes",
    }
    assert protocols.authentication[0].headers("https://mcp.test") == {
        "Authorization": "Bearer tool-secret"
    }

    await registry.invoke("weather", {"parameters": {"X-Caller": "yes"}})
    openapi_payload = protocols.calls[1][1]
    assert openapi_payload["endpoint"] == "https://api.test/weather"
    assert protocols.bound_endpoints[1] == "https://api.test/weather"
    assert openapi_payload["parameters"] == {
        "X-Caller": "yes",
    }
    assert protocols.authentication[1].headers("https://api.test") == {
        "Authorization": "Bearer tool-secret"
    }
    assert protocols.selection[1].operation_id == "weather"
    assert protocols.selection[1].endpoint == "https://api.test/weather"
    assert "tool-secret" not in protocols.selection[1].description.document_json

    with pytest.raises(ToolError, match="configured tool not found"):
        await registry.invoke("missing", {})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field", ["document", "operationId", "endpoint", "security_profile", "_openapi_selection"]
)
async def test_openapi_tool_rejects_model_binding_override_before_transport(field):
    document = operation_document("https://api.test")
    description = OpenApiDescription.capture("https://description.test/spec.json", document)
    protocols = ProtocolStub()
    registry = ToolRegistry.from_config(
        [
            ToolConfig(
                type="openapi",
                name="peer",
                options={"operationId": "lookup"},
                openapi_binding={
                    "id": "peer",
                    "version": "1",
                    "document_uri": description.uri,
                    "document": document,
                    "digest": description.digest,
                },
            )
        ],
        protocols,
    )
    with pytest.raises(ToolError, match="cannot replace"):
        await registry.invoke("peer", {field: "forged"})
    assert protocols.calls == [] and protocols.authentication == []


def _mcp_stdio_deployment(tmp_path):
    command = os.path.abspath(os.sys.executable)
    arguments = ["-u", "-c", "pass"]
    config = SandboxConfig.model_validate(
        {
            "enabled": True,
            "workspace_root": str(tmp_path / "workspace"),
            "file_size_bytes": None if os.name == "nt" else 33_554_432,
            "mcp_stdio": {
                "enabled": True,
                "servers": [{"id": "fixture", "command": command, "arguments": arguments}],
            },
        }
    )
    return config, command, arguments


def _mcp_tool_payload(transport):
    return {
        "protocolVersion": "2026-07-28",
        "method": "tools/call",
        "parameters": {"name": "lookup", "arguments": {"query": "sample"}},
        "client": {"name": "test-client", "version": "1.0"},
        "transport": transport,
    }


@pytest.mark.asyncio
async def test_role_mcp_preflight_routes_http_and_deployment_bound_stdio_without_io(tmp_path):
    requests = []
    authorized = []

    async def authorize_destination(protocol, endpoint, payload):
        authorized.append((protocol, endpoint, payload))

    http = HttpClient(
        allowed_hosts={"mcp.test"},
        transport=httpx.MockTransport(lambda request: requests.append(request)),
    )
    protocols = ProtocolServices(http, authorize_destination=authorize_destination)
    config, command, arguments = _mcp_stdio_deployment(tmp_path)
    protocols.sandbox = SandboxManager(InternalSandboxBackend(config))
    registry = ToolRegistry.from_config([ToolConfig(type="mcp", name="lookup")], protocols)

    http_payload = _mcp_tool_payload({"http": {"endpoint": "https://mcp.test/rpc"}})
    await registry.preflight_policy("lookup", http_payload)
    stdio_payload = _mcp_tool_payload({"stdio": {"command": command, "arguments": arguments}})
    await registry.preflight_policy("lookup", stdio_payload)

    assert authorized[0][:2] == ("mcp", "https://mcp.test/rpc")
    assert authorized[1][:2] == ("mcp", "stdio:fixture")
    assert authorized[1][2]["transport"] == {"stdio": {"binding": "fixture"}}
    assert requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "transport",
    [
        {"http": {"endpoint": "https://mcp.test/rpc"}, "stdio": {"command": "x"}},
        {"websocket": {"endpoint": "wss://mcp.test/rpc"}},
        {"http": {"endpoint": "https://user:password@mcp.test/rpc"}},
    ],
    ids=["mixed-transports", "unsupported-transport", "credentialed-url"],
)
async def test_role_mcp_rejects_invalid_transport_before_authorization_or_io(transport):
    requests = []
    authorized = []

    async def authorize_destination(protocol, endpoint, payload):
        authorized.append((protocol, endpoint))

    protocols = ProtocolServices(
        HttpClient(transport=httpx.MockTransport(lambda request: requests.append(request))),
        authorize_destination=authorize_destination,
    )
    registry = ToolRegistry.from_config([ToolConfig(type="mcp", name="lookup")], protocols)

    with pytest.raises(ToolError):
        await registry.preflight_policy("lookup", _mcp_tool_payload(transport))
    assert authorized == [] and requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("method", "prompts/list"),
        ("protocolVersion", "2025-01-01"),
        ("security_profile", "caller-selected"),
        ("securityProfile", "caller-selected"),
        ("endpoint", "https://other.test/rpc"),
    ],
)
async def test_role_mcp_rejects_route_and_credential_overrides_before_callbacks_or_io(field, value):
    requests = []
    authorized = []

    async def authorize_destination(protocol, endpoint, payload):
        authorized.append((protocol, endpoint))

    protocols = ProtocolServices(
        HttpClient(transport=httpx.MockTransport(lambda request: requests.append(request))),
        authorize_destination=authorize_destination,
    )
    registry = ToolRegistry.from_config(
        [
            ToolConfig(
                type="mcp",
                name="lookup",
                endpoint="https://mcp.test/rpc",
                options={"method": "tools/call", "protocolVersion": "2026-07-28"},
            )
        ],
        protocols,
    )
    payload = _mcp_tool_payload({"http": {"endpoint": "https://mcp.test/rpc"}})
    payload[field] = value

    with pytest.raises(ToolError):
        await registry.preflight_policy("lookup", payload)
    assert authorized == [] and requests == []


@pytest.mark.asyncio
async def test_role_mcp_refuses_http_credentials_for_stdio_before_authorization_or_io(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OWA_ROLE_TOOL_TOKEN", "role-tool-token")
    security = RuntimeConfig.model_validate(
        {
            "security": {
                "profiles": {
                    "outbound": {
                        "type": "bearer",
                        "token": {"from_env": "OWA_ROLE_TOOL_TOKEN"},
                    }
                }
            }
        }
    ).security
    config, command, arguments = _mcp_stdio_deployment(tmp_path)
    requests = []
    authorized = []

    async def authorize_destination(protocol, endpoint, payload):
        authorized.append((protocol, endpoint))

    protocols = ProtocolServices(
        HttpClient(transport=httpx.MockTransport(lambda request: requests.append(request))),
        authorize_destination=authorize_destination,
    )
    protocols.sandbox = SandboxManager(InternalSandboxBackend(config))
    registry = ToolRegistry.from_config(
        [ToolConfig(type="mcp", name="lookup", security_profile="outbound")],
        protocols,
        security=security,
    )

    with pytest.raises(ToolError, match="stdio role tool cannot use HTTP credentials"):
        await registry.preflight_policy(
            "lookup", _mcp_tool_payload({"stdio": {"command": command, "arguments": arguments}})
        )
    assert authorized == [] and requests == []


def _oauth_security(allowed_endpoints):
    return SecurityConfig.model_validate(
        {
            "profiles": {
                "peer-auth": {
                    "type": "oauth2_client_credentials",
                    "token_url": "https://issuer.test/token",
                    "client_id": {"from_env": "OWA_TEST_CLIENT_ID"},
                    "client_secret": {"from_env": "OWA_TEST_CLIENT_SECRET"},
                    "allowed_resource_endpoints": allowed_endpoints,
                }
            }
        }
    )


def _a2a_tool_payload(endpoint):
    return {
        "protocolVersion": "1.0",
        "method": "SendMessage",
        "parameters": {"message": {"parts": [{"text": "hello"}]}},
        "operation_id": "fixed-role-a2a-operation",
        "server": "https://ignored-user-server.test/a2a",
        "agentCard": json_format.MessageToDict(peer_card(endpoint)),
    }


@pytest.mark.asyncio
async def test_role_a2a_card_uses_exact_endpoint_and_oauth_resource_preflight(monkeypatch):
    monkeypatch.setenv("OWA_TEST_CLIENT_ID", "client-id")
    monkeypatch.setenv("OWA_TEST_CLIENT_SECRET", "client-secret")
    endpoint = "https://a2a.test/authorized"
    requests = []
    destinations = []
    credentials = []

    async def authorize_destination(protocol, selected, payload):
        destinations.append((protocol, selected))

    async def authorize_credential(profile):
        credentials.append(profile)

    protocols = ProtocolServices(
        HttpClient(
            allowed_hosts={"a2a.test", "issuer.test"},
            transport=httpx.MockTransport(lambda request: requests.append(request)),
        ),
        authorize_destination=authorize_destination,
        authorize_credential=authorize_credential,
    )
    registry = ToolRegistry.from_config(
        [ToolConfig(type="a2a", name="peer", endpoint=endpoint, security_profile="peer-auth")],
        protocols,
        security=_oauth_security([endpoint]),
    )

    await registry.preflight_policy("peer", _a2a_tool_payload(endpoint))

    assert destinations == [("a2a", endpoint), ("a2a", "https://issuer.test/token")]
    assert credentials == ["peer-auth"]
    assert requests == []


@pytest.mark.asyncio
async def test_role_a2a_rejects_changed_card_endpoint_and_unapproved_oauth_resource_before_io(
    monkeypatch,
):
    monkeypatch.setenv("OWA_TEST_CLIENT_ID", "client-id")
    monkeypatch.setenv("OWA_TEST_CLIENT_SECRET", "client-secret")
    allowed_endpoint = "https://a2a.test/authorized"
    changed_endpoint = "https://a2a.test/other"
    requests = []
    destinations = []

    async def authorize_destination(protocol, selected, payload):
        destinations.append((protocol, selected))

    protocols = ProtocolServices(
        HttpClient(
            allowed_hosts={"a2a.test", "issuer.test"},
            transport=httpx.MockTransport(lambda request: requests.append(request)),
        ),
        authorize_destination=authorize_destination,
    )
    bound_registry = ToolRegistry.from_config(
        [
            ToolConfig(
                type="a2a", name="peer", endpoint=allowed_endpoint, security_profile="peer-auth"
            )
        ],
        protocols,
        security=_oauth_security([allowed_endpoint, changed_endpoint]),
    )
    with pytest.raises(ToolError, match="configured A2A endpoint cannot be replaced"):
        await bound_registry.preflight_policy("peer", _a2a_tool_payload(changed_endpoint))
    assert destinations == [] and requests == []

    unbound_registry = ToolRegistry.from_config(
        [ToolConfig(type="a2a", name="peer", security_profile="peer-auth")],
        protocols,
        security=_oauth_security([allowed_endpoint]),
    )
    with pytest.raises(ToolError) as error:
        await unbound_registry.preflight_policy("peer", _a2a_tool_payload(changed_endpoint))
    assert error.value.details["reason"] == "unbound_card_credentials"
    assert destinations == [] and requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("method", "GetTask"),
        ("protocolVersion", "0.3"),
        ("security_profile", "caller-selected"),
        ("securityProfile", "caller-selected"),
        ("endpoint", "https://other.test/a2a"),
    ],
)
async def test_role_a2a_rejects_route_and_credential_overrides_before_callbacks_or_io(
    monkeypatch, field, value
):
    monkeypatch.setenv("OWA_TEST_CLIENT_ID", "client-id")
    monkeypatch.setenv("OWA_TEST_CLIENT_SECRET", "client-secret")
    endpoint = "https://a2a.test/authorized"
    requests = []
    destinations = []

    async def authorize_destination(protocol, selected, payload):
        destinations.append((protocol, selected))

    protocols = ProtocolServices(
        HttpClient(
            allowed_hosts={"a2a.test", "issuer.test"},
            transport=httpx.MockTransport(lambda request: requests.append(request)),
        ),
        authorize_destination=authorize_destination,
    )
    registry = ToolRegistry.from_config(
        [ToolConfig(type="a2a", name="peer", endpoint=endpoint, security_profile="peer-auth")],
        protocols,
        security=_oauth_security([endpoint]),
    )
    payload = _a2a_tool_payload(endpoint)
    payload[field] = value

    with pytest.raises(ToolError):
        await registry.preflight_policy("peer", payload)
    assert destinations == [] and requests == []


@pytest.mark.asyncio
async def test_role_preflight_authorization_denial_prevents_transport_dispatch():
    requests = []
    authorized = []

    async def deny_destination(protocol, endpoint, payload):
        authorized.append((protocol, endpoint))
        raise ToolError("destination denied")

    protocols = ProtocolServices(
        HttpClient(transport=httpx.MockTransport(lambda request: requests.append(request))),
        authorize_destination=deny_destination,
    )
    registry = ToolRegistry.from_config([ToolConfig(type="mcp", name="lookup")], protocols)

    with pytest.raises(ToolError, match="destination denied"):
        await registry.preflight_policy(
            "lookup", _mcp_tool_payload({"http": {"endpoint": "https://mcp.test/rpc"}})
        )
    assert authorized == [("mcp", "https://mcp.test/rpc")]
    assert requests == []


@pytest.mark.asyncio
async def test_role_credential_authorization_denial_prevents_oauth_or_business_http(monkeypatch):
    monkeypatch.setenv("OWA_TEST_CLIENT_ID", "client-id")
    monkeypatch.setenv("OWA_TEST_CLIENT_SECRET", "client-secret")
    endpoint = "https://a2a.test/authorized"
    requests = []
    callbacks = []

    async def authorize_destination(protocol, selected, payload):
        callbacks.append(("destination", protocol, selected))

    async def deny_credential(profile):
        callbacks.append(("credential", profile))
        raise ToolError("credential denied")

    protocols = ProtocolServices(
        HttpClient(
            allowed_hosts={"a2a.test", "issuer.test"},
            transport=httpx.MockTransport(lambda request: requests.append(request)),
        ),
        authorize_destination=authorize_destination,
        authorize_credential=deny_credential,
    )
    registry = ToolRegistry.from_config(
        [ToolConfig(type="a2a", name="peer", endpoint=endpoint, security_profile="peer-auth")],
        protocols,
        security=_oauth_security([endpoint]),
    )

    with pytest.raises(ToolError, match="credential denied"):
        await registry.preflight_policy("peer", _a2a_tool_payload(endpoint))
    assert callbacks == [
        ("destination", "a2a", endpoint),
        ("credential", "peer-auth"),
    ]
    assert requests == []
