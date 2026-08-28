from __future__ import annotations

import pytest
from open_workflow_agent.a2a_tasks import (
    A2A_TASK_STATES,
    a2a_task_state,
    project_a2a_task,
)
from open_workflow_agent.persistence import ExecutionHandle


def _handle(status: str, *, output=None, error=None) -> ExecutionHandle:
    return ExecutionHandle(
        invocation_id="inv-1",
        engine="langgraph",
        engine_execution_reference="secret-engine-reference",
        user_id="user-1",
        session_id="ctx-1",
        workflow_name="renewal",
        workflow_version="1.0.0",
        workflow_fingerprint="secret-fingerprint",
        status=status,
        output=output,
        error=error,
    )


@pytest.mark.parametrize(
    ("owa_status", "a2a_state"),
    [
        ("running", "TASK_STATE_WORKING"),
        ("waiting", "TASK_STATE_INPUT_REQUIRED"),
        ("completed", "TASK_STATE_COMPLETED"),
        ("faulted", "TASK_STATE_FAILED"),
        ("cancelled", "TASK_STATE_CANCELED"),
    ],
)
def test_task_state_mapping_is_explicit_and_total(owa_status: str, a2a_state: str) -> None:
    assert a2a_task_state(owa_status) == a2a_state
    assert A2A_TASK_STATES[owa_status] == a2a_state


def test_unknown_invocation_state_fails_closed() -> None:
    with pytest.raises(ValueError, match="unsupported invocation state"):
        a2a_task_state("mystery")


def test_task_identity_projects_common_invocation_and_session_only() -> None:
    task = project_a2a_task(_handle("running"))

    assert task == {
        "id": "inv-1",
        "contextId": "ctx-1",
        "status": {"state": "TASK_STATE_WORKING"},
    }
    serialized = repr(task)
    assert "secret-engine-reference" not in serialized
    assert "secret-fingerprint" not in serialized
    assert "langgraph" not in serialized
    assert "user-1" not in serialized


def test_completed_task_projects_output_as_artifact() -> None:
    task = project_a2a_task(_handle("completed", output={"approved": True}))

    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert task["artifacts"] == [
        {
            "artifactId": "inv-1:output",
            "name": "workflow-output",
            "parts": [{"data": {"approved": True}, "mediaType": "application/json"}],
        }
    ]


def test_completed_text_output_uses_v1_text_part_shape() -> None:
    task = project_a2a_task(_handle("completed", output="done"))

    assert task["artifacts"][0]["parts"] == [{"text": "done", "mediaType": "text/plain"}]
    assert "kind" not in task["artifacts"][0]["parts"][0]


def test_waiting_task_maps_to_input_required_without_engine_details() -> None:
    task = project_a2a_task(_handle("waiting"))

    assert task["status"] == {
        "state": "TASK_STATE_INPUT_REQUIRED",
        "message": {
            "messageId": "inv-1:input-required",
            "role": "ROLE_AGENT",
            "parts": [{"text": "additional input is required"}],
        },
    }


def test_faulted_task_exposes_only_sanitized_common_error_code() -> None:
    task = project_a2a_task(
        _handle(
            "faulted",
            error={
                "code": "tool_error",
                "message": "internal implementation details",
                "details": {"token": "do-not-leak"},
            },
        )
    )

    assert task["status"]["state"] == "TASK_STATE_FAILED"
    assert task["status"]["message"]["parts"] == [{"text": "workflow failed: tool_error"}]
    serialized = repr(task)
    assert "internal implementation details" not in serialized
    assert "do-not-leak" not in serialized


def test_non_json_output_fails_closed() -> None:
    task_handle = _handle("completed", output={"bad": object()})
    with pytest.raises(ValueError, match="not JSON serializable"):
        project_a2a_task(task_handle)
