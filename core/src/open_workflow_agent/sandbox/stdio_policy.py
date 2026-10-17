"""Pure deployment admission for the private MCP duplex sandbox capability."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from ..config import McpStdioServerConfig, SandboxConfig
from ..errors import SandboxPolicyError


def validate_stdio_transport(transport: Any, sandbox_config: SandboxConfig) -> McpStdioServerConfig:
    if (
        not sandbox_config.enabled
        or sandbox_config.backend != "internal"
        or not sandbox_config.mcp_stdio.enabled
    ):
        raise SandboxPolicyError("MCP stdio requires an enabled internal deployment binding")
    if os.name == "nt" and sandbox_config.file_size_bytes is not None:
        raise SandboxPolicyError("MCP stdio file-size resource limits are unavailable on Windows")
    if set(sandbox_config.inherited_environment) & {"PATH", "HOME", "TMPDIR"}:
        raise SandboxPolicyError("MCP stdio cannot inherit reserved workspace environment")
    if os.name == "posix":
        import resource

        if not callable(getattr(resource, "setrlimit", None)) or any(
            limit is not None and getattr(resource, name, None) is None
            for name, limit in (
                ("RLIMIT_CPU", sandbox_config.cpu_seconds),
                ("RLIMIT_AS", sandbox_config.memory_bytes),
                ("RLIMIT_FSIZE", sandbox_config.file_size_bytes),
                ("RLIMIT_NPROC", sandbox_config.process_count),
            )
        ):
            raise SandboxPolicyError("MCP stdio configured resource limits are unavailable")
    if not isinstance(transport, Mapping) or set(transport) != {"stdio"}:
        raise SandboxPolicyError("MCP stdio transport configuration is unsupported")
    value = transport["stdio"]
    if not isinstance(value, Mapping) or set(value) - {"command", "arguments", "environment"}:
        raise SandboxPolicyError("MCP stdio server configuration is invalid")
    command, arguments = value.get("command"), value.get("arguments", [])
    environment = value.get("environment")
    if (
        not isinstance(command, str)
        or not isinstance(arguments, list)
        or any(not isinstance(item, str) or "${" in item or "\x00" in item for item in arguments)
        or "${" in command
        or "\x00" in command
        or (
            environment is not None
            and (
                not isinstance(environment, Mapping)
                or any(
                    not isinstance(key, str)
                    or not isinstance(item, str)
                    or "${" in item
                    or "\x00" in item
                    for key, item in environment.items()
                )
            )
        )
    ):
        raise SandboxPolicyError("MCP stdio requires fixed deployment-approved values")
    for server in sandbox_config.mcp_stdio.servers:
        if server.command == command and server.arguments == arguments:
            if environment is not None and dict(environment) != server.environment:
                raise SandboxPolicyError("MCP stdio environment does not match deployment binding")
            return server
    raise SandboxPolicyError("MCP stdio executable binding is not deployment-approved")
