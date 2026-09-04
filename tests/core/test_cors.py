"""Tests for CORS configuration support."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from open_workflow_agent.api import create_app
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.services import RuntimeServices


def test_cors_disabled_by_default() -> None:
    """Verify CORS is disabled by default (no origins configured)."""
    config = RuntimeConfig.model_validate({"model": {"provider": "fake"}})
    services = RuntimeServices(config, database_root="/tmp/test-cors")
    app = create_app(config=config, services=services)

    with TestClient(app) as client:
        # Regular request should work
        resp = client.get("/health/live")
        assert resp.status_code == 200

        # CORS preflight should not be handled (no CORS middleware)
        resp = client.options(
            "/v1/capabilities",
            headers={
                "Origin": "https://example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        # Without CORS middleware, OPTIONS returns 405 Method Not Allowed
        assert resp.status_code == 405


def test_cors_with_allowed_origins() -> None:
    """Verify CORS works with configured allowed origins."""
    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "server": {
                "cors_origins": ["https://example.com", "https://app.example.com"],
            },
        }
    )
    services = RuntimeServices(config, database_root="/tmp/test-cors")
    app = create_app(config=config, services=services)

    with TestClient(app) as client:
        # Request from allowed origin should get CORS headers
        resp = client.get(
            "/v1/capabilities",
            headers={"Origin": "https://example.com"},
        )
        assert resp.status_code == 200
        assert "access-control-allow-origin" in resp.headers
        assert resp.headers["access-control-allow-origin"] == "https://example.com"

        # Request from disallowed origin should not get CORS headers
        resp = client.get(
            "/v1/capabilities",
            headers={"Origin": "https://evil.com"},
        )
        assert resp.status_code == 200
        assert "access-control-allow-origin" not in resp.headers


def test_cors_preflight_request() -> None:
    """Verify CORS preflight requests are handled correctly."""
    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "server": {
                "cors_origins": ["https://example.com"],
                "cors_methods": ["GET", "POST"],
                "cors_headers": ["Content-Type", "Authorization"],
            },
        }
    )
    services = RuntimeServices(config, database_root="/tmp/test-cors")
    app = create_app(config=config, services=services)

    with TestClient(app) as client:
        # Preflight request should return CORS headers
        resp = client.options(
            "/v1/invoke",
            headers={
                "Origin": "https://example.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type",
            },
        )
        assert resp.status_code == 200
        assert "access-control-allow-origin" in resp.headers
        assert resp.headers["access-control-allow-origin"] == "https://example.com"
        assert "access-control-allow-methods" in resp.headers
        assert "POST" in resp.headers["access-control-allow-methods"]
        assert "access-control-allow-headers" in resp.headers


def test_cors_with_credentials() -> None:
    """Verify CORS works with credentials when configured."""
    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "server": {
                "cors_origins": ["https://example.com"],
                "cors_allow_credentials": True,
            },
        }
    )
    services = RuntimeServices(config, database_root="/tmp/test-cors")
    app = create_app(config=config, services=services)

    with TestClient(app) as client:
        resp = client.get(
            "/v1/capabilities",
            headers={"Origin": "https://example.com"},
        )
        assert resp.status_code == 200
        assert "access-control-allow-credentials" in resp.headers
        assert resp.headers["access-control-allow-credentials"] == "true"


def test_cors_wildcard_origin() -> None:
    """Verify CORS works with wildcard origin."""
    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "server": {
                "cors_origins": ["*"],
            },
        }
    )
    services = RuntimeServices(config, database_root="/tmp/test-cors")
    app = create_app(config=config, services=services)

    with TestClient(app) as client:
        resp = client.get(
            "/v1/capabilities",
            headers={"Origin": "https://any-origin.com"},
        )
        assert resp.status_code == 200
        assert "access-control-allow-origin" in resp.headers
        assert resp.headers["access-control-allow-origin"] == "*"


def test_cors_configuration_via_environment(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify CORS can be configured via environment variables."""
    import yaml

    # Create a config file with CORS origins
    config_path = tmp_path / "agent.yaml"
    config_path.write_text(
        yaml.dump(
            {
                "model": {"provider": "fake"},
                "server": {
                    "cors_origins": ["https://env.example.com"],
                },
            }
        ),
        encoding="utf-8",
    )

    config = RuntimeConfig.from_file(config_path)
    services = RuntimeServices(config, database_root=str(tmp_path / "data"))
    app = create_app(config=config, services=services)

    with TestClient(app) as client:
        resp = client.get(
            "/v1/capabilities",
            headers={"Origin": "https://env.example.com"},
        )
        assert resp.status_code == 200
        assert "access-control-allow-origin" in resp.headers
