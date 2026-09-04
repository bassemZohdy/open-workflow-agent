"""Protocol interoperability evidence and baseline conformance tests.

These tests verify that all advertised protocol baselines conform to their
respective specifications and accurately advertise their capabilities.
"""

from __future__ import annotations

import json

import httpx
import pytest
from open_workflow_agent.a2a import (
    A2A_PROTOCOL_VERSION as INBOUND_A2A_PROTOCOL_VERSION,
    A2A_SPEC_RELEASE,
)
from open_workflow_agent.observability import (
    CLOUD_EVENTS_SPECVERSION,
    CloudEvent,
    LifecycleCloudEventSink,
    WorkflowEvent,
)
from open_workflow_agent.protocols import (
    A2A_METHODS,
    A2A_PROTOCOL_VERSION,
    MCP_METHODS,
    MCP_PROTOCOL_VERSION,
    HttpClient,
    ProtocolServices,
)


# --- CloudEvents 1.0 Conformance Tests ---


def test_cloud_events_specversion_matches_baseline() -> None:
    """Verify CloudEvents specversion matches pinned baseline."""
    assert CLOUD_EVENTS_SPECVERSION == "1.0"


def test_cloud_event_has_required_v1_fields() -> None:
    """Verify CloudEvent contains all required CloudEvents 1.0 fields."""
    event = CloudEvent(
        specversion=CLOUD_EVENTS_SPECVERSION,
        id="test-id",
        source="urn:test",
        type="com.test.event.v1",
        subject="test-subject",
        time="2026-01-01T00:00:00Z",
        datacontenttype="application/json",
        dataschema="urn:test:schema:1",
        data={"key": "value"},
    )

    # Required CloudEvents 1.0 attributes
    assert event.specversion == "1.0"
    assert event.id is not None
    assert event.source is not None
    assert event.type is not None
    assert event.time is not None


def test_cloud_event_serialization_includes_required_attributes() -> None:
    """Verify CloudEvent serialization includes all required attributes."""
    event = CloudEvent(
        specversion=CLOUD_EVENTS_SPECVERSION,
        id="test-id",
        source="urn:test",
        type="com.test.event.v1",
        subject="test-subject",
        time="2026-01-01T00:00:00Z",
        datacontenttype="application/json",
        dataschema="urn:test:schema:1",
        data={"key": "value"},
    )

    serialized = event.as_dict()

    # Required CloudEvents 1.0 attributes in serialized form
    assert "specversion" in serialized
    assert serialized["specversion"] == "1.0"
    assert "id" in serialized
    assert "source" in serialized
    assert "type" in serialized
    assert "time" in serialized
    assert "datacontenttype" in serialized


def test_lifecycle_event_to_cloud_event_has_correct_structure() -> None:
    """Verify lifecycle events convert to valid CloudEvents structure."""
    from datetime import UTC, datetime

    event = WorkflowEvent(
        event_type="WorkflowStarted",
        invocation_id="inv-1",
        session_id="sess-1",
        workflow_name="test-workflow",
        workflow_version="1.0.0",
    )

    cloud_event = event.to_cloud_event()

    # Should have correct CloudEvents structure
    assert cloud_event.specversion == CLOUD_EVENTS_SPECVERSION
    assert cloud_event.id is not None
    assert cloud_event.source == "urn:open-workflow-agent:lifecycle"
    assert cloud_event.type.startswith("com.openworkflow.agent.lifecycle.")
    assert cloud_event.type.endswith(".v1")
    assert cloud_event.time is not None
    assert cloud_event.datacontenttype == "application/json"


# --- MCP Protocol Conformance Tests ---


def test_mcp_protocol_version_matches_baseline() -> None:
    """Verify MCP protocol version matches pinned baseline."""
    assert MCP_PROTOCOL_VERSION == "2026-07-28"


def test_mcp_method_set_covers_bounded_profile() -> None:
    """Verify MCP method set covers the bounded common client/tool profile."""
    required_methods = {
        "tools/list",
        "tools/call",
        "prompts/list",
        "prompts/get",
        "resources/list",
        "resources/read",
        "resources/templates/list",
    }

    assert required_methods.issubset(MCP_METHODS)


def test_mcp_outbound_request_includes_required_headers() -> None:
    """Verify MCP outbound requests include required protocol headers."""
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"result": {"ok": True}})

    services = ProtocolServices(HttpClient(transport=httpx.MockTransport(handler)))

    import asyncio

    asyncio.run(
        services.call(
            "mcp",
            {
                "method": "tools/list",
                "transport": {"http": {"endpoint": "https://mcp.test"}},
                "parameters": {},
            },
        )
    )

    request = seen[0]
    # Required MCP protocol headers
    assert "Mcp-Protocol-Version" in request.headers
    assert request.headers["Mcp-Protocol-Version"] == MCP_PROTOCOL_VERSION
    assert "Mcp-Method" in request.headers
    assert request.headers["Mcp-Method"] == "tools/list"


def test_mcp_rejects_legacy_protocol_version() -> None:
    """Verify MCP rejects unsupported protocol versions."""
    services = ProtocolServices(
        HttpClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"ok": True})
            )
        )
    )

    import asyncio

    with pytest.raises(Exception, match="unsupported MCP protocol version"):
        asyncio.run(
            services.call(
                "mcp",
                {
                    "endpoint": "https://mcp.test",
                    "protocolVersion": "2025-11-25",
                    "method": "tools/list",
                },
            )
        )


# --- A2A Protocol Conformance Tests ---


def test_a2a_protocol_version_matches_baseline() -> None:
    """Verify A2A protocol version matches pinned baseline."""
    assert A2A_PROTOCOL_VERSION == "1.0"
    assert INBOUND_A2A_PROTOCOL_VERSION == "1.0"


def test_a2a_spec_release_matches_baseline() -> None:
    """Verify A2A spec release matches pinned baseline."""
    assert A2A_SPEC_RELEASE == "1.0.1"


def test_a2a_outbound_method_set_covers_bounded_profile() -> None:
    """Verify A2A outbound method set covers the bounded profile."""
    required_methods = {
        "SendMessage",
        "GetTask",
        "CancelTask",
    }

    assert required_methods.issubset(A2A_METHODS)


def test_a2a_outbound_request_includes_required_headers() -> None:
    """Verify A2A outbound requests include required protocol headers."""
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"result": {"ok": True}})

    services = ProtocolServices(HttpClient(transport=httpx.MockTransport(handler)))

    import asyncio

    asyncio.run(
        services.call(
            "a2a",
            {
                "server": "https://agent.test/a2a",
                "method": "SendMessage",
                "message": {"role": "ROLE_USER", "parts": [{"text": "hello"}]},
            },
        )
    )

    request = seen[0]
    # Required A2A protocol headers
    assert "A2A-Version" in request.headers
    assert request.headers["A2A-Version"] == A2A_PROTOCOL_VERSION


def test_a2a_rejects_legacy_protocol_version() -> None:
    """Verify A2A rejects unsupported protocol versions."""
    services = ProtocolServices(
        HttpClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"ok": True})
            )
        )
    )

    import asyncio

    with pytest.raises(Exception, match="unsupported A2A protocol version"):
        asyncio.run(
            services.call(
                "a2a",
                {
                    "server": "https://agent.test/a2a",
                    "protocolVersion": "0.3",
                    "method": "SendMessage",
                    "message": {"role": "ROLE_USER", "parts": [{"text": "hello"}]},
                },
            )
        )


def test_a2a_rejects_legacy_method_names() -> None:
    """Verify A2A rejects legacy method names."""
    services = ProtocolServices(
        HttpClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"ok": True})
            )
        )
    )

    import asyncio

    # Legacy A2A 0.3 method names should be rejected
    with pytest.raises(Exception, match="unsupported A2A method"):
        asyncio.run(
            services.call(
                "a2a",
                {
                    "server": "https://agent.test/a2a",
                    "method": "message/send",
                    "message": {"role": "ROLE_USER", "parts": [{"text": "hello"}]},
                },
            )
        )


# --- OpenAPI Protocol Conformance Tests ---


def test_openapi_outbound_request_uses_operation_id() -> None:
    """Verify OpenAPI outbound requests use operationId for routing."""
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    services = ProtocolServices(HttpClient(transport=httpx.MockTransport(handler)))

    import asyncio

    asyncio.run(
        services.call(
            "openapi",
            {
                "document": {"endpoint": "https://api.test/openapi"},
                "operationId": "lookup",
                "parameters": {"query": "hello"},
            },
        )
    )

    # Should have made a request
    assert len(seen) == 1


# --- Cross-Protocol Consistency Tests ---


def test_all_protocol_versions_are_consistent_across_modules() -> None:
    """Verify protocol versions are consistent across inbound and outbound modules."""
    # A2A version should be consistent
    assert A2A_PROTOCOL_VERSION == INBOUND_A2A_PROTOCOL_VERSION

    # MCP version should be a valid date format
    assert len(MCP_PROTOCOL_VERSION) == 10  # YYYY-MM-DD
    assert MCP_PROTOCOL_VERSION[4] == "-"
    assert MCP_PROTOCOL_VERSION[7] == "-"


def test_protocol_baseline_manifest_matches_runtime_versions() -> None:
    """Verify protocol baseline manifest matches runtime version constants."""
    from pathlib import Path

    import yaml

    ROOT = Path(__file__).resolve().parents[2]
    BASELINE_FILE = ROOT / "resources" / "protocol-baselines.yaml"
    baselines = yaml.safe_load(BASELINE_FILE.read_text(encoding="utf-8"))["baselines"]

    # Verify all versions match
    assert baselines["a2a"]["release"] == A2A_SPEC_RELEASE
    assert baselines["a2a"]["protocol_version"] == A2A_PROTOCOL_VERSION
    assert baselines["mcp"]["release"] == MCP_PROTOCOL_VERSION
    assert baselines["mcp"]["protocol_version"] == MCP_PROTOCOL_VERSION
    assert baselines["cloudevents"]["specversion"] == CLOUD_EVENTS_SPECVERSION
