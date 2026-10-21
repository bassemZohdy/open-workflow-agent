"""Shared, bounded workflow-definition admission witnesses for Python and TypeScript."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from open_workflow_agent.errors import WorkflowSchemaError
from open_workflow_agent.workflow import load_workflow_text, validate_schema

FIXTURES = Path(__file__).parents[1] / "fixtures" / "workflow-definition-admission.json"
CASES: list[dict[str, Any]] = json.loads(FIXTURES.read_text(encoding="utf-8"))


def _category(error: WorkflowSchemaError) -> str:
    message = error.message
    if "mapping keys must be unique strings" in message or "invalid workflow YAML" in message:
        return "invalid_document"
    if "acyclic JSON" in message:
        return "invalid_document"
    if "finite JSON" in message or "cannot be serialized" in message:
        return "non_json_value"
    if "workflow root must be an object" in message:
        return "root_not_object"
    if message.startswith("schema validation failed at "):
        return "schema_invalid"
    if message == "only Open Workflow DSL 1.0.3 is supported":
        return "unsupported_version"
    raise AssertionError(f"unclassified workflow admission error: {message}")


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_python_definition_admission_matches_shared_parity_fixture(case: dict[str, Any]) -> None:
    if case["outcome"] == "accept":
        definition = load_workflow_text(case["source"])
        validate_schema(definition)
        assert definition == case["expected"]
        return

    with pytest.raises(WorkflowSchemaError) as raised:
        definition = load_workflow_text(case["source"])
        validate_schema(definition)
    assert _category(raised.value) == case["category"]
