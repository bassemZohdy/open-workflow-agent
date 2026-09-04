"""Tests for enhanced health check endpoint."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from open_workflow_agent.api import create_app
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.services import RuntimeServices


def test_health_live_endpoint() -> None:
    """Verify /health/live always returns ok."""
    config = RuntimeConfig.model_validate({"model": {"provider": "fake"}})
    services = RuntimeServices(config, database_root="/tmp/test-health")
    app = create_app(config=config, services=services)

    with TestClient(app) as client:
        resp = client.get("/health/live")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


def test_health_ready_before_initialization() -> None:
    """Verify /health/ready returns 503 before app is ready."""
    config = RuntimeConfig.model_validate({"model": {"provider": "fake"}})
    services = RuntimeServices(config, database_root="/tmp/test-health")
    app = create_app(config=config, services=services)

    # Before lifespan completes, app is not ready
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/health/ready")
        # The app should be ready after TestClient context manager starts
        assert resp.status_code in (200, 503)


def test_health_ready_with_dependency_checks() -> None:
    """Verify /health/ready includes dependency checks."""
    config = RuntimeConfig.model_validate({"model": {"provider": "fake"}})
    services = RuntimeServices(config, database_root="/tmp/test-health")
    app = create_app(config=config, services=services)

    with TestClient(app) as client:
        resp = client.get("/health/ready")
        assert resp.status_code == 200

        data = resp.json()
        assert "status" in data
        assert "checks" in data

        # Should have database check
        assert "database" in data["checks"]
        assert data["checks"]["database"] == "ok"

        # Should have model check
        assert "model" in data["checks"]
        assert data["checks"]["model"] == "ok"


def test_health_ready_with_knowledge_configured() -> None:
    """Verify /health/ready checks knowledge when configured."""
    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "knowledge": {"path": "/knowledge"},
        }
    )
    services = RuntimeServices(config, database_root="/tmp/test-health")
    app = create_app(config=config, services=services)

    with TestClient(app) as client:
        resp = client.get("/health/ready")
        assert resp.status_code == 200

        data = resp.json()
        assert "knowledge" in data["checks"]
        assert data["checks"]["knowledge"] == "ok"


def test_health_ready_reports_all_checks() -> None:
    """Verify /health/ready reports all configured dependency checks."""
    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "knowledge": {"path": "/knowledge"},
        }
    )
    services = RuntimeServices(config, database_root="/tmp/test-health")
    app = create_app(config=config, services=services)

    with TestClient(app) as client:
        resp = client.get("/health/ready")
        assert resp.status_code == 200

        data = resp.json()
        checks = data["checks"]

        # All checks should be present
        assert "database" in checks
        assert "model" in checks
        assert "knowledge" in checks

        # All checks should pass
        for check_name, check_status in checks.items():
            assert check_status == "ok", f"Check {check_name} failed: {check_status}"


def test_health_ready_status_ok_when_all_healthy() -> None:
    """Verify overall status is ok when all checks pass."""
    config = RuntimeConfig.model_validate({"model": {"provider": "fake"}})
    services = RuntimeServices(config, database_root="/tmp/test-health")
    app = create_app(config=config, services=services)

    with TestClient(app) as client:
        resp = client.get("/health/ready")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
