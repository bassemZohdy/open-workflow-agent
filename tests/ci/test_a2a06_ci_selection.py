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


def _step(job: dict[str, Any], name: str) -> dict[str, Any]:
    return next(step for step in job["steps"] if step.get("name") == name)


def test_terminal_get_task_witnesses_run_in_common_windows_ci() -> None:
    jobs = _workflow("ci.yml")["jobs"]
    command = _commands(jobs["windows-mcp"])

    assert "tests/core/test_a2a_task_routes.py" in command
    assert "tests/core/test_a2a_child_projection.py" in command


def test_resume_corruption_witnesses_run_in_engine_lifecycle_ci() -> None:
    jobs = _workflow("ci.yml")["jobs"]
    windows_command = _commands(jobs["windows-native-lifecycle"])
    native_command = _commands(jobs["native-lifecycle"])

    assert "test_workflow_version_recovery.py" in windows_command
    assert "test_a2a_resume_native_corruption.py" in windows_command
    assert "test_a2a_resume_receiver_receipt_corruption.py" in windows_command
    assert "test_workflow_version_recovery.py" in native_command
    assert "test_a2a_resume_receiver_receipt_corruption.py" in native_command
    langgraph_step = _step(
        jobs["native-lifecycle"], "Verify LangGraph A2A native checkpoint corruption"
    )
    assert langgraph_step["if"] == "matrix.engine == 'langgraph'"
    assert "test_a2a_resume_native_corruption.py" in langgraph_step["run"]
    adk_step = _step(jobs["native-lifecycle"], "Verify ADK A2A native event corruption")
    assert adk_step["if"] == "matrix.engine == 'adk'"
    assert "test_a2a_resume_adk_native_corruption.py" in adk_step["run"]


def test_resume_corruption_witnesses_run_in_postgresql_lifecycle_ci() -> None:
    jobs = _workflow("postgres-ci.yml")["jobs"]
    for engine in ("adk", "langgraph"):
        job = jobs[f"postgres-{engine}-lifecycle"]
        lifecycle_step = _step(
            job, "Verify native PostgreSQL lifecycle and trusted receiver recovery"
        )
        command = lifecycle_step["run"]

        assert "test_workflow_version_recovery.py" in command
        assert "test_a2a_resume_receiver_receipt_corruption.py" in command
        assert f"--directory engines/{engine}" in command
        assert "--extra postgres" in command
        assert "-k postgresql" in command
        assert lifecycle_step.get("env", {}).get("OWA_TEST_POSTGRES_URL")

    langgraph_command = _commands(jobs["postgres-langgraph-lifecycle"])
    adk_command = _commands(jobs["postgres-adk-lifecycle"])
    assert "test_a2a_resume_adk_native_corruption.py" in adk_command
    assert "test_a2a_resume_adk_native_corruption.py" not in langgraph_command
    assert "test_a2a_resume_native_corruption.py" in langgraph_command
