"""Subprocess recovery diagnostics expose classification without input values."""

from __future__ import annotations

import sqlite3

from _recovery_failure_artifact import failure_artifact
from open_workflow_agent.errors import InvocationStateError


def test_failure_artifact_allowlists_error_details_and_profile_field_paths() -> None:
    error = InvocationStateError(
        "secret user input must not appear",
        details={
            "reason": "runtime_profile_changed",
            "changed_fields": ["python.architecture"],
            "token": "secret-token-value",
        },
    )

    artifact = failure_artifact(error, stage="recover:resolve")
    serialized = repr(artifact)

    assert artifact["stage"] == "recover:resolve"
    assert artifact["type"] == "InvocationStateError"
    assert artifact["code"] == "invalid_invocation_state"
    assert artifact["details"] == {
        "reason": "runtime_profile_changed",
        "changed_fields": ["python.architecture"],
    }
    assert "secret" not in serialized
    assert "token" not in serialized


def test_failure_artifact_records_only_safe_traceback_frames_and_sqlite_codes() -> None:
    try:
        sqlite3.connect(":memory:").execute("SELECT * FROM missing_table")
    except sqlite3.OperationalError as error:
        artifact = failure_artifact(error, stage="recover:sqlite", include_traceback=True)
    else:
        raise AssertionError("expected the deterministic SQLite error")

    assert artifact["sqlite_errorcode"] == sqlite3.SQLITE_ERROR
    assert artifact["sqlite_errorname"] == "SQLITE_ERROR"
    assert artifact["frames"]
    assert all(set(frame) == {"file", "line", "function"} for frame in artifact["frames"])
    assert "missing_table" not in repr(artifact)
