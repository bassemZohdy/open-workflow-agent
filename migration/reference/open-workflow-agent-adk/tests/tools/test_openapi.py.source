"""Tests for OpenAPI spec generation."""

from __future__ import annotations

from openworkflow_adk import export_openapi, generate_openapi, load


def test_generate_openapi_contains_workflow_endpoints() -> None:
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "openapi-flow",
                "version": "1.0.0",
            },
            "do": [{"finish": {"set": {"value": 1}}}],
        }
    )

    spec = generate_openapi(document, base_url="http://example.com")

    assert spec["openapi"] == "3.1.0"
    assert spec["info"]["title"] == "owf-adk: openapi-flow"
    assert spec["info"]["version"] == "1.0.0"
    assert any(s["url"] == "http://example.com" for s in spec["servers"])
    assert "/health" in spec["paths"]
    assert "/metrics" in spec["paths"]
    assert "/run" in spec["paths"]
    assert "/run/stream" in spec["paths"]
    assert "RunRequest" in spec["components"]["schemas"]


def test_export_openapi_returns_json_string() -> None:
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "openapi-json",
                "version": "2.0.0",
            },
            "do": [{"finish": {"set": {"value": 1}}}],
        }
    )

    text = export_openapi(document)
    assert '"openapi": "3.1.0"' in text
    assert "openapi-json" in text
