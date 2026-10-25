"""Small deterministic validation edges for the common workflow contract."""

from __future__ import annotations

import pytest
from open_workflow_agent.errors import (
    UnsupportedWorkflowFeature,
    WorkflowSchemaError,
    WorkflowSemanticError,
)
from open_workflow_agent.workflow import (
    _catch_error_value,
    _duration_seconds,
    _retry_attempts,
    load_json_value,
    validate_capabilities,
)


def test_yaml_loader_rejects_invalid_syntax_and_non_json_values() -> None:
    assert load_json_value("null") is None
    with pytest.raises(WorkflowSchemaError, match="invalid workflow YAML"):
        load_json_value("items: [unterminated")
    with pytest.raises(WorkflowSchemaError, match="finite UTF-8 JSON"):
        load_json_value("value: !!binary YQ==")


def test_capability_validation_rejects_duplicate_names_and_disabled_execution() -> None:
    with pytest.raises(WorkflowSemanticError, match="duplicate task name"):
        validate_capabilities(
            {
                "document": {"dsl": "1.0.3"},
                "do": [{"repeat": {"set": {}}}, {"repeat": {"set": {}}}],
            }
        )
    with pytest.raises(UnsupportedWorkflowFeature, match="is not enabled"):
        validate_capabilities(
            {"document": {"dsl": "1.0.3"}, "do": [{"missing": {"call": "custom:missing"}}]}
        )


def test_retry_duration_and_catch_projection_keep_bounded_common_semantics() -> None:
    assert _retry_attempts(None) == 1
    assert _retry_attempts({"limit": {"attempt": {"count": 2}}}) == 3
    assert _retry_attempts({"limit": "not-a-number"}) == 1
    assert _duration_seconds({"after": {"milliseconds": 250}}) == 0.25
    assert _duration_seconds("PT1M2S") == 62
    with pytest.raises(WorkflowSemanticError, match="invalid duration"):
        _duration_seconds("soonish")

    caught = _catch_error_value(
        {
            "code": "workflow_schema_error",
            "message": "invalid input",
            "details": {"reference": "/do/0"},
        },
        status_code=403,
    )
    assert caught["reference"] == "/do/0"
    assert caught["type"].endswith("/authorization")
