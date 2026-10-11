"""Tests for the minimal HTTP server."""

from __future__ import annotations

import importlib.util

import pytest

from openworkflow_adk import load
from openworkflow_adk.ops.postgres_history import PostgresRunHistory, PostgresRunHistoryConfig
from tests.conftest import require_docker

pytestmark = [require_docker()]


def test_create_app_requires_server_extras(monkeypatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "fastapi", None)
    from openworkflow_adk.server import _check_deps

    with pytest.raises(ImportError):
        _check_deps()


@pytest.mark.skipif(
    importlib.util.find_spec("fastapi") is None or importlib.util.find_spec("uvicorn") is None,
    reason="server extras not installed",
)
def test_health_endpoint() -> None:
    from fastapi.testclient import TestClient

    from openworkflow_adk.server import create_app

    document = load(
        {
            "document": {"dsl": "1.0.3", "namespace": "demo", "name": "hello", "version": "1.0.0"},
            "do": [{"finish": {"set": {"value": 1}}}],
        }
    )
    client = TestClient(create_app(document))
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["workflow"] == "hello"


@pytest.mark.skipif(
    importlib.util.find_spec("fastapi") is None or importlib.util.find_spec("uvicorn") is None,
    reason="server extras not installed",
)
def test_run_endpoint_executes_workflow() -> None:
    from fastapi.testclient import TestClient

    from openworkflow_adk.server import create_app

    document = load(
        {
            "document": {"dsl": "1.0.3", "namespace": "demo", "name": "echo", "version": "1.0.0"},
            "do": [{"finish": {"set": {"greeting": '"hello"'}}}],
        }
    )
    client = TestClient(create_app(document))
    response = client.post("/run", json={"input": {"name": "Ada"}})
    assert response.status_code == 200
    data = response.json()
    assert data["workflow"] == "echo"
    assert data["events"]


@pytest.mark.skipif(
    importlib.util.find_spec("fastapi") is None or importlib.util.find_spec("uvicorn") is None,
    reason="server extras not installed",
)
def test_openapi_json_endpoint() -> None:
    from fastapi.testclient import TestClient

    from openworkflow_adk.server import create_app

    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "openapi-served",
                "version": "1.0.0",
            },
            "do": [{"finish": {"set": {"value": 1}}}],
        }
    )
    client = TestClient(create_app(document))
    response = client.get("/openapi.json")
    assert response.status_code == 200
    data = response.json()
    assert data["openapi"] == "3.1.0"
    assert "/run" in data["paths"]
    assert "/metrics" in data["paths"]


@pytest.mark.skipif(
    importlib.util.find_spec("fastapi") is None or importlib.util.find_spec("uvicorn") is None,
    reason="server extras not installed",
)
@pytest.mark.integration
async def test_run_endpoint_persists_to_postgres(postgres_url) -> None:
    from fastapi.testclient import TestClient

    from openworkflow_adk.server import create_app

    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "persisted",
                "version": "1.0.0",
            },
            "do": [{"finish": {"set": {"greeting": '"hello"'}}}],
        }
    )
    config = PostgresRunHistoryConfig(
        url=postgres_url, schema="server_test", namespace_id="server-ns"
    )
    history = PostgresRunHistory(config)
    await history.connect()
    try:
        client = TestClient(create_app(document, history=history))
        response = client.post("/run", json={"input": {"name": "Ada"}, "session_id": "session-1"})
        assert response.status_code == 200

        record = await history.get("session-1")
        assert record.status == "completed"
        assert record.workflow == "persisted"
    finally:
        await history.close()
