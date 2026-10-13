"""Finite common MCP profile without credentials or deployment command details."""

from __future__ import annotations

import importlib.metadata
from typing import Any

from .protocols import MCP_METHODS, MCP_PROTOCOL_VERSION
from .sandbox import SandboxManager


def mcp_capabilities(sandbox: SandboxManager) -> dict[str, Any]:
    reviewed_sdk = "2.3.0"
    try:
        sdk_available = all(
            importlib.metadata.version(name) == reviewed_sdk for name in ("mcp", "mcp-types")
        )
    except importlib.metadata.PackageNotFoundError:
        sdk_available = False

    backend = sandbox.capabilities()
    stdio = backend.get("mcpStdio", {})
    return {
        "protocolVersion": MCP_PROTOCOL_VERSION,
        "sdkVersion": reviewed_sdk,
        "sdkAvailable": sdk_available,
        "profile": "bounded common client/tool profile",
        "conformanceClaim": False,
        "methods": sorted(MCP_METHODS),
        "result": "validated_jsonrpc_result",
        "http": {
            "enabled": sdk_available,
            "json": True,
            "sse": "single_correlated_response",
            "discovery": False,
            "legacyNegotiation": False,
            "reconnect": False,
        },
        "stdio": {
            "enabled": sdk_available and bool(stdio.get("enabled", False)),
            "deploymentBindingRequired": True,
            "externalBackends": False,
            "hardIsolation": False,
            "processTree": stdio.get("processTree", "unavailable"),
            "resourceControls": dict(stdio.get("resourceControls", {})),
        },
        "authentication": {
            "deploymentReferences": True,
            "staticHttpProfiles": ["bearer", "api_key", "mtls"],
            "oauth": "bound_client_credentials",
            "interactive": False,
            "delegated": False,
            "stdio": "approved_environment_references",
        },
        "interactions": {"inputRequired": False, "tasks": False, "notifications": False},
        "recovery": {
            "knownReceiptReplay": True,
            "uncertainOutcome": "pending",
            "automaticBusinessReplay": False,
            "remoteRollback": False,
        },
    }
