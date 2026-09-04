"""Tests for optional API authentication middleware."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from open_workflow_agent.api import create_app
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.services import RuntimeServices


def test_api_authentication_disabled_by_default() -> None:
    """Verify API endpoints are accessible without auth when not configured."""
    config = RuntimeConfig.model_validate({"model": {"provider": "fake"}})
    services = RuntimeServices(config, database_root="/tmp/test-api-auth")
    app = create_app(config=config, services=services)

    with TestClient(app) as client:
        # Health endpoints should work
        resp = client.get("/health/live")
        assert resp.status_code == 200

        # API endpoints should work without auth
        resp = client.get("/v1/capabilities")
        assert resp.status_code == 200


def test_api_authentication_with_bearer_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify API authentication works with bearer token."""
    monkeypatch.setenv("API_TOKEN", "test-api-token-123")

    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "server": {"api_security_profile": "api-auth"},
            "security": {
                "profiles": {
                    "api-auth": {
                        "type": "bearer",
                        "token": {"from_env": "API_TOKEN"},
                    }
                }
            },
        }
    )
    services = RuntimeServices(config, database_root="/tmp/test-api-auth")
    app = create_app(config=config, services=services)

    with TestClient(app, raise_server_exceptions=False) as client:
        # Health endpoints should work without auth
        resp = client.get("/health/live")
        assert resp.status_code == 200

        # API endpoints should fail without auth
        resp = client.get("/v1/capabilities")
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "authentication_required"

        # API endpoints should fail with wrong token
        resp = client.get(
            "/v1/capabilities",
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "authentication_failed"

        # API endpoints should work with correct token
        resp = client.get(
            "/v1/capabilities",
            headers={"Authorization": "Bearer test-api-token-123"},
        )
        assert resp.status_code == 200


def test_api_authentication_with_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify API authentication works with API key."""
    monkeypatch.setenv("API_KEY", "test-api-key-456")

    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "server": {"api_security_profile": "api-key-auth"},
            "security": {
                "profiles": {
                    "api-key-auth": {
                        "type": "api_key",
                        "key": {"from_env": "API_KEY"},
                        "header": "X-API-Key",
                    }
                }
            },
        }
    )
    services = RuntimeServices(config, database_root="/tmp/test-api-auth")
    app = create_app(config=config, services=services)

    with TestClient(app, raise_server_exceptions=False) as client:
        # Health endpoints should work without auth
        resp = client.get("/health/live")
        assert resp.status_code == 200

        # API endpoints should fail without auth
        resp = client.get("/v1/capabilities")
        assert resp.status_code == 401

        # API endpoints should fail with wrong key
        resp = client.get(
            "/v1/capabilities",
            headers={"X-API-Key": "wrong-key"},
        )
        assert resp.status_code == 403

        # API endpoints should work with correct key
        resp = client.get(
            "/v1/capabilities",
            headers={"X-API-Key": "test-api-key-456"},
        )
        assert resp.status_code == 200


def test_api_authentication_does_not_protect_health_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify health endpoints are not protected by API authentication."""
    monkeypatch.setenv("API_TOKEN", "test-token")

    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "server": {"api_security_profile": "api-auth"},
            "security": {
                "profiles": {
                    "api-auth": {
                        "type": "bearer",
                        "token": {"from_env": "API_TOKEN"},
                    }
                }
            },
        }
    )
    services = RuntimeServices(config, database_root="/tmp/test-api-auth")
    app = create_app(config=config, services=services)

    with TestClient(app) as client:
        # Health endpoints should work without auth
        resp = client.get("/health/live")
        assert resp.status_code == 200

        resp = client.get("/health/ready")
        assert resp.status_code in (200, 503)  # May not be ready


def test_api_authentication_invalid_profile_reference() -> None:
    """Verify invalid profile reference fails at startup."""
    with pytest.raises(Exception, match="unknown security profile"):
        RuntimeConfig.model_validate(
            {
                "server": {"api_security_profile": "nonexistent"},
            }
        )


def test_api_authentication_non_header_capable_profile() -> None:
    """Verify non-header-capable profile fails at startup."""
    with pytest.raises(Exception, match="must reference a security profile of type"):
        RuntimeConfig.model_validate(
            {
                "server": {"api_security_profile": "oauth"},
                "security": {
                    "profiles": {
                        "oauth": {
                            "type": "oauth2_client_credentials",
                            "token_url": "https://auth.example.com/token",
                            "client_id": {"from_env": "CLIENT_ID"},
                            "client_secret": {"from_env": "CLIENT_SECRET"},
                        }
                    }
                },
            }
        )
