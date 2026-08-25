"""Common protocol clients with bounded, secure HTTP behavior."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any, Protocol
from urllib.parse import urlparse
from uuid import uuid4

import httpx

from .errors import ToolError

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
        "message/send",
        "message/stream",
        "tasks/get",
        "tasks/list",
        "tasks/cancel",
        "tasks/resubscribe",
        "tasks/pushNotificationConfig/set",
        "tasks/pushNotificationConfig/get",
        "tasks/pushNotificationConfig/list",
        "tasks/pushNotificationConfig/delete",
        "agent/getAuthenticatedExtendedCard",
    }
)
OUTPUT_MODES = frozenset({"raw", "content", "response"})


class AuthenticationProvider(Protocol):
    def headers(self, endpoint: str) -> Mapping[str, str]: ...


class EnvironmentAuthentication:
    """Resolve bearer/basic credentials from environment variables at runtime."""

    def __init__(
        self,
        *,
        bearer_env: str | None = None,
        username_env: str | None = None,
        password_env: str | None = None,
    ) -> None:
        self.bearer_env = bearer_env
        self.username_env = username_env
        self.password_env = password_env

    def headers(self, endpoint: str) -> Mapping[str, str]:
        del endpoint
        if self.bearer_env:
            token = os.getenv(self.bearer_env)
            if token:
                return {"Authorization": f"Bearer {token}"}
        if self.username_env and self.password_env:
            username = os.getenv(self.username_env)
            password = os.getenv(self.password_env)
            if username is not None and password is not None:
                import base64

                encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
                return {"Authorization": f"Basic {encoded}"}
        return {}


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
    ) -> None:
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self.verify_tls = verify_tls
        self.follow_redirects = follow_redirects
        self.allowed_hosts = allowed_hosts
        self.authentication = authentication
        self.transport = transport

    async def request(
        self,
        method: str,
        endpoint: str,
        *,
        operation_id: str | None = None,
        follow_redirects: bool | None = None,
        output: str = "content",
        request_timeout: float | None = None,
        **kwargs: Any,
    ) -> Any:
        self._check_endpoint(endpoint)
        if output not in OUTPUT_MODES:
            raise ToolError(f"unsupported HTTP output mode: {output}")
        headers = dict(kwargs.pop("headers", {}) or {})
        if self.authentication:
            headers = {**self.authentication.headers(endpoint), **headers}
        if operation_id:
            headers.setdefault("X-OWA-Operation-ID", operation_id)
            headers.setdefault("Idempotency-Key", operation_id)
        if headers:
            kwargs["headers"] = headers
        try:
            timeout = self.timeout
            if request_timeout is not None:
                timeout = min(timeout, max(0.001, request_timeout))
            redirects_enabled = (
                self.follow_redirects if follow_redirects is None else follow_redirects
            )
            async with httpx.AsyncClient(
                timeout=timeout,
                verify=self.verify_tls,
                follow_redirects=redirects_enabled,
                transport=self.transport,
            ) as client:
                response = await client.request(method, endpoint, **kwargs)
                if len(response.content) > self.max_response_bytes:
                    raise ToolError("HTTP response exceeds configured maximum size")
                if response.is_error or (response.is_redirect and not redirects_enabled):
                    raise ToolError(
                        "HTTP request failed with an error status",
                        details={
                            "status": response.status_code,
                            "type": "https://open-workflow-specification.org/dsl/errors/types/communication",
                            "title": response.reason_phrase,
                            "detail": response.text[:1024],
                        },
                    )
                return _format_response(response, output)
        except ToolError:
            raise
        except httpx.HTTPError as exc:
            raise ToolError(f"HTTP request failed: {exc}") from exc

    def _check_endpoint(self, endpoint: str) -> None:
        parsed = urlparse(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ToolError("protocol endpoint must be an absolute HTTP(S) URL")
        if self.allowed_hosts is not None and parsed.hostname not in self.allowed_hosts:
            raise ToolError(f"protocol host is not allowed: {parsed.hostname}")


class ProtocolServices:
    def __init__(self, http: HttpClient | None = None) -> None:
        self.http = http or HttpClient(
            allowed_hosts={
                host.strip()
                for host in os.getenv("OWA_ALLOWED_HOSTS", "").split(",")
                if host.strip()
            }
            or None,
            authentication=(
                EnvironmentAuthentication(
                    bearer_env=os.getenv("OWA_BEARER_TOKEN_ENV"),
                    username_env=os.getenv("OWA_BASIC_USERNAME_ENV"),
                    password_env=os.getenv("OWA_BASIC_PASSWORD_ENV"),
                )
                if any(
                    os.getenv(name)
                    for name in (
                        "OWA_BEARER_TOKEN_ENV",
                        "OWA_BASIC_USERNAME_ENV",
                        "OWA_BASIC_PASSWORD_ENV",
                    )
                )
                else None
            ),
        )

    async def call(self, protocol: str, payload: Any) -> Any:
        if protocol not in {"http", "mcp", "a2a", "openapi"}:
            raise ToolError(f"unsupported protocol: {protocol}")
        if not isinstance(payload, dict):
            raise ToolError(f"{protocol} payload must be an object")
        operation_id = _operation_id(payload)
        if protocol == "http":
            endpoint = _endpoint_value(payload.get("endpoint") or payload.get("url"))
            if not endpoint:
                raise ToolError("http call requires endpoint")
            return await self.http.request(
                str(payload.get("method", "GET")),
                str(endpoint),
                headers=payload.get("headers"),
                params=payload.get("query"),
                json=payload.get("body"),
                operation_id=operation_id,
                follow_redirects=payload.get("redirect"),
                output=str(payload.get("output", "content")),
                request_timeout=_duration_seconds(payload.get("timeout")),
            )
        if protocol == "mcp":
            transport = payload.get("transport", {})
            if not isinstance(transport, dict):
                raise ToolError("mcp call requires a transport object")
            if "stdio" in transport:
                raise ToolError("mcp stdio transport is not enabled")
            http_transport = transport.get("http", {})
            if not isinstance(http_transport, dict):
                raise ToolError("mcp HTTP transport must be an object")
            endpoint = _endpoint_value(payload.get("endpoint") or http_transport.get("endpoint"))
            if not endpoint:
                raise ToolError("mcp call requires transport.http.endpoint")
            method = payload.get("method", "tools/call")
            if method not in MCP_METHODS:
                raise ToolError(f"unsupported MCP method: {method}")
            parameters = payload.get("parameters")
            if parameters is None:
                parameters = {
                    "name": payload.get("name", payload.get("tool")),
                    "arguments": payload.get("arguments", payload.get("input", {})),
                }
            body = {
                "jsonrpc": "2.0",
                "id": payload.get("id", operation_id or str(uuid4())),
                "method": method,
                "params": parameters,
            }
            headers = dict(http_transport.get("headers", {}) or {})
            if payload.get("protocolVersion"):
                headers.setdefault("MCP-Protocol-Version", str(payload["protocolVersion"]))
            client = payload.get("client")
            if isinstance(client, dict):
                if client.get("name"):
                    headers.setdefault("MCP-Client-Name", str(client["name"]))
                if client.get("version"):
                    headers.setdefault("MCP-Client-Version", str(client["version"]))
            return await self.http.request(
                "POST",
                str(endpoint),
                headers=headers,
                json=body,
                operation_id=operation_id,
                request_timeout=_duration_seconds(payload.get("timeout")),
            )
        if protocol == "a2a":
            agent_card = payload.get("agentCard")
            card_endpoint = agent_card.get("endpoint") if isinstance(agent_card, dict) else None
            endpoint = _endpoint_value(
                payload.get("endpoint") or payload.get("server") or card_endpoint
            )
            if not endpoint:
                raise ToolError("a2a call requires server endpoint")
            method = payload.get("method", "message/send")
            if method not in A2A_METHODS:
                raise ToolError(f"unsupported A2A method: {method}")
            body = {
                "jsonrpc": "2.0",
                "id": payload.get("id", operation_id or str(uuid4())),
                "method": method,
                "params": payload.get(
                    "parameters", payload.get("params", payload.get("message", {}))
                ),
            }
            return await self.http.request(
                "POST",
                str(endpoint),
                json=body,
                operation_id=operation_id,
                request_timeout=_duration_seconds(payload.get("timeout")),
            )
        if protocol == "openapi":
            document = payload.get("document")
            endpoint = _endpoint_value(document) or _endpoint_value(payload.get("endpoint"))
            if not endpoint:
                raise ToolError("openapi call requires document endpoint")
            operation_id_value = payload.get("operationId") or (
                document.get("operationId") if isinstance(document, dict) else None
            )
            if not operation_id_value:
                raise ToolError("openapi call requires operationId")
            parameters = payload.get("parameters")
            if parameters is None and isinstance(document, dict):
                parameters = document.get("parameters")
            if parameters is None:
                parameters = payload.get("body")
            return await self.http.request(
                str(payload.get("method", "POST")),
                str(endpoint),
                params=payload.get("query"),
                json=parameters,
                operation_id=operation_id or str(operation_id_value),
                follow_redirects=payload.get("redirect"),
                output=str(payload.get("output", "content")),
                request_timeout=_duration_seconds(payload.get("timeout")),
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


def _operation_id(payload: dict[str, Any]) -> str:
    value = payload.get("operation_id") or payload.get("operationId") or payload.get("id")
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


def _format_response(response: httpx.Response, output: str) -> Any:
    if output == "raw":
        return response.text
    try:
        body: Any = response.json()
    except ValueError:
        body = response.text
    if output == "response":
        return {
            "status": response.status_code,
            "headers": dict(response.headers),
            "body": body,
        }
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
            return sum(float(value.get(unit, 0)) * multiplier for unit, multiplier in units.items())
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lower()
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*(ms|s|m|h)?", text)
    if not match:
        raise ToolError(f"invalid protocol timeout: {value}")
    number = float(match.group(1))
    return {
        None: number,
        "ms": number / 1000,
        "s": number,
        "m": number * 60,
        "h": number * 3600,
    }[match.group(2)]
