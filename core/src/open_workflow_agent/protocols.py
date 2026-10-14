"""Common protocol clients with bounded, secure HTTP behavior."""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import math
import os
import re
import socket
import ssl
import time
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import urlparse
from uuid import uuid4

import httpx

from .credential_guard import (
    LiteralCredentialGuard,
    capture_authentication_headers,
    capture_credentials,
    credential_scope,
)
from .credential_guard import active_projection_guard as _active_projection_guard
from .errors import OwaError, ToolError
from .security import ProfileAuthentication, SecurityConfig

if TYPE_CHECKING:
    from .a2a_client import A2ACardSelection
    from .grpc_client import GrpcRegistry
    from .openapi_client import OpenApiOperationSelection
    from .sandbox import SandboxManager

_DESTINATION_POLICY: ContextVar[Callable[[str], Awaitable[None]] | None] = ContextVar(
    "owa_protocol_destination", default=None
)
_CREDENTIAL_POLICY: ContextVar[Callable[[str], Awaitable[None]] | None] = ContextVar(
    "owa_protocol_credential", default=None
)
DestinationAuthorization = Callable[[str, str, dict[str, Any]], Awaitable[None]]
CredentialAuthorization = Callable[[str], Awaitable[None]]

MCP_PROTOCOL_VERSION = "2026-07-28"
A2A_PROTOCOL_VERSION = "1.0"

MCP_METHODS = frozenset(
    {
        "tools/list",
        "tools/call",
        "prompts/list",
        "prompts/get",
        "resources/list",
        "resources/read",
        "resources/templates/list",
    }
)
A2A_METHODS = frozenset(
    {
        "SendMessage",
        "GetTask",
        "ListTasks",
        "CancelTask",
        "GetExtendedAgentCard",
        "SendStreamingMessage",
        "SubscribeToTask",
    }
)
OUTPUT_MODES = frozenset({"raw", "content", "response"})
PROTOCOL_DATA_FIELDS = frozenset(
    {
        "body",
        "arguments",
        "parameters",
        "params",
        "message",
        "input",
        "query",
        "metadata",
        "example",
        "examples",
        "default",
        "components",
        "paths",
        "responses",
    }
)

_OPEN_WORKFLOW_A2A_METHODS = {
    "message/send": "SendMessage",
    "message/stream": "SendStreamingMessage",
    "tasks/get": "GetTask",
    "tasks/list": "ListTasks",
    "tasks/cancel": "CancelTask",
    "tasks/resubscribe": "SubscribeToTask",
    "tasks/pushNotificationConfig/set": "CreateTaskPushNotificationConfig",
    "tasks/pushNotificationConfig/get": "GetTaskPushNotificationConfig",
    "tasks/pushNotificationConfig/list": "ListTaskPushNotificationConfigs",
    "tasks/pushNotificationConfig/delete": "DeleteTaskPushNotificationConfig",
    "agent/getAuthenticatedExtendedCard": "GetExtendedAgentCard",
}


def workflow_protocol_payload(protocol: str, payload: Any) -> Any:
    """Translate OWS method names at the workflow boundary, before wire dispatch."""
    if protocol == "a2a" and isinstance(payload, dict):
        method = payload.get("method")
        if isinstance(method, str) and method in _OPEN_WORKFLOW_A2A_METHODS:
            return {**payload, "method": _OPEN_WORKFLOW_A2A_METHODS[method]}
    return payload


class AuthenticationProvider(Protocol):
    def headers(self, endpoint: str) -> Mapping[str, str]: ...


class HttpClient:
    def __init__(
        self,
        *,
        timeout: float = 30.0,
        max_response_bytes: int = 4_000_000,
        verify_tls: bool = True,
        follow_redirects: bool = False,
        allowed_hosts: set[str] | None = None,
        authentication: AuthenticationProvider | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        destination_resolver: Callable[[str], Awaitable[tuple[str, ...]]] | None = None,
        client_cert: tuple[str, str] | None = None,
        ca_bundle: str | None = None,
    ) -> None:
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self.verify_tls = ca_bundle if ca_bundle else verify_tls
        self.follow_redirects = follow_redirects
        self.allowed_hosts = allowed_hosts
        self.authentication = authentication
        self.transport = transport
        self.destination_resolver = destination_resolver
        self.client_cert = client_cert

    async def request(
        self,
        method: str,
        endpoint: str,
        *,
        operation_id: str | None = None,
        follow_redirects: bool | None = None,
        output: str = "content",
        request_timeout: float | None = None,
        allow_not_modified: bool = False,
        _return_response: bool = False,
        _authentication: AuthenticationProvider | None = None,
        _on_dispatch: Callable[[], None] | None = None,
        _allow_redirect_status: bool = False,
        **kwargs: Any,
    ) -> Any:
        budget = self.timeout
        if request_timeout is not None:
            budget = min(budget, request_timeout)
        if not math.isfinite(budget) or budget <= 0:
            raise ToolError("HTTP request deadline must be finite and positive")
        with credential_scope() as guard:
            try:
                async with asyncio.timeout(budget):
                    result = await self._request_bounded(
                        method,
                        endpoint,
                        operation_id=operation_id,
                        follow_redirects=follow_redirects,
                        output=output,
                        request_timeout=budget,
                        allow_not_modified=allow_not_modified,
                        _return_response=_return_response,
                        _authentication=_authentication,
                        _on_dispatch=_on_dispatch,
                        _allow_redirect_status=_allow_redirect_status,
                        **kwargs,
                    )
                return result if _return_response else guard.project(result)
            except OwaError as error:
                guard.error(error)
                raise error from None
            except TimeoutError:
                raise ToolError("HTTP request deadline exceeded") from None

    async def _request_bounded(
        self,
        method: str,
        endpoint: str,
        *,
        operation_id: str | None = None,
        follow_redirects: bool | None = None,
        output: str = "content",
        request_timeout: float | None = None,
        allow_not_modified: bool = False,
        _return_response: bool = False,
        _authentication: AuthenticationProvider | None = None,
        _on_dispatch: Callable[[], None] | None = None,
        _allow_redirect_status: bool = False,
        **kwargs: Any,
    ) -> Any:
        self._check_endpoint(endpoint)
        destination_policy = _DESTINATION_POLICY.get()
        if destination_policy is not None:
            await destination_policy(endpoint)
        if output not in OUTPUT_MODES:
            raise ToolError(f"unsupported HTTP output mode: {output}")
        headers = dict(kwargs.pop("headers", {}) or {})
        authentication = _authentication if _authentication is not None else self.authentication
        redirects_enabled = self.follow_redirects if follow_redirects is None else follow_redirects
        if authentication is not None and redirects_enabled:
            raise ToolError("authenticated HTTP redirects are not enabled")
        timeout = self.timeout
        if request_timeout is not None:
            timeout = min(timeout, max(0.001, request_timeout))
        started = time.monotonic()
        client_cert = self.client_cert
        verify_tls: bool | str | ssl.SSLContext = self.verify_tls
        sensitive_headers: frozenset[str] = frozenset()
        capture_credentials(*(client_cert or ()))
        if isinstance(verify_tls, str):
            capture_credentials(verify_tls)
        if authentication:
            # Deployment-owned authentication and operation identity cannot be
            # replaced by workflow-provided headers.
            credential_policy = _CREDENTIAL_POLICY.get()
            profile_name = getattr(authentication, "profile_name", None)
            if credential_policy is not None and isinstance(profile_name, str):
                await credential_policy(profile_name)
            prepare = getattr(authentication, "prepare", None)
            if prepare is None:
                auth_headers = authentication.headers(endpoint)
            else:
                async with asyncio.timeout(timeout):
                    binding = await prepare(endpoint, remaining_timeout=timeout)
                auth_headers = binding.headers
                client_cert = binding.client_cert
                # An explicit different profile replaces global TLS trust.
                # Preparing the client's own authentication must preserve its
                # deployment-selected CA, including query-only recovery clients.
                inherited = _authentication is None or _authentication is self.authentication
                verify_tls = binding.ca_bundle or (
                    self.verify_tls if inherited and self.verify_tls is not False else True
                )
            capture_authentication_headers(auth_headers)
            capture_credentials(*(client_cert or ()))
            if isinstance(verify_tls, str):
                capture_credentials(verify_tls)
            headers = _merge_authoritative_headers(headers, auth_headers)
            sensitive_headers = frozenset(name.lower() for name in auth_headers)
        if operation_id:
            headers = _merge_authoritative_headers(
                headers,
                {
                    "X-OWA-Operation-ID": operation_id,
                    "Idempotency-Key": operation_id,
                },
            )
        if headers:
            kwargs["headers"] = headers
        try:
            request_transport = self.transport
            trust_env = self.destination_resolver is None
            if client_cert is not None:
                # HTTPX's filename-CA branch returns before loading cert. Bind
                # both trust and identity in one context for every transport.
                context = httpx.create_ssl_context(verify=verify_tls, trust_env=trust_env)
                context.load_cert_chain(*client_cert)
                verify_tls = context
                client_cert = None
            if self.destination_resolver is not None and self.transport is None:
                addresses = await self.destination_resolver(endpoint)
                request_transport = _pinned_transport(
                    verify_tls=verify_tls,
                    endpoint=endpoint,
                    addresses=addresses,
                    client_cert=client_cert,
                )
                # Ambient proxy and certificate environment variables must not
                # change the destination selected by the deployment policy.
                trust_env = False
            timeout -= time.monotonic() - started
            if timeout <= 0:
                raise ToolError("HTTP request deadline exceeded")
            async with httpx.AsyncClient(
                timeout=timeout,
                verify=verify_tls,
                follow_redirects=redirects_enabled,
                trust_env=trust_env,
                transport=request_transport,
                cert=client_cert,
            ) as client:
                if _on_dispatch is not None:
                    _on_dispatch()
                async with client.stream(method, endpoint, **kwargs) as streamed:
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in streamed.aiter_bytes():
                        total += len(chunk)
                        if total > self.max_response_bytes:
                            raise ToolError("HTTP response exceeds configured maximum size")
                        chunks.append(chunk)
                    response = httpx.Response(
                        streamed.status_code,
                        headers=streamed.headers,
                        content=b"".join(chunks),
                        request=streamed.request,
                        extensions=streamed.extensions,
                    )
                maximum_status = 400 if redirects_enabled or _allow_redirect_status else 300
                if not (
                    200 <= response.status_code < maximum_status
                    or (allow_not_modified and response.status_code == 304)
                ):
                    raise ToolError(
                        "HTTP request failed with an error status",
                        details={
                            "status": response.status_code,
                            "type": "https://open-workflow-specification.org/dsl/errors/types/communication",
                            "title": response.reason_phrase,
                            "detail": _project_response_bytes(
                                response, _active_projection_guard()
                            ).decode("utf-8", errors="replace")[:1024],
                        },
                    )
                return (
                    response
                    if _return_response
                    else _format_response(
                        response,
                        output,
                        _sensitive_headers=sensitive_headers,
                        _credential_guard=_active_projection_guard(),
                    )
                )
        except ToolError:
            raise
        except httpx.HTTPError:
            raise ToolError("HTTP request failed") from None
        except (ssl.SSLError, OSError):
            raise ToolError("HTTP TLS or transport binding failed") from None

    def _check_endpoint(self, endpoint: str) -> None:
        try:
            parsed = urlparse(endpoint)
            hostname = parsed.hostname
            port = parsed.port
        except ValueError as exc:
            raise ToolError("protocol endpoint must be an absolute HTTP(S) URL") from exc
        if (
            parsed.scheme not in {"http", "https"}
            or not hostname
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise ToolError("protocol endpoint must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password:
            raise ToolError("protocol endpoint cannot contain credentials")
        normalized_hostname = _normalize_hostname(hostname)
        if self.allowed_hosts is not None and normalized_hostname not in {
            _normalize_hostname(host) for host in self.allowed_hosts
        }:
            raise ToolError(f"protocol host is not allowed: {hostname}")


class ProtocolServices:
    def __init__(
        self,
        http: HttpClient | None = None,
        *,
        security: SecurityConfig | None = None,
        security_profile: str | None = None,
        a2a_credential_endpoints: Mapping[str, tuple[str, ...]] | None = None,
        openapi_credential_endpoints: Mapping[str, tuple[str, ...]] | None = None,
        grpc_registry: GrpcRegistry | None = None,
        grpc_credential_endpoints: Mapping[str, tuple[str, ...]] | None = None,
        authorize_destination: DestinationAuthorization | None = None,
        authorize_credential: CredentialAuthorization | None = None,
    ) -> None:
        self.authorize_destination = authorize_destination
        self.authorize_credential = authorize_credential
        self._a2a_credential_endpoints = {
            name: tuple(endpoints) for name, endpoints in (a2a_credential_endpoints or {}).items()
        }
        self._openapi_credential_endpoints = {
            name: tuple(endpoints)
            for name, endpoints in (openapi_credential_endpoints or {}).items()
        }
        self.grpc_registry = grpc_registry
        self._grpc_credential_endpoints = {
            name: tuple(endpoints) for name, endpoints in (grpc_credential_endpoints or {}).items()
        }
        authentication: AuthenticationProvider | None = None
        client_cert: tuple[str, str] | None = None
        ca_bundle: str | None = None
        self._profile_authentication: AuthenticationProvider | None = None
        self.sandbox: SandboxManager | None = None
        if security_profile:
            if security is None:
                raise ToolError(
                    "protocol security profile reference requires runtime security configuration"
                )
            auth = ProfileAuthentication(security, security_profile)
            self._profile_authentication = auth
            if hasattr(auth, "_supports_oauth2") and auth._supports_oauth2:
                authentication = auth
            elif hasattr(auth, "_supports_mtls") and auth._supports_mtls:
                authentication = auth
            else:
                authentication = auth
        self.http = http or HttpClient(
            allowed_hosts={
                host.strip()
                for host in os.getenv("OWA_ALLOWED_HOSTS", "").split(",")
                if host.strip()
            }
            or None,
            authentication=authentication,
            client_cert=client_cert,
            ca_bundle=ca_bundle,
        )
        if isinstance(self._profile_authentication, ProfileAuthentication):
            self._profile_authentication.bind_transport(self.http)

    @contextmanager
    def authorization_scope(
        self, protocol: str, payload: dict[str, Any] | None = None
    ) -> Iterator[None]:
        """Apply operation-local policy to metadata, issuer and business HTTP.

        This only binds callbacks. It resolves no credentials or destinations,
        opens no transport, and cannot issue an execution authority.
        """

        async def destination(endpoint: str) -> None:
            if self.authorize_destination is not None:
                await self.authorize_destination(protocol, endpoint, payload or {})

        destination_token = _DESTINATION_POLICY.set(
            destination if self.authorize_destination else None
        )
        credential_token = _CREDENTIAL_POLICY.set(self.authorize_credential)
        try:
            yield
        finally:
            _CREDENTIAL_POLICY.reset(credential_token)
            _DESTINATION_POLICY.reset(destination_token)

    async def authorize_prepared_call(
        self,
        protocol: str,
        payload: Any,
        *,
        _a2a_selection: A2ACardSelection | None = None,
        _openapi_selection: OpenApiOperationSelection | None = None,
    ) -> None:
        """Check exact prepared business destination before recording intent.

        Immutable card/description selection must already exist for discovered
        operations. Metadata fetches use authorization_scope separately.
        """
        if protocol == "mcp" and isinstance(payload, dict):
            from .mcp_client import validate_supported_configuration

            validate_supported_configuration(payload)
        if self.authorize_destination is None:
            return
        if not isinstance(payload, dict):
            raise ToolError("protocol payload must be an object")
        safe_payload = payload
        endpoint: str | None = None
        if protocol == "grpc":
            if self.grpc_registry is None:
                raise ToolError("gRPC deployment registry is not enabled")
            endpoint = self.grpc_registry.prepare(
                {key: value for key, value in payload.items() if key != "operation_id"}
            ).binding.endpoint
        elif protocol == "mcp":
            transport = payload.get("transport", {})
            if not isinstance(transport, dict):
                raise ToolError("mcp call requires a transport object")
            if "stdio" in transport:
                from .sandbox.backends.internal import InternalSandboxBackend
                from .sandbox.stdio_policy import validate_stdio_transport

                if self.sandbox is None or not isinstance(
                    self.sandbox.backend, InternalSandboxBackend
                ):
                    raise ToolError("mcp stdio transport is not enabled")
                binding = validate_stdio_transport(transport, self.sandbox.backend.config)
                endpoint = "stdio:" + binding.id
                safe_payload = {**payload, "transport": {"stdio": {"binding": binding.id}}}
            else:
                http = transport.get("http", {})
                if not isinstance(http, dict) or set(transport) - {"http"}:
                    raise ToolError("mcp HTTP transport is invalid")
                endpoint = _endpoint_value(payload.get("endpoint") or http.get("endpoint"))
        elif protocol == "a2a":
            if _a2a_selection is not None:
                from .a2a_client import A2ACardSelection

                endpoint = A2ACardSelection.from_value(_a2a_selection.as_value()).endpoint
            elif "agentCard" in payload:
                from .a2a_client import select_interface

                endpoint = select_interface(payload["agentCard"]).endpoint
            else:
                endpoint = _endpoint_value(payload.get("endpoint") or payload.get("server"))
        elif protocol == "openapi":
            if _openapi_selection is None:
                raise ToolError("OpenAPI business authorization requires admitted selection")
            from .openapi_client import prepare_request

            endpoint = prepare_request(_openapi_selection, payload.get("parameters", {})).endpoint
        elif protocol == "http":
            endpoint = _endpoint_value(payload.get("endpoint") or payload.get("url"))
        else:
            raise ToolError("unsupported protocol")
        if not endpoint:
            raise ToolError("protocol call requires an exact prepared endpoint")
        if protocol != "grpc" and not endpoint.startswith("stdio:"):
            self.http._check_endpoint(endpoint)
        await self.authorize_destination(protocol, endpoint, safe_payload)

    async def call(
        self,
        protocol: str,
        payload: Any,
        *,
        _authentication: AuthenticationProvider | None = None,
        _a2a_selection: A2ACardSelection | None = None,
        _a2a_bound_endpoint: str | None = None,
        _openapi_selection: OpenApiOperationSelection | None = None,
        _openapi_bound_endpoint: str | None = None,
    ) -> Any:
        if protocol not in {"http", "mcp", "a2a", "openapi", "grpc"} or not isinstance(
            payload, dict
        ):
            return await self._call_dispatch(protocol, payload)

        if protocol == "mcp":
            from .mcp_client import validate_supported_configuration

            validate_supported_configuration(payload)

        with self.authorization_scope(protocol, payload):
            with credential_scope() as guard:
                try:
                    if (
                        protocol == "mcp"
                        and isinstance(payload.get("transport"), dict)
                        and "stdio" in payload["transport"]
                    ):
                        from .sandbox.backends.internal import InternalSandboxBackend
                        from .sandbox.stdio_policy import validate_stdio_transport

                        if self.sandbox is None or not isinstance(
                            self.sandbox.backend, InternalSandboxBackend
                        ):
                            raise ToolError("mcp stdio transport is not enabled")
                        binding = validate_stdio_transport(
                            payload["transport"], self.sandbox.backend.config
                        )
                        if self.authorize_destination is not None:
                            safe_payload = {
                                **payload,
                                "transport": {"stdio": {"binding": binding.id}},
                            }
                            await self.authorize_destination(
                                protocol, "stdio:" + binding.id, safe_payload
                            )
                    result = await self._call_dispatch(
                        protocol,
                        payload,
                        _authentication=_authentication,
                        _a2a_selection=_a2a_selection,
                        _a2a_bound_endpoint=_a2a_bound_endpoint,
                        _openapi_selection=_openapi_selection,
                        _openapi_bound_endpoint=_openapi_bound_endpoint,
                    )
                    return guard.project(result)
                except OwaError as error:
                    guard.error(error)
                    raise error from None

    async def _call_dispatch(
        self,
        protocol: str,
        payload: Any,
        *,
        _authentication: AuthenticationProvider | None = None,
        _a2a_selection: A2ACardSelection | None = None,
        _a2a_bound_endpoint: str | None = None,
        _openapi_selection: OpenApiOperationSelection | None = None,
        _openapi_bound_endpoint: str | None = None,
    ) -> Any:
        if protocol not in {"http", "mcp", "a2a", "openapi", "grpc"}:
            raise ToolError(f"unsupported protocol: {protocol}")
        if not isinstance(payload, dict):
            raise ToolError(f"{protocol} payload must be an object")
        operation_id = _operation_id(payload, allow_operation_alias=protocol != "openapi")
        if protocol == "grpc":
            from .grpc_client import call_grpc

            if self.grpc_registry is None:
                raise ToolError("gRPC deployment registry is not enabled")
            authentication = _authentication or self._profile_authentication
            profile = getattr(authentication, "profile_name", None)
            prepared = self.grpc_registry.prepare(
                {key: value for key, value in payload.items() if key != "operation_id"}
            )
            if self.authorize_destination is not None:
                await self.authorize_destination(protocol, prepared.binding.endpoint, payload)
            if self.authorize_credential is not None and isinstance(profile, str):
                await self.authorize_credential(profile)
            return await call_grpc(
                self.http,
                {key: value for key, value in payload.items() if key != "operation_id"},
                registry=self.grpc_registry,
                authentication=authentication,
                credential_endpoints=frozenset(
                    self._grpc_credential_endpoints.get(profile, ())
                    if isinstance(profile, str)
                    else ()
                ),
                operation_key=operation_id,
            )
        if protocol == "http":
            endpoint = _endpoint_value(payload.get("endpoint") or payload.get("url"))
            if not endpoint:
                raise ToolError("http call requires endpoint")
            redirect = payload.get("redirect", False)
            if not isinstance(redirect, bool):
                raise ToolError("http redirect must be a boolean")
            return await self.http.request(
                str(payload.get("method", "GET")),
                str(endpoint),
                headers=payload.get("headers"),
                params=payload.get("query"),
                json=payload.get("body"),
                operation_id=operation_id,
                # OWS redirect selects accepted response statuses. It grants
                # no authority to visit a Location endpoint or forward secrets.
                follow_redirects=False,
                _allow_redirect_status=redirect,
                output=str(payload.get("output", "content")),
                request_timeout=_duration_seconds(payload.get("timeout")),
                _authentication=_authentication,
            )
        if protocol == "mcp":
            from .mcp_client import call_mcp

            return await call_mcp(
                self.http,
                payload,
                timeout=min(
                    self.http.timeout,
                    _duration_seconds(payload.get("timeout")) or self.http.timeout,
                ),
                operation_id=operation_id,
                authentication=(
                    _authentication or self._profile_authentication or self.http.authentication
                ),
                sandbox=getattr(self, "sandbox", None),
            )
        if protocol == "a2a":
            from .a2a_client import call_a2a

            return await call_a2a(
                self.http,
                payload,
                timeout=min(
                    self.http.timeout,
                    _duration_seconds(payload.get("timeout")) or self.http.timeout,
                ),
                operation_id=operation_id,
                authentication=(
                    _authentication or self._profile_authentication or self.http.authentication
                ),
                selection=_a2a_selection,
                credential_endpoints=self._a2a_credential_endpoints,
                bound_endpoint=_a2a_bound_endpoint,
            )
        if protocol == "openapi":
            from .openapi_client import call_openapi, fetch_validate_description, select_operation

            authentication = (
                _authentication or self._profile_authentication or self.http.authentication
            )
            started = time.monotonic()
            timeout = self.http.timeout
            requested_timeout = payload.get("timeout")
            if requested_timeout is not None:
                if isinstance(requested_timeout, bool):
                    raise ToolError("OpenAPI timeout must be finite and positive")
                try:
                    seconds = _duration_seconds(requested_timeout)
                except (ValueError, TypeError):
                    raise ToolError("OpenAPI timeout must be finite and positive") from None
                if seconds is None or not math.isfinite(seconds) or seconds <= 0:
                    raise ToolError("OpenAPI timeout must be finite and positive")
                timeout = min(timeout, seconds)
            selection = _openapi_selection
            if selection is None:
                document = payload.get("document") or (
                    {"endpoint": payload["endpoint"]} if "endpoint" in payload else None
                )
                if document is None:
                    raise ToolError("openapi call requires document endpoint")
                if not isinstance(payload.get("operationId"), str):
                    raise ToolError("openapi call requires operationId")
                description = await fetch_validate_description(self.http, document, timeout=timeout)
                selection = select_operation(
                    description,
                    payload["operationId"],
                    credential_profile=getattr(authentication, "profile_name", None),
                )
            return await call_openapi(
                self.http,
                payload,
                selection=selection,
                timeout=timeout - (time.monotonic() - started),
                operation_id=operation_id,
                authentication=authentication,
                credential_endpoints=self._openapi_credential_endpoints,
                bound_endpoint=_openapi_bound_endpoint,
            )
        endpoint = payload.get("endpoint")
        if not endpoint:
            raise ToolError(f"{protocol} call requires endpoint")
        return await self.http.request(
            str(payload.get("method", "POST")),
            str(endpoint),
            json=payload.get("body", payload),
            operation_id=operation_id,
        )


class _PinnedNetworkBackend:
    """Use only the addresses approved before opening the TCP connection.

    ``httpcore`` keeps the hostname in the HTTP origin and uses it for TLS SNI
    and certificate verification.  This wrapper changes only the host passed
    to the TCP connector, preventing a second DNS lookup after policy checks.
    The surrounding ``HttpClient`` pins one transport to one endpoint, so a
    different origin or port is rejected instead of reusing the address set.
    """

    def __init__(
        self,
        delegate: Any,
        *,
        hostname: str,
        port: int,
        addresses: tuple[str, ...],
    ) -> None:
        self._delegate = delegate
        self._hostname = _normalize_hostname(hostname)
        self._port = port
        self._addresses = addresses

    async def connect_tcp(self, host: str, port: int, **kwargs: Any) -> Any:
        if _normalize_hostname(host) != self._hostname or port != self._port:
            raise OSError("pinned transport received an unexpected network destination")
        last_error: Exception | None = None
        for address in self._addresses:
            try:
                return await self._delegate.connect_tcp(address, port, **kwargs)
            except Exception as exc:  # pragma: no cover - exercised by multi-address fallback
                last_error = exc
        if last_error is not None:
            raise last_error
        raise OSError("pinned transport has no approved network addresses")

    async def connect_unix_socket(self, path: str, **kwargs: Any) -> Any:
        return await self._delegate.connect_unix_socket(path, **kwargs)

    async def sleep(self, seconds: float) -> None:
        await self._delegate.sleep(seconds)


def _pinned_transport(
    *,
    verify_tls: bool | str | ssl.SSLContext,
    endpoint: str,
    addresses: tuple[str, ...],
    client_cert: tuple[str, str] | None = None,
) -> httpx.AsyncBaseTransport:
    if not addresses:
        raise ToolError("protocol endpoint has no approved network addresses")
    parsed = urlparse(endpoint)
    hostname = parsed.hostname
    if hostname is None:
        raise ToolError("protocol endpoint is missing a hostname")
    transport = httpx.AsyncHTTPTransport(verify=verify_tls, cert=client_cert, trust_env=False)
    # httpx 0.28.1 (pinned by this project) does not expose httpcore's network
    # backend in its public transport constructor.  Keep the compatibility
    # check close to the security boundary so a future upgrade fails closed.
    pool = getattr(transport, "_pool", None)
    backend = getattr(pool, "_network_backend", None)
    if pool is None or backend is None:
        raise ToolError("pinned HTTP transport is unavailable")
    pool._network_backend = _PinnedNetworkBackend(
        backend,
        hostname=hostname,
        port=parsed.port or (443 if parsed.scheme == "https" else 80),
        addresses=addresses,
    )
    return transport


async def resolve_public_addresses_async(
    endpoint: str, *, timeout: float = 10.0
) -> tuple[str, ...]:
    """Async form used by network clients before creating a pinned transport."""

    parsed = urlparse(endpoint)
    host = parsed.hostname
    if host is None:
        raise ToolError("protocol endpoint is missing a hostname")
    literal = _public_ip(host)
    if literal is not None:
        if not literal.is_global:
            raise ToolError("protocol endpoint resolves to a disallowed IP address")
        return (str(literal),)
    return await _resolve_host(host, parsed.port or 443, timeout)


async def _resolve_host(host: str, port: int, timeout: float) -> tuple[str, ...]:
    try:
        records = await asyncio.wait_for(
            asyncio.to_thread(
                socket.getaddrinfo,
                host,
                port,
                type=socket.SOCK_STREAM,
            ),
            timeout=max(0.001, timeout),
        )
    except TimeoutError as exc:
        raise ToolError("protocol endpoint resolution timed out") from exc
    except OSError as exc:
        raise ToolError("protocol endpoint could not be resolved") from exc
    try:
        addresses = tuple(
            dict.fromkeys(
                str(ipaddress.ip_address(item[4][0])) for item in records if item[4] and item[4][0]
            )
        )
    except (IndexError, TypeError, ValueError) as exc:
        raise ToolError("protocol endpoint could not be resolved") from exc
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ToolError("protocol endpoint resolves to a disallowed IP address")
    return addresses


def _public_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _normalize_hostname(host: str) -> str:
    try:
        return host.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError:
        return host.rstrip(".").lower()


def _merge_authoritative_headers(
    headers: Mapping[Any, Any], authoritative: Mapping[str, str]
) -> dict[str, str]:
    """Merge deployment-owned headers while honoring HTTP name case rules."""

    protected = {name.lower() for name in authoritative}
    merged = {
        str(name): str(value)
        for name, value in headers.items()
        if str(name).lower() not in protected
    }
    merged.update(authoritative)
    return merged


def _operation_id(payload: dict[str, Any], *, allow_operation_alias: bool = True) -> str:
    value = payload.get("operation_id")
    if allow_operation_alias:
        value = value or payload.get("operationId")
    value = value or payload.get("id")
    return str(value) if value else str(uuid4())


def _endpoint_value(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return None
    if "endpoint" in value:
        return _endpoint_value(value["endpoint"])
    endpoint = value.get("uri")
    return str(endpoint) if isinstance(endpoint, str) else None


def _project_response_bytes(
    response: httpx.Response, guard: LiteralCredentialGuard | None
) -> bytes:
    if guard is None:
        return response.content
    try:
        value = response.json()
    except ValueError:
        return guard.raw(response.content)
    projected = guard.project(value)
    if projected == value:
        return response.content
    return json.dumps(projected, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(
        "utf-8"
    )


def _format_response(
    response: httpx.Response,
    output: str,
    *,
    _sensitive_headers: frozenset[str] = frozenset(),
    _credential_guard: LiteralCredentialGuard | None = None,
) -> Any:
    if output == "raw":
        content = _project_response_bytes(response, _credential_guard)
        return base64.b64encode(content).decode("ascii")
    try:
        body: Any = response.json()
    except ValueError:
        body = response.text
    if output == "response":
        formatted: dict[str, Any] = {
            "status": response.status_code,
            "headers": {
                key: value
                for key, value in response.headers.items()
                if key.lower() not in {"authorization", "proxy-authorization", "set-cookie"}
                and key.lower() not in _sensitive_headers
            },
            "body": body,
            # Keep the compact response keys used by OWA callers while also
            # exposing the Open Workflow response vocabulary. Request
            # metadata is deliberately limited to non-sensitive headers.
            "statusCode": response.status_code,
            "content": body,
        }
        try:
            request = response.request
        except RuntimeError:
            request = None
        formatted["request"] = (
            {
                "method": request.method,
                "uri": str(request.url),
                "headers": {
                    key: value
                    for key, value in request.headers.items()
                    if key.lower() not in {"authorization", "cookie", "proxy-authorization"}
                    and key.lower() not in _sensitive_headers
                },
            }
            if request is not None
            else {}
        )
        return formatted
    return body


def _duration_seconds(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, dict):
        units = {
            "days": 86_400,
            "hours": 3_600,
            "minutes": 60,
            "seconds": 1,
            "milliseconds": 0.001,
        }
        if any(unit in value for unit in units):
            value = sum(
                float(value.get(unit, 0)) * multiplier for unit, multiplier in units.items()
            )
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds <= 0:
            raise ToolError(f"invalid protocol timeout: {value}")
        return seconds
    text = str(value).strip().lower()
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*(ms|s|m|h)?", text)
    if not match:
        raise ToolError(f"invalid protocol timeout: {value}")
    number = float(match.group(1))
    seconds = {
        None: number,
        "ms": number / 1000,
        "s": number,
        "m": number * 60,
        "h": number * 3600,
    }[match.group(2)]
    if seconds <= 0:
        raise ToolError(f"invalid protocol timeout: {value}")
    return seconds
