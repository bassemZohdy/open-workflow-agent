"""Use actual CI selectors against actual collected recovery cases."""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "engine,fixture,expected",
    [
        ("LangGraph", "test_native_scope_dependencies.py", 9),
        ("LangGraph", "test_sandbox_effect_recovery.py", 18),
        ("ADK", "test_sandbox_effect_recovery.py", 18),
        ("LangGraph", "test_event_child_effect_recovery.py", 20),
        ("ADK", "test_event_child_effect_recovery.py", 20),
    ],
)
def test_actual_postgresql_gate_selects_required_recovery_cases(
    engine: str, fixture: str, expected: int
) -> None:
    workflow: dict[str, Any] = yaml.safe_load(
        (ROOT / ".github/workflows/postgres-ci.yml").read_text(encoding="utf-8")
    )
    step = next(
        step
        for job in workflow["jobs"].values()
        for step in job["steps"]
        if step.get("name")
        == f"Verify competitive fork and invocation recovery on native {engine} with PostgreSQL"
    )
    assert f"../../tests/contract/{fixture}" in step["run"]
    command = shlex.split(step["run"])
    selector = command[command.index("-k") + 1]
    collected = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(ROOT / "tests/contract" / fixture),
            "--collect-only",
            "-q",
            "-k",
            selector,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert collected.returncode == 0, collected.stdout + collected.stderr
    nodes = [line for line in collected.stdout.splitlines() if "::test_" in line]
    assert len(nodes) == expected, collected.stdout
    assert all("postgresql" in node for node in nodes)
