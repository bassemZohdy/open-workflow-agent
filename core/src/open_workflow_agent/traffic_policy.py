"""Deployment-controlled traffic policy enforcement middleware."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from fastapi.responses import JSONResponse

from .config import (
    ConcurrencyLimitConfig,
    RateLimitConfig,
    TrafficEndpointLimitConfig,
    TrafficLimitConfig,
    TrafficPolicyConfig,
)
from .metrics import Metrics


@dataclass(frozen=True)
class _AdmissionScope:
    key: str
    rate_limit: RateLimitConfig | None
    concurrency_limit: ConcurrencyLimitConfig | None


@dataclass
class _TokenBucket:
    tokens: float
    last_refill: float


class TrafficPolicyMiddleware:
    """ASGI middleware enforcing global, endpoint, and principal limits."""

    def __init__(
        self, app: Any, *, policy: TrafficPolicyConfig, metrics: Metrics | None = None
    ) -> None:
        self.app = app
        self.policy = policy
        self.metrics = metrics
        self._active_requests = 0
        self._active_by_scope: dict[str, int] = {}
        self._buckets: dict[str, _TokenBucket] = {}
        self._lock = asyncio.Lock()

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or not self.policy.enabled:
            await self.app(scope, receive, send)
            return

        scopes = self._scopes_for(scope)
        async with self._lock:
            rejection = self._admit_locked(scopes)
            if rejection is None:
                for admission_scope in scopes:
                    if admission_scope.concurrency_limit is not None:
                        self._active_by_scope[admission_scope.key] = (
                            self._active_by_scope.get(admission_scope.key, 0) + 1
                        )
                self._active_requests = self._active_by_scope.get("global", 0)
                if self.metrics is not None:
                    self.metrics.set_traffic_active(self._active_requests)

        if rejection is not None:
            kind, limit = rejection
            if self.metrics is not None:
                self.metrics.record_traffic_rejection(kind)
            if kind == "rate_limit":
                assert isinstance(limit, RateLimitConfig)
                await self._send_rate_limit_error(scope, send, limit)
            else:
                assert isinstance(limit, ConcurrencyLimitConfig)
                await self._send_concurrency_error(scope, send, limit)
            return

        try:
            await self.app(scope, receive, send)
        finally:
            async with self._lock:
                for admission_scope in scopes:
                    if admission_scope.concurrency_limit is not None:
                        active = self._active_by_scope[admission_scope.key] - 1
                        if active:
                            self._active_by_scope[admission_scope.key] = active
                        else:
                            del self._active_by_scope[admission_scope.key]
                self._active_requests = self._active_by_scope.get("global", 0)
                if self.metrics is not None:
                    self.metrics.set_traffic_active(self._active_requests)

    def _scopes_for(self, scope: dict[str, Any]) -> list[_AdmissionScope]:
        scopes = [
            _AdmissionScope(
                key="global",
                rate_limit=self.policy.rate_limit,
                concurrency_limit=self.policy.concurrency_limit,
            )
        ]
        path = str(scope.get("path", "/"))
        endpoint = self._endpoint_limit(path)
        if endpoint is not None:
            scopes.append(self._scope_from_limit(f"endpoint:{endpoint.path_prefix}", endpoint))
        principal = str(scope.get("owa.principal") or "anonymous")
        principal_limit = next(
            (item for item in self.policy.principal_limits if item.principal == principal), None
        )
        if principal_limit is not None:
            scopes.append(self._scope_from_limit(f"principal:{principal}", principal_limit))
        return scopes

    def _endpoint_limit(self, path: str) -> TrafficEndpointLimitConfig | None:
        matches = [
            item
            for item in self.policy.endpoint_limits
            if item.path_prefix == "/"
            or path == item.path_prefix
            or path.startswith(f"{item.path_prefix}/")
        ]
        return max(matches, key=lambda item: len(item.path_prefix), default=None)

    @staticmethod
    def _scope_from_limit(key: str, limit: TrafficLimitConfig) -> _AdmissionScope:
        return _AdmissionScope(
            key=key,
            rate_limit=limit.rate_limit,
            concurrency_limit=limit.concurrency_limit,
        )

    def _admit_locked(
        self, scopes: list[_AdmissionScope]
    ) -> tuple[str, RateLimitConfig | ConcurrencyLimitConfig] | None:
        for admission_scope in scopes:
            concurrency_limit = admission_scope.concurrency_limit
            if (
                concurrency_limit is not None
                and self._active_by_scope.get(admission_scope.key, 0)
                >= concurrency_limit.max_concurrent
            ):
                return "concurrency", concurrency_limit
        now = time.monotonic()
        for admission_scope in scopes:
            rate_limit = admission_scope.rate_limit
            if rate_limit is not None and not self._has_rate_token_locked(
                admission_scope.key, rate_limit, now
            ):
                return "rate_limit", rate_limit
        for admission_scope in scopes:
            if admission_scope.rate_limit is not None:
                self._buckets[admission_scope.key].tokens -= 1.0
        return None

    def _has_rate_token_locked(self, key: str, limit: RateLimitConfig, now: float) -> bool:
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _TokenBucket(tokens=float(limit.burst), last_refill=now)
            self._buckets[key] = bucket
        else:
            elapsed = max(0.0, now - bucket.last_refill)
            bucket.tokens = min(
                float(limit.burst), bucket.tokens + elapsed * limit.requests_per_second
            )
            bucket.last_refill = now
        return bucket.tokens >= 1.0

    async def _send_rate_limit_error(
        self, scope: dict[str, Any], send: Any, limit: RateLimitConfig
    ) -> None:
        response = JSONResponse(
            status_code=429,
            content={
                "error": {
                    "code": "rate_limit_exceeded",
                    "message": "request rate limit exceeded",
                    "details": {
                        "requests_per_second": limit.requests_per_second,
                        "burst": limit.burst,
                    },
                }
            },
        )
        await response(scope, _disconnected_receive, send)

    async def _send_concurrency_error(
        self, scope: dict[str, Any], send: Any, limit: ConcurrencyLimitConfig
    ) -> None:
        response = JSONResponse(
            status_code=429,
            content={
                "error": {
                    "code": "concurrency_limit_exceeded",
                    "message": "too many concurrent requests",
                    "details": {"max_concurrent": limit.max_concurrent},
                }
            },
        )
        await response(scope, _disconnected_receive, send)


async def _disconnected_receive() -> dict[str, Any]:
    return {"type": "http.disconnect"}


def _limit_capabilities(limit: TrafficLimitConfig) -> dict[str, Any]:
    value: dict[str, Any] = {}
    if limit.rate_limit is not None:
        value["rateLimit"] = {
            "requestsPerSecond": limit.rate_limit.requests_per_second,
            "burst": limit.rate_limit.burst,
        }
    if limit.concurrency_limit is not None:
        value["concurrencyLimit"] = {"maxConcurrent": limit.concurrency_limit.max_concurrent}
    return value


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
        "concurrencyLimit": {"maxConcurrent": policy.concurrency_limit.max_concurrent},
        "endpointLimits": [
            {"pathPrefix": item.path_prefix, **_limit_capabilities(item)}
            for item in policy.endpoint_limits
        ],
        "principalLimits": {"configured": len(policy.principal_limits)},
    }
