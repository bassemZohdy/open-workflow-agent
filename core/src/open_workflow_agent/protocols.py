"""Common protocol clients with bounded, secure HTTP behavior."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any, Protocol
from urllib.parse import urlparse
from uuid import uuid4

import httpx

from .errors import ToolError


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
        **kwargs: Any,
    ) -> Any:
        self._check_endpoint(endpoint)
        headers = dict(kwargs.pop("headers", {}) or {})
        if self.authentication:
            headers = {**self.authentication.headers(endpoint), **headers}
        if operation_id:
            headers.setdefault("X-OWA-Operation-ID", operation_id)
            headers.setdefault("Idempotency-Key", operation_id)
        if headers:
            kwargs["headers"] = headers
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                verify=self.verify_tls,
                follow_redirects=(
                    self.follow_redirects if follow_redirects is None else follow_redirects
                ),
                transport=self.transport,
            ) as client:
                response = await client.request(method, endpoint, **kwargs)
                if len(response.content) > self.max_response_bytes:
                    raise ToolError("HTTP response exceeds configured maximum size")
                response.raise_for_status()
                try:
                    return response.json()
                except ValueError:
                    return response.text
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
        if not isinstance(payload, dict):
            raise ToolError(f"{protocol} payload must be an object")
        operation_id = _operation_id(payload)
        if protocol == "http":
            endpoint = payload.get("endpoint") or payload.get("url")
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
            )
        if protocol == "mcp":
            transport = payload.get("transport", {})
            http_transport = transport.get("http", {}) if isinstance(transport, dict) else {}
            endpoint = payload.get("endpoint") or http_transport.get("endpoint")
            if not endpoint:
                raise ToolError("mcp call requires transport.http.endpoint")
            method = payload.get("method", "tools/call")
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
            return await self.http.request(
                "POST",
                str(endpoint),
                headers=http_transport.get("headers"),
                json=body,
                operation_id=operation_id,
            )
        if protocol == "a2a":
            endpoint = payload.get("endpoint") or payload.get("server")
            if not endpoint:
                raise ToolError("a2a call requires server endpoint")
            body = {
                "jsonrpc": "2.0",
                "id": payload.get("id", operation_id or str(uuid4())),
                "method": payload.get("method", "message/send"),
                "params": payload.get(
                    "parameters", payload.get("params", payload.get("message", {}))
                ),
            }
            return await self.http.request(
                "POST", str(endpoint), json=body, operation_id=operation_id
            )
        if protocol == "openapi":
            document = payload.get("document")
            endpoint = (
                document.get("endpoint") if isinstance(document, dict) else None
            ) or payload.get("endpoint")
            if not endpoint:
                raise ToolError("openapi call requires document endpoint")
            parameters = payload.get("parameters", payload.get("body"))
            return await self.http.request(
                str(payload.get("method", "POST")),
                str(endpoint),
                params=payload.get("query"),
                json=parameters,
                operation_id=operation_id or payload.get("operationId"),
                follow_redirects=payload.get("redirect"),
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
