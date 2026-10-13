from __future__ import annotations

import json
import sys

from open_workflow_agent.config import SandboxConfig
from open_workflow_agent.mcp_capabilities import mcp_capabilities
from open_workflow_agent.sandbox import InternalSandboxBackend, SandboxManager


def test_default_mcp_profile_reports_finite_http_and_disabled_stdio_without_conformance():
    value = mcp_capabilities(SandboxManager(InternalSandboxBackend(SandboxConfig())))
    assert value["protocolVersion"] == "2026-07-28"
    assert value["sdkVersion"] == "2.3.0"
    assert value["sdkAvailable"] is True
    assert value["conformanceClaim"] is False
    assert len(value["methods"]) == 7
    assert value["http"]["sse"] == "single_correlated_response"
    assert value["http"]["legacyNegotiation"] is False
    assert value["stdio"]["enabled"] is False
    assert value["stdio"]["externalBackends"] is False
    assert value["recovery"]["uncertainOutcome"] == "pending"
    assert not any(value["interactions"].values())


def test_mcp_stdio_projection_does_not_expose_command_environment_or_secret_bindings():
    config = SandboxConfig(
        enabled=True,
        file_size_bytes=None,
        mcp_stdio={
            "enabled": True,
            "servers": [
                {
                    "id": "private-server",
                    "command": sys.executable,
                    "arguments": ["private.py"],
                    "environment": {"PRIVATE_DEPLOYMENT": "private-literal"},
                    "secret_environment": {"MCP_TOKEN": "DEPLOYMENT_TOKEN_REFERENCE"},
                }
            ],
        },
        secret_environment=["DEPLOYMENT_TOKEN_REFERENCE"],
    )
    value = mcp_capabilities(SandboxManager(InternalSandboxBackend(config)))
    assert value["stdio"]["enabled"] is True
    assert value["stdio"]["hardIsolation"] is False
    assert value["stdio"]["resourceControls"]["wholeDeadline"] is True
    wire = json.dumps(value)
    for private in (
        sys.executable,
        "private-server",
        "private.py",
        "private-literal",
        "PRIVATE_DEPLOYMENT",
        "DEPLOYMENT_TOKEN_REFERENCE",
        "MCP_TOKEN",
    ):
        assert private not in wire


def test_unreviewed_sdk_does_not_advertise_an_enabled_transport(monkeypatch):
    monkeypatch.setattr("importlib.metadata.version", lambda _name: "2.4.0")
    value = mcp_capabilities(SandboxManager(InternalSandboxBackend(SandboxConfig())))
    assert value["sdkAvailable"] is False
    assert value["http"]["enabled"] is False and value["stdio"]["enabled"] is False
