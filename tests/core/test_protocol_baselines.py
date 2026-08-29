from __future__ import annotations

from pathlib import Path

import yaml
from open_workflow_agent.a2a import (
    A2A_PROTOCOL_VERSION as INBOUND_A2A_PROTOCOL_VERSION,
)
from open_workflow_agent.a2a import A2A_SPEC_RELEASE
from open_workflow_agent.protocols import (
    A2A_METHODS,
    A2A_PROTOCOL_VERSION,
    MCP_METHODS,
    MCP_PROTOCOL_VERSION,
)

ROOT = Path(__file__).resolve().parents[2]
BASELINE_FILE = ROOT / "resources" / "protocol-baselines.yaml"
DOC_FILE = ROOT / "docs" / "protocol-baselines.md"


def _baselines() -> dict[str, dict[str, object]]:
    document = yaml.safe_load(BASELINE_FILE.read_text(encoding="utf-8"))
    assert document["policy"] == "latest-stable-reviewed"
    return document["baselines"]


def test_protocol_baseline_manifest_matches_runtime_wire_versions() -> None:
    baselines = _baselines()

    assert baselines["open_workflow"]["release"] == "1.0.3"
    assert (ROOT / "resources" / "open-workflow" / "1.0.3" / "workflow.yaml").exists()

    assert baselines["a2a"]["release"] == A2A_SPEC_RELEASE
    assert baselines["a2a"]["protocol_version"] == A2A_PROTOCOL_VERSION
    assert A2A_PROTOCOL_VERSION == INBOUND_A2A_PROTOCOL_VERSION

    assert baselines["mcp"]["release"] == MCP_PROTOCOL_VERSION
    assert baselines["mcp"]["protocol_version"] == MCP_PROTOCOL_VERSION

    assert baselines["cloudevents"]["release"] == "1.0.2"
    assert baselines["cloudevents"]["specversion"] == "1.0"
    assert baselines["openapi"]["release"] == "3.2.0"
    assert baselines["asyncapi"]["release"] == "3.1.0"


def test_protocol_baseline_manifest_keeps_claims_bounded() -> None:
    baselines = _baselines()

    assert all(not entry.get("conformance_claim", False) for entry in baselines.values())
    assert baselines["open_workflow"]["profile"] == "OWA Portable Profile v1"
    assert baselines["a2a"]["profile"] == "bounded inbound/outbound profile"
    assert baselines["mcp"]["profile"] == "bounded common client/tool profile"
    assert baselines["openapi"]["profile"] == "bounded operation adapter"
    assert baselines["cloudevents"]["profile"] == "bounded lifecycle events"
    assert baselines["asyncapi"]["implemented"] is False


def test_protocol_method_sets_match_the_reviewed_bounded_profiles() -> None:
    assert MCP_METHODS == frozenset(
        {
            "tools/list",
            "tools/call",
            "prompts/list",
            "prompts/get",
            "resources/list",
            "resources/read",
            "resources/templates/list",
        }
    )
    assert A2A_METHODS == frozenset(
        {"SendMessage", "GetTask", "ListTasks", "CancelTask", "GetExtendedAgentCard"}
    )
    assert "message/send" not in A2A_METHODS
    assert "SendStreamingMessage" not in A2A_METHODS
    assert "SubscribeToTask" not in A2A_METHODS


def test_protocol_documentation_cannot_drift_from_the_pinned_manifest() -> None:
    baselines = _baselines()
    documentation = DOC_FILE.read_text(encoding="utf-8")

    expected_labels = {
        "Open Workflow Specification": "open_workflow",
        "A2A Protocol": "a2a",
        "Model Context Protocol": "mcp",
        "OpenAPI Specification": "openapi",
        "CloudEvents": "cloudevents",
        "AsyncAPI Specification": "asyncapi",
    }
    for label, key in expected_labels.items():
        entry = baselines[key]
        release = entry["release"]
        protocol_version = entry.get("protocol_version")
        if protocol_version is not None and protocol_version != release:
            expected_row = f"| {label} | release `{release}`, protocol `{protocol_version}` |"
        else:
            expected_row = f"| {label} | `{release}` |"
        assert expected_row in documentation

    assert "reviewed stable releases" in documentation
    assert "never float automatically at runtime" in documentation
