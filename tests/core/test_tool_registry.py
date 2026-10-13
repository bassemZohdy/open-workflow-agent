from __future__ import annotations

import pytest
from open_workflow_agent.config import RuntimeConfig, ToolConfig
from open_workflow_agent.errors import ToolError
from open_workflow_agent.openapi_client import OpenApiDescription
from open_workflow_agent.protocols import HttpClient
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
