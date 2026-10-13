"""Externally configured agent tools and explicit workflow-call adapters."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .config import ToolConfig
from .errors import ToolError
from .openapi_client import OpenApiDescription, OpenApiOperationSelection, select_operation
from .protocols import AuthenticationProvider, ProtocolServices
from .security import ProfileAuthentication, SecurityConfig


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    type: str
    endpoint: str | None
    options: dict[str, Any]
    authentication: AuthenticationProvider | None = None
    openapi_selection: OpenApiOperationSelection | None = None


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
                authentication = ProfileAuthentication(
                    security, item.security_profile, token_http=protocols.http
                )
            openapi_selection = None
            if item.type == "openapi":
                if item.openapi_binding is None:
                    raise ToolError(
                        "configured OpenAPI tools require an immutable deployment binding"
                    )
                if (
                    set(item.options) - {"operationId", "output", "redirect"}
                    or item.options.get("output", "content") not in {"content", "response", "raw"}
                    or type(item.options.get("redirect", False)) is not bool
                ):
                    raise ToolError(
                        "configured OpenAPI tool options are outside the admitted profile"
                    )
                if not isinstance(item.options.get("operationId"), str):
                    raise ToolError("configured OpenAPI tools require an operationId")
                openapi_selection = select_operation(
                    OpenApiDescription.capture(
                        item.openapi_binding.document_uri, item.openapi_binding.document
                    ),
                    item.options["operationId"],
                    credential_profile=item.security_profile,
                )
            tools.append(
                ToolDefinition(
                    name=item.name or f"{item.type}-{index}",
                    type=item.type,
                    endpoint=item.endpoint,
                    options=item.options,
                    authentication=authentication,
                    openapi_selection=openapi_selection,
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
        if tool.type == "openapi" and (
            not isinstance(payload, dict) or set(payload) - {"parameters", "output", "redirect"}
        ):
            raise ToolError("OpenAPI tool input cannot replace its deployment operation binding")
        if isinstance(payload, dict) and tool.options:
            payload = {**tool.options, **payload}
        if tool.endpoint and isinstance(payload, dict):
            payload = {**payload, "endpoint": tool.endpoint}
        if tool.type == "openapi":
            if tool.openapi_selection is None or not isinstance(payload, dict):
                raise ToolError("configured OpenAPI tool has no admitted operation binding")
            selection = tool.openapi_selection
            if payload.get("operationId") != selection.operation_id:
                raise ToolError("configured OpenAPI operation cannot be replaced")
            return await self.protocols.call(
                tool.type,
                payload,
                _authentication=tool.authentication,
                _openapi_selection=selection,
            )
        if tool.authentication is not None:
            if tool.type == "a2a" and tool.endpoint is not None:
                return await self.protocols.call(
                    tool.type,
                    payload,
                    _authentication=tool.authentication,
                    _a2a_bound_endpoint=tool.endpoint,
                )
            return await self.protocols.call(
                tool.type, payload, _authentication=tool.authentication
            )
        return await self.protocols.call(tool.type, payload)
