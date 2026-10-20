"""Keep the A2A06 corruption and terminal projection witnesses in hosted CI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _workflow(filename: str) -> dict[str, Any]:
    return yaml.safe_load((ROOT / ".github" / "workflows" / filename).read_text(encoding="utf-8"))


def _commands(job: dict[str, Any]) -> str:
    return "\n".join(step["run"] for step in job["steps"] if isinstance(step.get("run"), str))


def test_terminal_get_task_witnesses_run_in_common_windows_ci() -> None:
    jobs = _workflow("ci.yml")["jobs"]
    command = _commands(jobs["windows-mcp"])

    assert "tests/core/test_a2a_task_routes.py" in command
    assert "tests/core/test_a2a_child_projection.py" in command


def test_resume_corruption_witnesses_run_in_engine_lifecycle_ci() -> None:
    jobs = _workflow("ci.yml")["jobs"]
    windows_command = _commands(jobs["windows-native-lifecycle"])
    native_command = _commands(jobs["native-lifecycle"])

    assert "test_a2a_resume_native_corruption.py" in windows_command
    assert "test_a2a_resume_receiver_receipt_corruption.py" in windows_command
    assert "test_a2a_resume_receiver_receipt_corruption.py" in native_command
    langgraph_step = next(
        step
        for step in jobs["native-lifecycle"]["steps"]
        if step.get("name") == "Verify LangGraph A2A native checkpoint corruption"
    )
    assert langgraph_step["if"] == "matrix.engine == 'langgraph'"
    assert "test_a2a_resume_native_corruption.py" in langgraph_step["run"]


def test_resume_corruption_witnesses_run_in_postgresql_lifecycle_ci() -> None:
    jobs = _workflow("postgres-ci.yml")["jobs"]
    adk_command = _commands(jobs["postgres-adk-lifecycle"])
    langgraph_command = _commands(jobs["postgres-langgraph-lifecycle"])

    assert "test_a2a_resume_receiver_receipt_corruption.py" in adk_command
    assert "test_a2a_resume_receiver_receipt_corruption.py" in langgraph_command
    assert "test_a2a_resume_native_corruption.py" in langgraph_command
