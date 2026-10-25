"""Required real-store and platform coverage for optional diagnostic history."""

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]


def workflow(name: str) -> dict[str, Any]:
    value = yaml.load((ROOT / ".github/workflows" / name).read_text(), Loader=yaml.BaseLoader)
    assert isinstance(value, dict)
    return value


def commands(job: dict[str, Any]) -> str:
    return "\n".join(step.get("run", "") for step in job["steps"])


def test_history_shared_postgresql_and_principal_native_selections_are_required() -> None:
    value = workflow("postgres-ci.yml")
    paths = value["on"]["pull_request"]["paths"]
    for path in (
        "tests/core/test_lifecycle_history.py",
        "tests/_lifecycle_history_writer.py",
        "tests/contract/test_lifecycle_history_contract.py",
        "tests/contract/test_traced_history_process_recovery.py",
        "tests/contract/_traced_history_recovery_worker.py",
    ):
        assert path in paths
    jobs = value["jobs"]
    common = next(
        job for job in jobs.values() if job.get("name") == "PostgreSQL common-store integration"
    )
    assert "tests/core/test_lifecycle_history.py" in commands(common)
    for engine in ("adk", "langgraph"):
        job = jobs[f"postgres-{engine}-operations"]
        assert "../../tests/contract/test_lifecycle_history_contract.py" in commands(job)
        assert "../../tests/contract/test_traced_history_process_recovery.py" in commands(job)
        assert "-k postgresql" in commands(job) and "--extra postgres" in commands(job)
        assert job["services"]["postgres"]["image"] == "postgres:16"


def test_history_windows_sqlite_uses_both_actual_engine_matrix_cells() -> None:
    job = workflow("ci.yml")["jobs"]["windows-grpc-sqlite-matrix"]
    assert "../../tests/contract/test_lifecycle_history_contract.py" in commands(job)
    assert "../../tests/contract/test_traced_history_process_recovery.py" in commands(job)
    assert "-k sqlite" in commands(job)
    assert {row["engine"] for row in job["strategy"]["matrix"]["include"]} == {"adk", "langgraph"}
