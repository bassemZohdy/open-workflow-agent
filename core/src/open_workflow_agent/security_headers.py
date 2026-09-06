"""Security response headers middleware for the Open Workflow Agent API."""

from __future__ import annotations

from typing import Any

from .config import SecurityHeadersConfig


class SecurityHeadersMiddleware:
    """Add configured security headers to HTTP responses.

    HSTS is emitted only when the ASGI server reports an HTTPS request. This
    avoids telling clients to use HTTPS when the application is serving plain
    HTTP, while still supporting TLS termination in front of the application
    when the proxy forwards the correct ASGI scheme.
    """

    def __init__(self, app: Any, *, config: SecurityHeadersConfig) -> None:
        self.app = app
        self.config = config

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                response_headers = list(message.get("headers", []))
                existing = {key.lower() for key, _ in response_headers}
                configured = {
                    b"x-content-type-options": self.config.content_type_options.encode("latin-1"),
                    b"x-frame-options": self.config.frame_options.encode("latin-1"),
                    b"content-security-policy": self.config.content_security_policy.encode(
                        "latin-1"
                    ),
                }
                if scope.get("scheme") == "https" and self.config.hsts_enabled:
                    hsts = f"max-age={self.config.hsts_max_age_seconds}"
                    if self.config.hsts_include_subdomains:
                        hsts += "; includeSubDomains"
                    if self.config.hsts_preload:
                        hsts += "; preload"
                    configured[b"strict-transport-security"] = hsts.encode("latin-1")

                for key, value in configured.items():
                    if key not in existing:
                        response_headers.append((key, value))

                message = {**message, "headers": response_headers}

            await send(message)

        await self.app(scope, receive, send_with_headers)
