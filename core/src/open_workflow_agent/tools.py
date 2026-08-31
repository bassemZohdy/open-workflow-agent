"""Externally configured agent tools and explicit workflow-call adapters."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .config import ToolConfig
from .errors import ToolError
from .protocols import AuthenticationProvider, ProtocolServices
from .security import ProfileAuthentication, SecurityConfig


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    type: str
    endpoint: str | None
    options: dict[str, Any]
    authentication: AuthenticationProvider | None = None


@dataclass(frozen=True, slots=True)
class AgentToolBinding:
    name: str
    description: str
    invoke: Callable[[Any], Awaitable[Any]]


class ToolRegistry:
    def __init__(
        self, tools: list[ToolDefinition] | None = None, protocols: ProtocolServices | None = None
    ) -> None:
        self.tools = {tool.name: tool for tool in tools or []}
        self.protocols = protocols or ProtocolServices()

    @classmethod
    def from_config(
        cls,
        config: list[ToolConfig],
        protocols: ProtocolServices,
        *,
        security: SecurityConfig | None = None,
    ) -> ToolRegistry:
        tools = []
        for index, item in enumerate(config):
            authentication = None
            if item.security_profile:
                if security is None:
                    raise ToolError(
                        f"tool {item.name or index} references a security profile "
                        "but runtime security configuration is absent"
                    )
                authentication = ProfileAuthentication(security, item.security_profile)
            tools.append(
                ToolDefinition(
                    name=item.name or f"{item.type}-{index}",
                    type=item.type,
                    endpoint=item.endpoint,
                    options=item.options,
                    authentication=authentication,
                )
            )
        return cls(tools, protocols)

    def names(self) -> tuple[str, ...]:
        return tuple(self.tools)

    def bindings(self) -> tuple[AgentToolBinding, ...]:
        bindings: list[AgentToolBinding] = []
        for tool in self.tools.values():

            async def invoke(payload: Any, name: str = tool.name) -> Any:
                return await self.invoke(name, payload)

            bindings.append(
                AgentToolBinding(
                    name=tool.name,
                    description=f"Configured {tool.type} tool {tool.name}",
                    invoke=invoke,
                )
            )
        return tuple(bindings)

    async def invoke(self, name: str, payload: Any) -> Any:
        tool = self.tools.get(name)
        if tool is None:
            raise ToolError(f"configured tool not found: {name}")
        if isinstance(payload, dict) and tool.options:
            payload = {**tool.options, **payload}
        if tool.authentication is not None and isinstance(payload, dict):
            endpoint = tool.endpoint or "http://localhost"
            profile_headers = dict(tool.authentication.headers(endpoint))
            if tool.type == "mcp":
                transport = dict(payload.get("transport") or {})
                http_transport = dict(transport.get("http") or {})
                http_transport["headers"] = {
                    **profile_headers,
                    **dict(http_transport.get("headers") or {}),
                }
                transport["http"] = http_transport
                payload["transport"] = transport
            else:
                payload["headers"] = {**profile_headers, **dict(payload.get("headers") or {})}
        if tool.endpoint and isinstance(payload, dict):
            payload = {**payload, "endpoint": tool.endpoint}
        return await self.protocols.call(tool.type, payload)
