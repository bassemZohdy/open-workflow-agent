"""Deployment-controlled traffic policy enforcement middleware."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi.responses import JSONResponse

from .config import TrafficPolicyConfig


class TrafficPolicyMiddleware:
    """ASGI middleware enforcing rate limits and concurrency limits."""

    def __init__(self, app: Any, *, policy: TrafficPolicyConfig) -> None:
        self.app = app
        self.policy = policy
        self._active_requests: int = 0
        self._lock = asyncio.Lock()
        self._tokens: float = float(policy.rate_limit.burst)
        self._last_refill: float = time.monotonic()
        self._rate_limit_enabled = policy.enabled and policy.rate_limit.requests_per_second > 0
        self._concurrency_enabled = policy.enabled and policy.concurrency_limit.max_concurrent > 0

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        # Check concurrency limit
        if self._concurrency_enabled:
            async with self._lock:
                if self._active_requests >= self.policy.concurrency_limit.max_concurrent:
                    await self._send_concurrency_error(scope, send)
                    return
                self._active_requests += 1

        try:
            # Check rate limit
            if self._rate_limit_enabled:
                allowed = await self._check_rate_limit()
                if not allowed:
                    await self._send_rate_limit_error(scope, send)
                    return

            await self.app(scope, receive, send)
        finally:
            if self._concurrency_enabled:
                async with self._lock:
                    self._active_requests -= 1

    async def _check_rate_limit(self) -> bool:
        """Check if request is within rate limit using token bucket algorithm."""
        now = time.monotonic()
        rate = self.policy.rate_limit.requests_per_second
        burst = self.policy.rate_limit.burst

        async with self._lock:
            # Refill tokens based on elapsed time
            elapsed = now - self._last_refill
            self._tokens = min(float(burst), self._tokens + elapsed * rate)
            self._last_refill = now

            # Check if we have tokens available
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True

            return False

    async def _send_rate_limit_error(self, scope: dict[str, Any], send: Any) -> None:
        response = JSONResponse(
            status_code=429,
            content={
                "error": {
                    "code": "rate_limit_exceeded",
                    "message": "request rate limit exceeded",
                    "details": {
                        "requests_per_second": self.policy.rate_limit.requests_per_second,
                        "burst": self.policy.rate_limit.burst,
                    },
                }
            },
        )

        async def receive() -> dict[str, Any]:
            return {"type": "http.disconnect"}

        await response(scope, receive, send)

    async def _send_concurrency_error(self, scope: dict[str, Any], send: Any) -> None:
        response = JSONResponse(
            status_code=429,
            content={
                "error": {
                    "code": "concurrency_limit_exceeded",
                    "message": "too many concurrent requests",
                    "details": {
                        "max_concurrent": self.policy.concurrency_limit.max_concurrent,
                    },
                }
            },
        )

        async def receive() -> dict[str, Any]:
            return {"type": "http.disconnect"}

        await response(scope, receive, send)


def traffic_policy_capabilities(policy: TrafficPolicyConfig) -> dict[str, Any]:
    """Return traffic policy capabilities for the capabilities endpoint."""
    if not policy.enabled:
        return {"enabled": False}
    return {
        "enabled": True,
        "rateLimit": {
            "requestsPerSecond": policy.rate_limit.requests_per_second,
            "burst": policy.rate_limit.burst,
        },
        "concurrencyLimit": {
            "maxConcurrent": policy.concurrency_limit.max_concurrent,
        },
    }
