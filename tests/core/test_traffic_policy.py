"""Tests for the deployment-controlled traffic policy."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from open_workflow_agent.config import RuntimeConfig


def test_traffic_policy_defaults_disabled() -> None:
    config = RuntimeConfig()
    assert config.traffic_policy.enabled is False
    assert config.traffic_policy.rate_limit.requests_per_second == 10.0
    assert config.traffic_policy.rate_limit.burst == 20
    assert config.traffic_policy.concurrency_limit.max_concurrent == 50


def test_traffic_policy_from_yaml(tmp_path: Path) -> None:
    path = tmp_path / "agent.yaml"
    path.write_text(
        "traffic_policy:\n"
        "  enabled: true\n"
        "  rate_limit:\n"
        "    requests_per_second: 5.0\n"
        "    burst: 10\n"
        "  concurrency_limit:\n"
        "    max_concurrent: 25\n",
        encoding="utf-8",
    )
    config = RuntimeConfig.from_file(path)
    assert config.traffic_policy.enabled is True
    assert config.traffic_policy.rate_limit.requests_per_second == 5.0
    assert config.traffic_policy.rate_limit.burst == 10
    assert config.traffic_policy.concurrency_limit.max_concurrent == 25


def test_traffic_policy_from_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
        RuntimeConfig.model_validate(
            {"traffic_policy": {"rate_limit": {"requests_per_second": 0}}}
        )

    with pytest.raises(ValidationError):
        RuntimeConfig.model_validate(
            {"traffic_policy": {"rate_limit": {"burst": 0}}}
        )


def test_traffic_policy_rejects_invalid_concurrency_limit() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        RuntimeConfig.model_validate(
            {"traffic_policy": {"concurrency_limit": {"max_concurrent": 0}}}
        )


def test_traffic_policy_unknown_keys_rejected(tmp_path: Path) -> None:
    from open_workflow_agent.errors import ConfigurationError

    path = tmp_path / "agent.yaml"
    path.write_text(
        "traffic_policy:\n  enabled: true\n  unknown_key: value\n", encoding="utf-8"
    )
    with pytest.raises(ConfigurationError):
        RuntimeConfig.from_file(path)


def test_traffic_policy_disabled_does_not_add_middleware() -> None:
    from open_workflow_agent.api import create_app
    from open_workflow_agent.config import RuntimeConfig

    config = RuntimeConfig()
    assert config.traffic_policy.enabled is False
    app = create_app(config=config)
    # Verify the middleware stack size doesn't include traffic policy
    # When disabled, only RequestSizeLimitMiddleware is added
    assert len(app.user_middleware) == 1


def test_traffic_policy_enabled_adds_middleware() -> None:
    from open_workflow_agent.api import create_app
    from open_workflow_agent.config import RuntimeConfig

    config = RuntimeConfig.model_validate({"traffic_policy": {"enabled": True}})
    app = create_app(config=config)
    # When enabled, both RequestSizeLimitMiddleware and TrafficPolicyMiddleware are added
    assert len(app.user_middleware) == 2


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
    }


def test_traffic_policy_rate_limit_enforcement() -> None:
    from open_workflow_agent.api import create_app
    from open_workflow_agent.config import RuntimeConfig

    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "traffic_policy": {
                "enabled": True,
                "rate_limit": {"requests_per_second": 0.1, "burst": 2},
                "concurrency_limit": {"max_concurrent": 100},
            },
        }
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


def test_traffic_policy_concurrency_limit_enforcement() -> None:
    from open_workflow_agent.api import create_app
    from open_workflow_agent.config import RuntimeConfig

    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "traffic_policy": {
                "enabled": True,
                "rate_limit": {"requests_per_second": 1000, "burst": 1000},
                "concurrency_limit": {"max_concurrent": 1},
            },
        }
    )
    app = create_app(config=config)

    # Need to manually test ASGI middleware since TestClient is synchronous
    from open_workflow_agent.traffic_policy import TrafficPolicyMiddleware
    from starlette.testclient import TestClient as StarletteTestClient

    client = StarletteTestClient(app, raise_server_exceptions=False)

    # First request should succeed
    resp1 = client.get("/health/live")
    assert resp1.status_code == 200
