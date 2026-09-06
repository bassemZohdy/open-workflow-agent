"""Deterministic concurrent-load probes for the traffic policy boundary."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.traffic_policy import TrafficPolicyMiddleware


@pytest.mark.asyncio
async def test_traffic_policy_stress_load_bounds_concurrency_and_burst() -> None:
    active = 0
    maximum_active = 0
    started = asyncio.Event()
    release = asyncio.Event()

    async def held_response(scope, receive, send):
        nonlocal active, maximum_active
        del scope, receive
        active += 1
        maximum_active = max(maximum_active, active)
        if active == 8:
            started.set()
        await release.wait()
        active -= 1
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    concurrency_policy = RuntimeConfig.model_validate(
        {
            "traffic_policy": {
                "enabled": True,
                "rate_limit": {"requests_per_second": 1000, "burst": 1000},
                "concurrency_limit": {"max_concurrent": 8},
            }
        }
    ).traffic_policy
    concurrency_middleware = TrafficPolicyMiddleware(held_response, policy=concurrency_policy)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=concurrency_middleware), base_url="http://test"
    ) as client:
        requests = [asyncio.create_task(client.get("/v1/invoke")) for _ in range(100)]
        await asyncio.wait_for(started.wait(), timeout=1)
        release.set()
        results = await asyncio.gather(*requests)

    assert maximum_active == 8
    assert sum(response.status_code == 200 for response in results) == 8
    assert sum(response.status_code == 429 for response in results) == 92

    async def immediate_response(scope, receive, send):
        del scope, receive
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    rate_policy = RuntimeConfig.model_validate(
        {
            "traffic_policy": {
                "enabled": True,
                "rate_limit": {"requests_per_second": 1, "burst": 16},
                "concurrency_limit": {"max_concurrent": 1000},
            }
        }
    ).traffic_policy
    rate_middleware = TrafficPolicyMiddleware(immediate_response, policy=rate_policy)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=rate_middleware), base_url="http://test"
    ) as client:
        results = await asyncio.gather(*(client.get("/v1/invoke") for _ in range(100)))

    assert sum(response.status_code == 200 for response in results) == 16
    assert sum(response.status_code == 429 for response in results) == 84
