from __future__ import annotations

import copy
import sys

import pytest
from open_workflow_agent.config import SandboxConfig
from open_workflow_agent.errors import UnsupportedWorkflowFeature
from open_workflow_agent.sandbox import compile_sandbox_workflow
from open_workflow_agent.workflow import compile_workflow


def _workflow():
    return {
        "document": {
            "dsl": "1.0.3",
            "namespace": "test",
            "name": "mcp-admission",
            "version": "1.0.0",
        },
        "do": [
            {
                "query": {
                    "call": "mcp",
                    "with": {
                        "protocolVersion": "2026-07-28",
                        "method": "tools/list",
                        "parameters": {},
                        "transport": {
                            "stdio": {"command": sys.executable, "arguments": ["server.py"]}
                        },
                    },
                }
            }
        ],
    }


def _sandbox():
    return SandboxConfig(
        enabled=True,
        backend="internal",
        file_size_bytes=None,
        mcp_stdio={
            "enabled": True,
            "servers": [
                {
                    "id": "catalog",
                    "command": sys.executable,
                    "arguments": ["server.py"],
                    "environment": {"SERVER_MODE": "readonly"},
                }
            ],
        },
    )


def test_stdio_admission_preserves_public_source_and_requires_deployment_binding():
    source = _workflow()
    original = copy.deepcopy(source)
    with pytest.raises(UnsupportedWorkflowFeature, match="stdio"):
        compile_workflow(source)
    plan = compile_sandbox_workflow(source, sandbox=_sandbox())
    assert source == original
    assert plan.source == original
    assert plan.fingerprint == compile_sandbox_workflow(source, sandbox=_sandbox()).fingerprint


@pytest.mark.parametrize(
    "mutation", ["disabled", "external", "command", "arguments", "environment"]
)
def test_stdio_rejects_unapproved_declarations_before_plan_admission(mutation):
    source, sandbox = _workflow(), _sandbox()
    stdio = source["do"][0]["query"]["with"]["transport"]["stdio"]
    if mutation == "disabled":
        sandbox.mcp_stdio.enabled = False
    elif mutation == "external":
        sandbox.docker.allowed_images = ["registry.example/worker@sha256:" + "a" * 64]
        sandbox.backend = "docker"
    elif mutation == "command":
        stdio["command"] = "${ .command }"
    elif mutation == "arguments":
        stdio["arguments"] = ["other-server.py"]
    else:
        stdio["environment"] = {"SERVER_MODE": "write"}
    with pytest.raises(UnsupportedWorkflowFeature, match="MCP stdio"):
        compile_sandbox_workflow(source, sandbox=sandbox)


def test_nested_stdio_admission_does_not_skip_unapproved_child_declaration():
    source = _workflow()
    child = source["do"][0]
    child["query"]["with"]["transport"]["stdio"]["arguments"] = ["unapproved.py"]
    source["do"] = [{"scope": {"do": [child]}}]
    with pytest.raises(UnsupportedWorkflowFeature) as failure:
        compile_sandbox_workflow(source, sandbox=_sandbox())
    assert failure.value.details["reference"] == "/do/0/scope/do/0/query"


@pytest.mark.parametrize(
    "protocol_version",
    ["2025-06-18", "2025-03-26", "2025-11-25", "2030-01-01", "2099-01-01"],
)
def test_unsupported_mcp_wire_version_fails_before_workflow_plan_admission(protocol_version):
    source = _workflow()
    source["do"][0]["query"]["with"]["protocolVersion"] = protocol_version
    source["do"][0]["query"]["with"]["transport"] = {"http": {"endpoint": "https://mcp.test"}}
    with pytest.raises(UnsupportedWorkflowFeature, match="protocol version"):
        compile_workflow(source)
    with pytest.raises(UnsupportedWorkflowFeature, match="protocol version"):
        compile_sandbox_workflow(source, sandbox=_sandbox())
