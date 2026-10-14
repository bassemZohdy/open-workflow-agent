"""Preserve protected approval backend/platform witnesses in mandatory CI jobs."""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = (
    "test_protected_approval_workflows.py",
    "test_protected_approval_process_recovery.py",
)
CORE = (
    "test_protected_approvals.py",
    "test_approval_dispatcher.py",
    "test_protected_approval_recovery.py",
)


def workflow(name):
    return yaml.load(
        (ROOT / ".github/workflows" / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader
    )


def command(job):
    return next(step["run"] for step in job["steps"] if "pytest" in step.get("run", ""))


def test_windows_protected_approvals_use_existing_native_timing_job_and_budget():
    job = workflow("ci.yml")["jobs"]["windows-native-timing"]
    assert job["runs-on"] == "windows-latest" and "needs" not in job
    assert job["timeout-minutes"] == "35"
    text = command(job)
    assert "\n" not in text
    assert "--directory engines/langgraph --locked --extra sqlite" in text
    assert all(f"../../tests/contract/{filename}" in text for filename in CONTRACTS)
    assert all(f"../../tests/core/{filename}" in text for filename in CORE)


@pytest.mark.parametrize("engine", ["adk", "langgraph"])
def test_postgresql_approval_operations_profiles_remain_isolated_and_gate_images(engine):
    source = workflow("postgres-ci.yml")
    jobs = source["jobs"]
    name = f"postgres-{engine}-operations"
    job = jobs[name]
    assert name in jobs["postgres-runtime"]["needs"] and "needs" not in job
    assert job["timeout-minutes"] == "25"
    assert job["services"]["postgres"]["image"] == "postgres:16"
    step = next(step for step in job["steps"] if "pytest" in step.get("run", ""))
    text = step["run"]
    assert step["env"]["OWA_TEST_POSTGRES_URL"].startswith("postgresql://")
    assert "--kill-after=30s 20m" in text
    assert (
        f"--directory engines/{engine} --locked --exact --extra knowledge --extra postgres" in text
    )
    assert "-k postgresql" in text
    assert all(f"../../tests/contract/{filename}" in text for filename in CONTRACTS)
    paths = source["on"]["pull_request"]["paths"]
    assert "core/src/open_workflow_agent/protected_approval*.py" in paths
    assert "core/src/open_workflow_agent/approval_dispatcher.py" in paths
    assert "tests/contract/_protected_approval*.py" in paths


def test_postgresql_common_approval_store_and_observer_guards_gate_runtime_images():
    jobs = workflow("postgres-ci.yml")["jobs"]
    job = jobs["postgres-core"]
    assert "postgres-core" in jobs["postgres-runtime"]["needs"]
    assert job["timeout-minutes"] == "20"
    text = command(job)
    assert "--kill-after=30s 12m" in text
    assert all(f"tests/core/{filename}" in text for filename in CORE)


def collected(filename, selector=None):
    arguments = [sys.executable, "-m", "pytest", str(ROOT / filename), "--collect-only", "-q"]
    if selector is not None:
        arguments.extend(["-k", selector])
    result = subprocess.run(
        arguments, cwd=ROOT, capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return [line for line in result.stdout.splitlines() if "::test_" in line], result.stdout


@pytest.mark.parametrize("engine", ["adk", "langgraph"])
@pytest.mark.parametrize(
    "filename,expected",
    [
        ("test_protected_approval_workflows.py", 14),
        ("test_protected_approval_process_recovery.py", 6),
    ],
)
def test_actual_postgresql_selectors_retain_twenty_approval_nodes_per_principal(
    engine, filename, expected
):
    text = command(workflow("postgres-ci.yml")["jobs"][f"postgres-{engine}-operations"])
    assert f"../../tests/contract/{filename}" in text
    arguments = shlex.split(text)
    selector = arguments[arguments.index("-k") + 1]
    nodes, output = collected(f"tests/contract/{filename}", f"({selector}) and {engine}")
    assert len(nodes) == expected, output
    assert all("postgresql" in node and engine in node.lower() for node in nodes)


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("test_protected_approvals.py", 36),
        ("test_approval_dispatcher.py", 15),
        ("test_protected_approval_recovery.py", 5),
    ],
)
def test_actual_common_approval_selector_retains_authored_finite_grid(filename, expected):
    text = command(workflow("postgres-ci.yml")["jobs"]["postgres-core"])
    assert f"tests/core/{filename}" in text
    nodes, output = collected(f"tests/core/{filename}")
    assert len(nodes) == expected, output
