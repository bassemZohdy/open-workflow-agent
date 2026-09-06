"""Tests for security response headers middleware."""

from __future__ import annotations

from fastapi.testclient import TestClient
from open_workflow_agent.api import create_app
from open_workflow_agent.config import RuntimeConfig, SecurityHeadersConfig
from open_workflow_agent.services import RuntimeServices


def test_security_headers_enabled_by_default() -> None:
    """Verify security headers are added when enabled by default."""
    config = RuntimeConfig.model_validate({"model": {"provider": "fake"}})
    services = RuntimeServices(config, database_root="/tmp/test-security-headers")
    app = create_app(config=config, services=services)

    with TestClient(app) as client:
        resp = client.get("/health/live")
        assert resp.status_code == 200

        # Check security headers
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.headers["x-frame-options"] == "DENY"
        assert resp.headers["content-security-policy"] == "default-src 'none'"
        assert "strict-transport-security" not in resp.headers


def test_security_headers_disabled() -> None:
    """Verify security headers are not added when disabled."""
    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "server": {"security_headers": {"enabled": False}},
        }
    )
    services = RuntimeServices(config, database_root="/tmp/test-security-headers")
    app = create_app(config=config, services=services)

    with TestClient(app) as client:
        resp = client.get("/health/live")
        assert resp.status_code == 200

        # Security headers should not be present
        assert "x-content-type-options" not in resp.headers
        assert "x-frame-options" not in resp.headers
        assert "content-security-policy" not in resp.headers


def test_security_headers_on_api_endpoints() -> None:
    """Verify security headers are present on API endpoints."""
    config = RuntimeConfig.model_validate({"model": {"provider": "fake"}})
    services = RuntimeServices(config, database_root="/tmp/test-security-headers")
    app = create_app(config=config, services=services)

    with TestClient(app) as client:
        resp = client.get("/v1/capabilities")
        assert resp.status_code == 200

        # Check security headers on API endpoints
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.headers["x-frame-options"] == "DENY"


def test_security_headers_via_config_file(tmp_path) -> None:
    """Verify security headers can be configured via config file."""
    import yaml

    config_path = tmp_path / "agent.yaml"
    config_path.write_text(
        yaml.dump(
            {
                "model": {"provider": "fake"},
                "server": {"security_headers": {"enabled": True}},
            }
        ),
        encoding="utf-8",
    )

    config = RuntimeConfig.from_file(config_path)
    assert config.server.security_headers.enabled is True


def test_security_headers_disabled_via_config_file(tmp_path) -> None:
    """Verify security headers can be disabled via config file."""
    import yaml

    config_path = tmp_path / "agent.yaml"
    config_path.write_text(
        yaml.dump(
            {
                "model": {"provider": "fake"},
                "server": {"security_headers": {"enabled": False}},
            }
        ),
        encoding="utf-8",
    )

    config = RuntimeConfig.from_file(config_path)
    assert config.server.security_headers.enabled is False


def test_security_headers_include_hsts_for_https() -> None:
    config = RuntimeConfig.model_validate({"model": {"provider": "fake"}})
    services = RuntimeServices(config, database_root="/tmp/test-security-headers")
    app = create_app(config=config, services=services)

    with TestClient(app, base_url="https://testserver") as client:
        resp = client.get("/health/live")

    assert resp.status_code == 200
    assert resp.headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"


def test_security_headers_are_configurable() -> None:
    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "server": {
                "security_headers": {
                    "content_type_options": "custom-nosniff",
                    "frame_options": "SAMEORIGIN",
                    "content_security_policy": "default-src 'self'",
                    "hsts_max_age_seconds": 86400,
                    "hsts_include_subdomains": False,
                    "hsts_preload": True,
                }
            },
        }
    )
    services = RuntimeServices(config, database_root="/tmp/test-security-headers")
    app = create_app(config=config, services=services)

    with TestClient(app, base_url="https://testserver") as client:
        resp = client.get("/health/live")

    assert resp.headers["x-content-type-options"] == "custom-nosniff"
    assert resp.headers["x-frame-options"] == "SAMEORIGIN"
    assert resp.headers["content-security-policy"] == "default-src 'self'"
    assert resp.headers["strict-transport-security"] == "max-age=86400; preload"


def test_security_headers_not_duplicated() -> None:
    """Verify security headers are not duplicated if already set."""
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from open_workflow_agent.security_headers import SecurityHeadersMiddleware

    app = FastAPI()

    @app.get("/test")
    async def test_endpoint():
        return JSONResponse(
            content={"ok": True},
            headers={"X-Content-Type-Options": "nosniff"},
        )

    app.add_middleware(SecurityHeadersMiddleware, config=SecurityHeadersConfig())

    with TestClient(app) as client:
        resp = client.get("/test")
        assert resp.status_code == 200

        # Should not have duplicate headers
        assert resp.headers.get_list("x-content-type-options") == ["nosniff"]
