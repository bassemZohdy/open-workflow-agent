"""Branch requests reject private identifiers and unbounded replacement data."""

import pytest
from open_workflow_agent.branch_control_api import BranchStateRequest, state_branch_capabilities
from pydantic import ValidationError


def test_branch_request_accepts_only_bounded_common_fields():
    valid = {"reference": "1" * 64, "task_reference": "/do/0/first", "values": {"value": 7}}
    assert BranchStateRequest(**valid).acknowledge_reexecution is False
    for invalid in (
        valid | {"reference": "short"},
        valid | {"checkpoint_id": "private"},
        valid | {"task_reference": "x" * 4097},
        valid | {"values": {"value": "x" * 70_000}},
        valid | {"values": {"value": float("nan")}},
        valid | {"acknowledge_reexecution": "true"},
    ):
        with pytest.raises(ValidationError):
            BranchStateRequest(**invalid)


def test_branch_capability_names_only_supported_scope():
    capability = state_branch_capabilities()
    assert capability["scope"] == "root"
    assert capability["checkpoints"] == "completed_task_prefix"
    assert capability["attribution"] == "last_completed_declared_set_task"
    assert capability["actions"] == ["state.branch"]
    assert capability["timeTravel"] is False
