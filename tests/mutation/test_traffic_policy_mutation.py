"""Focused mutation-test contract for the traffic-policy middleware."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.metrics import Metrics
from open_workflow_agent.traffic_policy import (
    TrafficPolicyMiddleware,
    traffic_policy_capabilities,
)


async def _ok_response(scope, receive, send):
    del scope, receive
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def _policy(payload: dict[str, object]):
    return RuntimeConfig.model_validate(
        {"traffic_policy": {"enabled": True, **payload}}
    ).traffic_policy


@pytest.mark.asyncio
async def test_rate_limit_rejects_after_the_configured_burst() -> None:
    middleware = TrafficPolicyMiddleware(
        _ok_response,
        policy=_policy(
            {
                "rate_limit": {"requests_per_second": 0.1, "burst": 1},
                "concurrency_limit": {"max_concurrent": 100},
            }
        ),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=middleware), base_url="http://test"
    ) as client:
        first = await client.get("/v1/invoke")
        second = await client.get("/v1/invoke")

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "rate_limit_exceeded"
    assert second.json()["error"]["message"] == "request rate limit exceeded"
    assert second.json()["error"]["details"]["requests_per_second"] == 0.1
    assert second.json()["error"]["details"]["burst"] == 1
    assert middleware._active_requests == 0


@pytest.mark.asyncio
async def test_disabled_and_non_http_requests_bypass_policy() -> None:
    calls: list[str] = []
    marker = object()
    receives_marker: list[bool] = []

    async def downstream(scope, receive, send):
        calls.append(scope["type"])
        receives_marker.append(receive is marker)
        await _ok_response(scope, receive, send)

    middleware = TrafficPolicyMiddleware(
        downstream,
        policy=_policy({"enabled": False}),
    )
    messages: list[dict[str, object]] = []
    statuses: list[int] = []

    async def send(message: dict[str, object]) -> None:
        messages.append(message)
        if message.get("type") == "http.response.start":
            statuses.append(int(message["status"]))

    await middleware({"type": "http", "path": "/v1/invoke"}, marker, send)
    await middleware({"type": "websocket", "path": "/v1/invoke"}, marker, send)

    assert calls == ["http", "websocket"]
    assert receives_marker == [True, True]
    assert statuses == [200, 200]

    enabled = TrafficPolicyMiddleware(
        downstream,
        policy=_policy(
            {
                "rate_limit": {"requests_per_second": 0.1, "burst": 1},
                "concurrency_limit": {"max_concurrent": 100},
            }
        ),
    )
    await enabled({"type": "http", "path": "/v1/invoke"}, marker, send)
    await enabled({"type": "websocket", "path": "/v1/invoke"}, marker, send)
    assert statuses[-1] == 200


def test_endpoint_selection_uses_longest_matching_path_boundary() -> None:
    middleware = TrafficPolicyMiddleware(
        _ok_response,
        policy=_policy(
            {
                "endpoint_limits": [
                    {"path_prefix": "/v1", "rate_limit": {"burst": 2}},
                    {
                        "path_prefix": "/v1/invoke",
                        "concurrency_limit": {"max_concurrent": 3},
                    },
                ]
            }
        ),
    )

    scopes = middleware._scopes_for({"type": "http", "path": "/v1/invoke/job"})

    assert [scope.key for scope in scopes] == ["global", "endpoint:/v1/invoke"]
    assert middleware._endpoint_limit("/v10") is None
    assert [scope.key for scope in middleware._scopes_for({"type": "http"})] == ["global"]


@pytest.mark.asyncio
async def test_endpoint_and_principal_scopes_are_additional_limits() -> None:
    policy = _policy(
        {
            "rate_limit": {"requests_per_second": 1000, "burst": 1000},
            "concurrency_limit": {"max_concurrent": 100},
            "endpoint_limits": [
                {"path_prefix": "/v1", "rate_limit": {"requests_per_second": 0.1, "burst": 1}}
            ],
            "principal_limits": [
                {
                    "principal": "partner",
                    "rate_limit": {"requests_per_second": 0.1, "burst": 1},
                }
            ],
        }
    )

    async def partner_request(scope, receive, send):
        if scope.get("path", "").startswith("/v1"):
            scope["owa.principal"] = "partner"
        await middleware(scope, receive, send)

    middleware = TrafficPolicyMiddleware(_ok_response, policy=policy)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=partner_request), base_url="http://test"
    ) as client:
        first = await client.get("/v1/invoke")
        second = await client.get("/v1/invoke")
        other_path = await client.get("/health/live")

    assert first.status_code == 200
    assert second.status_code == 429
    assert other_path.status_code == 200


@pytest.mark.asyncio
async def test_principal_concurrency_limit_is_additional_and_releases_slots() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    policy = _policy(
        {
            "rate_limit": {"requests_per_second": 1000, "burst": 1000},
            "concurrency_limit": {"max_concurrent": 10},
            "principal_limits": [
                {"principal": "partner", "concurrency_limit": {"max_concurrent": 1}}
            ],
        }
    )

    async def held_response(scope, receive, send):
        started.set()
        await release.wait()
        await _ok_response(scope, receive, send)

    middleware = TrafficPolicyMiddleware(held_response, policy=policy)

    async def partner_request(scope, receive, send):
        scope["owa.principal"] = "partner"
        await middleware(scope, receive, send)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=partner_request), base_url="http://test"
    ) as client:
        first_task = asyncio.create_task(client.get("/v1/invoke"))
        await asyncio.wait_for(started.wait(), timeout=1)
        overflow = await client.get("/v1/invoke")
        assert middleware._active_by_scope == {"global": 1, "principal:partner": 1}
        release.set()
        first = await first_task

    assert overflow.status_code == 429
    assert overflow.json()["error"]["code"] == "concurrency_limit_exceeded"
    assert overflow.json()["error"]["message"] == "too many concurrent requests"
    assert overflow.json()["error"]["details"]["max_concurrent"] == 1
    assert first.status_code == 200
    assert middleware._active_by_scope == {}


@pytest.mark.asyncio
async def test_downstream_failure_releases_concurrency_slot() -> None:
    attempts = 0

    async def fail_once(scope, receive, send):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("controlled downstream failure")
        await _ok_response(scope, receive, send)

    middleware = TrafficPolicyMiddleware(
        fail_once,
        policy=_policy(
            {
                "rate_limit": {"requests_per_second": 1000, "burst": 1000},
                "concurrency_limit": {"max_concurrent": 1},
            }
        ),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=middleware), base_url="http://test"
    ) as client:
        with pytest.raises(RuntimeError, match="controlled downstream failure"):
            await client.get("/v1/invoke")
        recovered = await client.get("/v1/invoke")

    assert recovered.status_code == 200


def test_rate_bucket_refills_after_elapsed_time(monkeypatch: pytest.MonkeyPatch) -> None:
    middleware = TrafficPolicyMiddleware(
        _ok_response,
        policy=_policy(
            {
                "rate_limit": {"requests_per_second": 2, "burst": 1},
                "concurrency_limit": {"max_concurrent": 10},
            }
        ),
    )
    now = [100.0]
    monkeypatch.setattr("open_workflow_agent.traffic_policy.time.monotonic", lambda: now[0])
    scope = middleware._scopes_for({"type": "http", "path": "/v1/invoke"})

    assert middleware._admit_locked(scope) is None
    assert middleware._admit_locked(scope)[0] == "rate_limit"
    now[0] = 100.5
    assert middleware._admit_locked(scope) is None


def test_admission_reports_concurrency_rejection_kind() -> None:
    middleware = TrafficPolicyMiddleware(
        _ok_response,
        policy=_policy({"concurrency_limit": {"max_concurrent": 1}}),
    )
    scopes = middleware._scopes_for({"type": "http", "path": "/v1/invoke"})
    middleware._active_by_scope["global"] = 1

    rejection = middleware._admit_locked(scopes)

    assert rejection == ("concurrency", middleware.policy.concurrency_limit)


@pytest.mark.asyncio
async def test_concurrency_limit_rejects_overflow_and_releases_slots() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def held_response(scope, receive, send):
        started.set()
        await release.wait()
        await _ok_response(scope, receive, send)

    middleware = TrafficPolicyMiddleware(
        held_response,
        policy=_policy(
            {
                "rate_limit": {"requests_per_second": 1000, "burst": 1000},
                "concurrency_limit": {"max_concurrent": 1},
            }
        ),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=middleware), base_url="http://test"
    ) as client:
        first_task = asyncio.create_task(client.get("/v1/invoke"))
        await asyncio.wait_for(started.wait(), timeout=1)
        overflow = await client.get("/v1/invoke")
        release.set()
        first = await first_task

    assert overflow.status_code == 429
    assert first.status_code == 200


def test_capabilities_advertise_scoped_limits() -> None:
    policy = _policy(
        {
            "endpoint_limits": [{"path_prefix": "/v1", "concurrency_limit": {"max_concurrent": 3}}],
            "principal_limits": [{"principal": "partner", "rate_limit": {"burst": 2}}],
        }
    )
    capabilities = traffic_policy_capabilities(policy)

    assert capabilities == {
        "enabled": True,
        "rateLimit": {"requestsPerSecond": 10.0, "burst": 20},
        "concurrencyLimit": {"maxConcurrent": 50},
        "endpointLimits": [{"pathPrefix": "/v1", "concurrencyLimit": {"maxConcurrent": 3}}],
        "principalLimits": {"configured": 1},
    }


def test_disabled_capabilities_keep_the_enabled_key() -> None:
    assert traffic_policy_capabilities(_policy({"enabled": False})) == {"enabled": False}


def test_scoped_capabilities_include_rate_and_concurrency_values() -> None:
    policy = _policy(
        {
            "endpoint_limits": [
                {
                    "path_prefix": "/v1",
                    "rate_limit": {"requests_per_second": 2, "burst": 4},
                    "concurrency_limit": {"max_concurrent": 3},
                }
            ],
            "principal_limits": [
                {
                    "principal": "partner",
                    "rate_limit": {"requests_per_second": 5, "burst": 6},
                    "concurrency_limit": {"max_concurrent": 7},
                }
            ],
        }
    )

    capabilities = traffic_policy_capabilities(policy)

    assert capabilities["endpointLimits"] == [
        {
            "pathPrefix": "/v1",
            "rateLimit": {"requestsPerSecond": 2.0, "burst": 4},
            "concurrencyLimit": {"maxConcurrent": 3},
        }
    ]


@pytest.mark.asyncio
async def test_metrics_observe_active_requests_and_rejections() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    metrics = Metrics()

    async def held_response(scope, receive, send):
        started.set()
        await release.wait()
        await _ok_response(scope, receive, send)

    middleware = TrafficPolicyMiddleware(
        held_response,
        policy=_policy(
            {
                "rate_limit": {"requests_per_second": 1000, "burst": 1000},
                "concurrency_limit": {"max_concurrent": 1},
            }
        ),
        metrics=metrics,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=middleware), base_url="http://test"
    ) as client:
        first_task = asyncio.create_task(client.get("/v1/invoke"))
        await asyncio.wait_for(started.wait(), timeout=1)
        assert "owa_traffic_policy_active_requests 1.0" in metrics.render()
        overflow = await client.get("/v1/invoke")
        release.set()
        await first_task

    assert overflow.status_code == 429
    rendered = metrics.render()
    assert 'owa_traffic_policy_rejections_total{reason="concurrency"} 1.0' in rendered
    assert "owa_traffic_policy_active_requests 0.0" in rendered
