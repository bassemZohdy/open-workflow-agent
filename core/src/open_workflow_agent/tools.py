"""Externally configured agent tools and explicit workflow-call adapters."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .config import ToolConfig
from .errors import ToolError
from .openapi_client import (
    OpenApiDescription,
    OpenApiOperationSelection,
    prepare_request,
    select_operation,
    validate_operation_authority,
)
from .protocols import AuthenticationProvider, ProtocolServices
from .security import OAuth2ClientCredentialsSecurityProfile, ProfileAuthentication, SecurityConfig


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
        self.authorize: Callable[[str, Any], Awaitable[None]] | None = None
        self.authority_enabled: Callable[[], bool] = lambda: False

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

    def validate_payload(self, name: str, payload: Any, *, strict: bool | None = None) -> Any:
        """Pure routing/credential binding preflight; no policy, auth or transport."""
        tool = self.tools.get(name)
        if tool is None:
            raise ToolError(f"configured tool not found: {name}")
        if strict is None:
            from .agent_roles import current_role_descriptor

            strict = self.authority_enabled() or current_role_descriptor() is not None
        if strict and isinstance(payload, dict):
            if {"security_profile", "securityProfile"}.intersection(payload):
                raise ToolError("agent tool input cannot select deployment credentials")
            for key in ("method", "name", "transport", "protocolVersion", "binding"):
                if key in tool.options and key in payload and payload[key] != tool.options[key]:
                    raise ToolError("agent tool input cannot replace its deployment routing")
            if (
                "endpoint" in payload
                and tool.endpoint is not None
                and payload["endpoint"] != tool.endpoint
            ):
                raise ToolError("agent tool input cannot replace its deployment endpoint")
        if tool.type == "openapi" and (
            not isinstance(payload, dict) or set(payload) - {"parameters", "output", "redirect"}
        ):
            raise ToolError("OpenAPI tool input cannot replace its deployment operation binding")
        if isinstance(payload, dict) and tool.options:
            payload = {**tool.options, **payload}
        if tool.endpoint and isinstance(payload, dict):
            payload = {**payload, "endpoint": tool.endpoint}
        if strict:
            if not isinstance(payload, dict):
                raise ToolError("role protocol tool requires a bounded object")
            if tool.type == "openapi":
                if (
                    tool.openapi_selection is None
                    or payload.get("operationId") != tool.openapi_selection.operation_id
                    or payload.get("output", "content") not in {"content", "response", "raw"}
                    or type(payload.get("redirect", False)) is not bool
                ):
                    raise ToolError("OpenAPI tool operation/projection is invalid")
                prepare_request(tool.openapi_selection, payload.get("parameters", {}))
            elif tool.type == "mcp":
                from .mcp_client import MCP_CALL_METHODS, MCP_VERSION, _request

                method = payload.get("method", "tools/call")
                if (
                    type(method) is not str
                    or method not in MCP_CALL_METHODS
                    or payload.get("protocolVersion", MCP_VERSION) != MCP_VERSION
                ):
                    raise ToolError("role MCP tool method/version is invalid")
                _request(payload, method)
            elif tool.type == "a2a":
                from ._a2a_types import A2A_METHODS, A2A_PROTOCOL_VERSION
                from .a2a_client import _parameters

                method = payload.get("method", "SendMessage")
                if (
                    type(method) is not str
                    or method not in A2A_METHODS
                    or payload.get("protocolVersion", A2A_PROTOCOL_VERSION) != A2A_PROTOCOL_VERSION
                ):
                    raise ToolError("role A2A tool method/version is invalid")
                _parameters(payload, method, None)
        return payload

    async def preflight_policy(self, name: str, payload: Any) -> None:
        """Authorize the actual bound route and credential without I/O or resolution."""
        prepared = self.validate_payload(name, payload, strict=True)
        tool = self.tools[name]
        authentication = (
            tool.authentication
            or self.protocols._profile_authentication
            or self.protocols.http.authentication
        )
        endpoint = None
        if tool.type == "openapi":
            if tool.openapi_selection is None:
                raise ToolError("configured OpenAPI tool has no admitted operation binding")
            validate_operation_authority(
                tool.openapi_selection,
                prepared.get("parameters", {}),
                authentication=authentication,
                credential_endpoints=self.protocols._openapi_credential_endpoints,
                bound_endpoint=tool.endpoint,
            )
            endpoint = prepare_request(
                tool.openapi_selection, prepared.get("parameters", {})
            ).endpoint
        elif tool.type == "a2a":
            from .a2a_client import _authorize_card_credentials, select_interface
            from .protocols import _endpoint_value

            if "agentCard" in prepared:
                selection = select_interface(
                    prepared["agentCard"],
                    credential_profile=getattr(authentication, "profile_name", None),
                )
                endpoint = selection.endpoint
                _authorize_card_credentials(
                    self.protocols.http,
                    endpoint,
                    authentication,
                    self.protocols._a2a_credential_endpoints,
                    tool.endpoint,
                )
                if tool.endpoint is not None and tool.endpoint != endpoint:
                    raise ToolError("configured A2A endpoint cannot be replaced")
            else:
                endpoint = _endpoint_value(prepared.get("endpoint") or prepared.get("server"))
        elif tool.type == "mcp":
            from .protocols import _endpoint_value

            transport = prepared.get("transport", {})
            if (
                type(transport) is not dict
                or set(transport) - {"http", "stdio"}
                or {"http", "stdio"} <= set(transport)
            ):
                raise ToolError("role MCP transport is invalid")
            if "stdio" in transport:
                if authentication is not None:
                    raise ToolError("stdio role tool cannot use HTTP credentials")
            else:
                http_transport = transport.get("http", {})
                if type(http_transport) is not dict:
                    raise ToolError("role MCP HTTP transport is invalid")
                endpoint = _endpoint_value(
                    prepared.get("endpoint") or http_transport.get("endpoint")
                )
        await self.protocols.authorize_prepared_call(
            tool.type, prepared, _openapi_selection=tool.openapi_selection
        )
        if isinstance(authentication, ProfileAuthentication):
            if self.protocols.authorize_credential is not None:
                await self.protocols.authorize_credential(authentication.profile_name)
            profile = authentication.security.profile(authentication.profile_name)
            if isinstance(profile, OAuth2ClientCredentialsSecurityProfile):
                if endpoint not in profile.allowed_resource_endpoints:
                    raise ToolError("role OAuth resource is outside its exact deployment binding")
                if self.protocols.authorize_destination is not None:
                    await self.protocols.authorize_destination(
                        tool.type, profile.token_url, prepared
                    )

    async def invoke(self, name: str, payload: Any) -> Any:
        prepared = self.validate_payload(name, payload)
        tool = self.tools[name]
        if self.authorize is not None:
            await self.authorize(name, payload)
        if self.authority_enabled() and (
            self.protocols.authorize_destination is None
            or self.protocols.authorize_credential is None
        ):
            raise ToolError("configured tool authorization transport is unavailable")
        payload = prepared
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
                _openapi_bound_endpoint=tool.endpoint,
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
