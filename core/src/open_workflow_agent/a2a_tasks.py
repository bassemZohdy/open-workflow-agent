"""A2A Task projection over common Open Workflow Agent invocation state.

A2A Tasks are protocol views of :class:`ExecutionHandle`; they are not a second
execution or persistence system. The public Task never exposes engine-native
checkpoint references, workflow fingerprints, or deployment secrets.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from .persistence import ExecutionHandle

A2A_TASK_STATES: dict[str, str] = {
    "running": "TASK_STATE_WORKING",
    "waiting": "TASK_STATE_INPUT_REQUIRED",
    "completed": "TASK_STATE_COMPLETED",
    "faulted": "TASK_STATE_FAILED",
    "cancelled": "TASK_STATE_CANCELED",
}


def a2a_task_state(status: str) -> str:
    """Translate one common invocation state to the A2A v1 Task state."""

    try:
        return A2A_TASK_STATES[status]
    except KeyError as exc:
        raise ValueError(f"unsupported invocation state for A2A Task: {status}") from exc


def project_a2a_task(handle: ExecutionHandle) -> dict[str, Any]:
    """Return a sanitized A2A v1 Task view for a common invocation handle."""

    task: dict[str, Any] = {
        "id": handle.invocation_id,
        "contextId": handle.session_id,
        "status": {"state": a2a_task_state(handle.status)},
    }

    if handle.status == "completed" and handle.output is not None:
        task["artifacts"] = [
            {
                "artifactId": f"{handle.invocation_id}:output",
                "name": "workflow-output",
                "parts": [_output_part(handle.output)],
            }
        ]
    elif handle.status == "faulted":
        code = "workflow_execution_error"
        if isinstance(handle.error, dict):
            candidate = handle.error.get("code")
            if isinstance(candidate, str) and candidate:
                code = candidate
        task["status"]["message"] = _status_message(
            handle,
            suffix="error",
            text=f"workflow failed: {code}",
        )
    elif handle.status == "waiting":
        task["status"]["message"] = _status_message(
            handle,
            suffix="input-required",
            text="additional input is required",
        )

    return task


def _status_message(handle: ExecutionHandle, *, suffix: str, text: str) -> dict[str, Any]:
    return {
        "messageId": f"{handle.invocation_id}:{suffix}",
        "contextId": handle.session_id,
        "taskId": handle.invocation_id,
        "role": "ROLE_AGENT",
        "parts": [{"text": text, "mediaType": "text/plain"}],
    }


def _output_part(output: Any) -> dict[str, Any]:
    if isinstance(output, str):
        return {"text": output, "mediaType": "text/plain"}
    if isinstance(output, bytes):
        return {
            "raw": base64.b64encode(output).decode("ascii"),
            "mediaType": "application/octet-stream",
        }
    return {
        "data": _json_compatible(output),
        "mediaType": "application/json",
    }


def _json_compatible(value: Any) -> Any:
    """Fail closed if an invocation output cannot cross the JSON protocol boundary."""

    try:
        return json.loads(json.dumps(value))
    except (TypeError, ValueError) as exc:
        raise ValueError("invocation output is not JSON serializable for A2A Task") from exc


__all__ = ["A2A_TASK_STATES", "a2a_task_state", "project_a2a_task"]
