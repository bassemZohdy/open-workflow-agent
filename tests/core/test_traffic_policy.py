"""Tests for the deployment-controlled traffic policy."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from open_workflow_agent.config import RuntimeConfig


def _test_config(tmp_path: Path, overrides: dict[str, object] | None = None) -> RuntimeConfig:
    data = dict(overrides or {})
    for section, filename in (
        ("knowledge", "knowledge.sqlite3"),
        ("memory", "memory.sqlite3"),
        ("persistence", "runtime.sqlite3"),
    ):
        section_value = data.setdefault(section, {})
        if not isinstance(section_value, dict):
            raise TypeError(f"{section} configuration must be an object")
        section_value.setdefault("database", str(tmp_path / filename))
    return RuntimeConfig.model_validate(data)


def test_traffic_policy_defaults_disabled() -> None:
    config = RuntimeConfig()
    assert config.traffic_policy.enabled is False
    assert config.traffic_policy.rate_limit.requests_per_second == 10.0
    assert config.traffic_policy.rate_limit.burst == 20
    assert config.traffic_policy.concurrency_limit.max_concurrent == 50
    assert config.traffic_policy.endpoint_limits == []
    assert config.traffic_policy.principal_limits == []


def test_traffic_policy_from_yaml(tmp_path: Path) -> None:
    path = tmp_path / "agent.yaml"
    path.write_text(
        "traffic_policy:\n"
        "  enabled: true\n"
        "  rate_limit:\n"
        "    requests_per_second: 5.0\n"
        "    burst: 10\n"
        "  concurrency_limit:\n"
        "    max_concurrent: 25\n"
        "  endpoint_limits:\n"
        "    - path_prefix: /v1/invoke\n"
        "      concurrency_limit:\n"
        "        max_concurrent: 3\n"
        "  principal_limits:\n"
        "    - principal: partner-client\n"
        "      rate_limit:\n"
        "        requests_per_second: 2\n"
        "        burst: 4\n",
        encoding="utf-8",
    )
    config = RuntimeConfig.from_file(path)
    assert config.traffic_policy.enabled is True
    assert config.traffic_policy.rate_limit.requests_per_second == 5.0
    assert config.traffic_policy.rate_limit.burst == 10
    assert config.traffic_policy.concurrency_limit.max_concurrent == 25
    assert config.traffic_policy.endpoint_limits[0].path_prefix == "/v1/invoke"
    assert config.traffic_policy.endpoint_limits[0].concurrency_limit is not None
    assert config.traffic_policy.endpoint_limits[0].concurrency_limit.max_concurrent == 3
    assert config.traffic_policy.principal_limits[0].principal == "partner-client"


def test_traffic_policy_from_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "agent.yaml"
    path.write_text("model:\n  provider: fake\n", encoding="utf-8")
    monkeypatch.setenv("OWA__TRAFFIC_POLICY__ENABLED", "true")
    monkeypatch.setenv("OWA__TRAFFIC_POLICY__RATE_LIMIT__REQUESTS_PER_SECOND", "3.0")
    monkeypatch.setenv("OWA__TRAFFIC_POLICY__RATE_LIMIT__BURST", "5")
    monkeypatch.setenv("OWA__TRAFFIC_POLICY__CONCURRENCY_LIMIT__MAX_CONCURRENT", "10")
    config = RuntimeConfig.from_file(path)
    assert config.traffic_policy.enabled is True
    assert config.traffic_policy.rate_limit.requests_per_second == 3.0
    assert config.traffic_policy.rate_limit.burst == 5
    assert config.traffic_policy.concurrency_limit.max_concurrent == 10


def test_traffic_policy_rejects_invalid_rate_limit() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        RuntimeConfig.model_validate({"traffic_policy": {"rate_limit": {"requests_per_second": 0}}})

    with pytest.raises(ValidationError):
        RuntimeConfig.model_validate({"traffic_policy": {"rate_limit": {"burst": 0}}})


def test_traffic_policy_rejects_invalid_concurrency_limit() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        RuntimeConfig.model_validate(
            {"traffic_policy": {"concurrency_limit": {"max_concurrent": 0}}}
        )


def test_traffic_policy_rejects_empty_and_duplicate_scoped_limits() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        RuntimeConfig.model_validate(
            {"traffic_policy": {"endpoint_limits": [{"path_prefix": "/v1"}]}}
        )

    with pytest.raises(ValidationError):
        RuntimeConfig.model_validate(
            {
                "traffic_policy": {
                    "endpoint_limits": [
                        {"path_prefix": "/v1", "rate_limit": {"burst": 1}},
                        {"path_prefix": "/v1", "rate_limit": {"burst": 2}},
                    ]
                }
            }
        )

    with pytest.raises(ValidationError):
        RuntimeConfig.model_validate(
            {
                "traffic_policy": {
                    "principal_limits": [
                        {"principal": "partner", "rate_limit": {"burst": 1}},
                        {"principal": "partner", "rate_limit": {"burst": 2}},
                    ]
                }
            }
        )


def test_traffic_policy_unknown_keys_rejected(tmp_path: Path) -> None:
    from open_workflow_agent.errors import ConfigurationError

    path = tmp_path / "agent.yaml"
    path.write_text("traffic_policy:\n  enabled: true\n  unknown_key: value\n", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        RuntimeConfig.from_file(path)


def test_traffic_policy_disabled_does_not_add_middleware(tmp_path: Path) -> None:
    from open_workflow_agent.api import create_app

    config = _test_config(tmp_path)
    assert config.traffic_policy.enabled is False
    app = create_app(config=config)
    # Verify the middleware stack size doesn't include traffic policy
    # When disabled, request-size, security-header, and metrics middleware are added.
    assert len(app.user_middleware) == 3


def test_traffic_policy_enabled_adds_middleware(tmp_path: Path) -> None:
    from open_workflow_agent.api import create_app

    config = _test_config(tmp_path, {"traffic_policy": {"enabled": True}})
    app = create_app(config=config)
    # When enabled, all four request middleware layers are added.
    assert len(app.user_middleware) == 4


def test_traffic_policy_capabilities_disabled() -> None:
    from open_workflow_agent.config import TrafficPolicyConfig
    from open_workflow_agent.traffic_policy import traffic_policy_capabilities

    policy = TrafficPolicyConfig()
    caps = traffic_policy_capabilities(policy)
    assert caps == {"enabled": False}


def test_traffic_policy_capabilities_enabled() -> None:
    from open_workflow_agent.config import TrafficPolicyConfig
    from open_workflow_agent.traffic_policy import traffic_policy_capabilities

    policy = TrafficPolicyConfig.model_validate(
        {
            "enabled": True,
            "rate_limit": {"requests_per_second": 5.0, "burst": 10},
            "concurrency_limit": {"max_concurrent": 25},
        }
    )
    caps = traffic_policy_capabilities(policy)
    assert caps == {
        "enabled": True,
        "rateLimit": {"requestsPerSecond": 5.0, "burst": 10},
        "concurrencyLimit": {"maxConcurrent": 25},
        "endpointLimits": [],
        "principalLimits": {"configured": 0},
    }


def test_traffic_policy_endpoint_limit_is_additional_and_path_scoped(tmp_path: Path) -> None:
    from open_workflow_agent.api import create_app

    config = _test_config(
        tmp_path,
        {
            "model": {"provider": "fake"},
            "traffic_policy": {
                "enabled": True,
                "rate_limit": {"requests_per_second": 1000, "burst": 1000},
                "concurrency_limit": {"max_concurrent": 100},
                "endpoint_limits": [
                    {
                        "path_prefix": "/v1",
                        "rate_limit": {"requests_per_second": 0.1, "burst": 1},
                    }
                ],
            },
        },
    )
    app = create_app(config=config)
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/v1/capabilities").status_code == 200
        assert client.get("/v1/capabilities").status_code == 429
        assert client.get("/health/live").status_code == 200


def test_traffic_policy_principal_limit_uses_authenticated_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from open_workflow_agent.api import create_app

    monkeypatch.setenv("TRAFFIC_POLICY_TOKEN", "traffic-token")
    config = _test_config(
        tmp_path,
        {
            "model": {"provider": "fake"},
            "server": {"api_security_profile": "partner-auth"},
            "security": {
                "profiles": {
                    "partner-auth": {
                        "type": "bearer",
                        "principal": "partner-client",
                        "token": {"from_env": "TRAFFIC_POLICY_TOKEN"},
                    }
                }
            },
            "traffic_policy": {
                "enabled": True,
                "rate_limit": {"requests_per_second": 1000, "burst": 1000},
                "concurrency_limit": {"max_concurrent": 100},
                "principal_limits": [
                    {
                        "principal": "partner-client",
                        "rate_limit": {"requests_per_second": 0.1, "burst": 1},
                    }
                ],
            },
        },
    )
    app = create_app(config=config)
    headers = {"Authorization": "Bearer traffic-token"}
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/v1/capabilities", headers=headers).status_code == 200
        assert client.get("/v1/capabilities", headers=headers).status_code == 429
        assert client.get("/health/live").status_code == 200


def test_traffic_policy_principal_limit_uses_a2a_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from open_workflow_agent.api import create_app

    monkeypatch.setenv("A2A_TRAFFIC_POLICY_TOKEN", "a2a-traffic-token")
    config = _test_config(
        tmp_path,
        {
            "model": {"provider": "fake"},
            "a2a": {"enabled": True, "security_profile": "a2a-auth"},
            "security": {
                "profiles": {
                    "a2a-auth": {
                        "type": "bearer",
                        "principal": "a2a-client",
                        "token": {"from_env": "A2A_TRAFFIC_POLICY_TOKEN"},
                    }
                }
            },
            "traffic_policy": {
                "enabled": True,
                "rate_limit": {"requests_per_second": 1000, "burst": 1000},
                "concurrency_limit": {"max_concurrent": 100},
                "principal_limits": [
                    {
                        "principal": "a2a-client",
                        "rate_limit": {"requests_per_second": 0.1, "burst": 1},
                    }
                ],
            },
        },
    )
    app = create_app(config=config)
    headers = {"Authorization": "Bearer a2a-traffic-token"}
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/.well-known/agent-card.json", headers=headers).status_code == 200
        assert client.get("/.well-known/agent-card.json", headers=headers).status_code == 429


def test_traffic_policy_rate_limit_enforcement(tmp_path: Path) -> None:
    from open_workflow_agent.api import create_app

    config = _test_config(
        tmp_path,
        {
            "model": {"provider": "fake"},
            "traffic_policy": {
                "enabled": True,
                "rate_limit": {"requests_per_second": 0.1, "burst": 2},
                "concurrency_limit": {"max_concurrent": 100},
            },
        },
    )
    app = create_app(config=config)
    client = TestClient(app, raise_server_exceptions=False)

    # First two requests should succeed (within burst)
    resp1 = client.get("/health/live")
    assert resp1.status_code == 200
    resp2 = client.get("/health/live")
    assert resp2.status_code == 200

    # Third request should be rate limited
    resp3 = client.get("/health/live")
    assert resp3.status_code == 429
    error = resp3.json()["error"]
    assert error["code"] == "rate_limit_exceeded"
    assert "requests_per_second" in error["details"]


def test_traffic_policy_concurrency_limit_enforcement(tmp_path: Path) -> None:
    from open_workflow_agent.api import create_app

    config = _test_config(
        tmp_path,
        {
            "model": {"provider": "fake"},
            "traffic_policy": {
                "enabled": True,
                "rate_limit": {"requests_per_second": 1000, "burst": 1000},
                "concurrency_limit": {"max_concurrent": 1},
            },
        },
    )
    app = create_app(config=config)

    # Need to manually test ASGI middleware since TestClient is synchronous
    from starlette.testclient import TestClient as StarletteTestClient

    client = StarletteTestClient(app, raise_server_exceptions=False)

    # First request should succeed
    resp1 = client.get("/health/live")
    assert resp1.status_code == 200


@pytest.mark.asyncio
async def test_traffic_policy_bounds_concurrent_load() -> None:
    from open_workflow_agent.config import RuntimeConfig
    from open_workflow_agent.traffic_policy import TrafficPolicyMiddleware

    started = asyncio.Event()
    release = asyncio.Event()
    active = 0
    maximum_active = 0

    async def downstream(scope, receive, send):
        nonlocal active, maximum_active
        del receive
        active += 1
        maximum_active = max(maximum_active, active)
        if active == 2:
            started.set()
        await release.wait()
        active -= 1
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    policy = RuntimeConfig.model_validate(
        {
            "traffic_policy": {
                "enabled": True,
                "rate_limit": {"requests_per_second": 1000, "burst": 1000},
                "concurrency_limit": {"max_concurrent": 2},
            }
        }
    ).traffic_policy
    middleware = TrafficPolicyMiddleware(downstream, policy=policy)

    import httpx

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=middleware), base_url="http://test"
    ) as client:
        first = [asyncio.create_task(client.get("/health/live")) for _ in range(2)]
        await asyncio.wait_for(started.wait(), timeout=1)
        overflow = [asyncio.create_task(client.get("/health/live")) for _ in range(8)]
        overflow_results = await asyncio.gather(*overflow)
        assert [response.status_code for response in overflow_results] == [429] * 8
        release.set()
        first_results = await asyncio.gather(*first)

    assert [response.status_code for response in first_results] == [200, 200]
    assert maximum_active == 2
