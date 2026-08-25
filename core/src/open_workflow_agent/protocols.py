"""Common protocol clients with bounded, secure HTTP behavior."""

from __future__ import annotations

from typing import Any

import httpx

from .errors import ToolError


class HttpClient:
    def __init__(
        self, *, timeout: float = 30.0, max_response_bytes: int = 4_000_000, verify_tls: bool = True
    ) -> None:
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self.verify_tls = verify_tls

    async def request(self, method: str, endpoint: str, **kwargs: Any) -> Any:
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, verify=self.verify_tls, follow_redirects=False
            ) as client:
                response = await client.request(method, endpoint, **kwargs)
                if len(response.content) > self.max_response_bytes:
                    raise ToolError("HTTP response exceeds configured maximum size")
                response.raise_for_status()
                try:
                    return response.json()
                except ValueError:
                    return response.text
        except httpx.HTTPError as exc:
            raise ToolError(f"HTTP request failed: {exc}") from exc


class ProtocolServices:
    def __init__(self, http: HttpClient | None = None) -> None:
        self.http = http or HttpClient()

    async def call(self, protocol: str, payload: Any) -> Any:
        if not isinstance(payload, dict):
            raise ToolError(f"{protocol} payload must be an object")
        if protocol == "http":
            endpoint = payload.get("endpoint") or payload.get("url")
            if not endpoint:
                raise ToolError("http call requires endpoint")
            return await self.http.request(
                payload.get("method", "GET"), endpoint, json=payload.get("body")
            )
        endpoint = payload.get("endpoint")
        if not endpoint:
            raise ToolError(f"{protocol} call requires endpoint")
        method = payload.get("method", "POST")
        body = payload.get("body", payload)
        return await self.http.request(method, endpoint, json=body)
