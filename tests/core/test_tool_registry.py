from __future__ import annotations

import pytest
from open_workflow_agent.config import RuntimeConfig, ToolConfig
from open_workflow_agent.errors import ToolError
from open_workflow_agent.tools import ToolRegistry


class ProtocolStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def call(self, protocol: str, payload: object) -> object:
        self.calls.append((protocol, payload))
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
                endpoint="https://api.test",
                security_profile="outbound",
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
        "Authorization": "Bearer tool-secret",
        "X-Caller": "yes",
    }

    await registry.invoke("weather", {"headers": {"X-Caller": "yes"}})
    openapi_payload = protocols.calls[1][1]
    assert openapi_payload["endpoint"] == "https://api.test"
    assert openapi_payload["headers"] == {
        "Authorization": "Bearer tool-secret",
        "X-Caller": "yes",
    }

    with pytest.raises(ToolError, match="configured tool not found"):
        await registry.invoke("missing", {})
