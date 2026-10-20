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
        ("LangGraph", "test_sandbox_effect_recovery.py", 23),
        ("ADK", "test_sandbox_effect_recovery.py", 23),
        ("LangGraph", "test_event_child_effect_recovery.py", 20),
        ("ADK", "test_event_child_effect_recovery.py", 20),
        ("LangGraph", "test_child_invocation_dependencies.py", 17),
        ("ADK", "test_child_invocation_dependencies.py", 17),
        ("LangGraph", "test_detached_children.py", 2),
        ("ADK", "test_detached_children.py", 2),
        ("LangGraph", "test_recursive_children.py", 3),
        ("ADK", "test_recursive_children.py", 3),
        ("LangGraph", "test_synchronous_child_waits.py", 29),
        ("ADK", "test_synchronous_child_waits.py", 29),
        ("LangGraph", "test_child_lifecycle_acceptance.py", 4),
        ("ADK", "test_child_lifecycle_acceptance.py", 4),
        ("LangGraph", "test_detached_child_process_recovery.py", 6),
        ("ADK", "test_detached_child_process_recovery.py", 6),
        ("LangGraph", "test_retry_error_context.py", 5),
        ("ADK", "test_retry_error_context.py", 5),
        ("LangGraph", "test_retry_timing.py", 11),
        ("ADK", "test_retry_timing.py", 11),
        ("LangGraph", "test_deadline_recovery.py", 8),
        ("ADK", "test_deadline_recovery.py", 8),
        ("LangGraph", "test_retry_schedule_recovery.py", 5),
        ("ADK", "test_retry_schedule_recovery.py", 5),
        ("LangGraph", "test_timing_process_recovery.py", 3),
        ("ADK", "test_timing_process_recovery.py", 3),
        ("LangGraph", "test_durable_events.py", 41),
        ("ADK", "test_durable_events.py", 41),
        ("LangGraph", "test_durable_event_process_recovery.py", 5),
        ("ADK", "test_durable_event_process_recovery.py", 5),
        ("LangGraph", "test_durable_timers.py", 13),
        ("ADK", "test_durable_timers.py", 13),
        ("LangGraph", "test_durable_timer_forks.py", 2),
        ("ADK", "test_durable_timer_forks.py", 2),
        ("LangGraph", "test_durable_timer_process_recovery.py", 5),
        ("ADK", "test_durable_timer_process_recovery.py", 5),
        ("LangGraph", "test_durable_scheduling.py", 8),
        ("ADK", "test_durable_scheduling.py", 8),
        ("LangGraph", "test_schedule_event_triggers.py", 14),
        ("ADK", "test_schedule_event_triggers.py", 14),
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


@pytest.mark.parametrize("engine", ["adk", "langgraph"])
@pytest.mark.parametrize(
    "fixture,expected",
    [
        ("test_a2a_workflow_calls.py", 17),
        ("test_a2a_sdk_inbound.py", 7),
        ("test_a2a_sdk_http_json_optional_methods.py", 1),
        ("test_a2a_sdk_malformed_streaming_admission.py", 2),
        ("test_a2a_task_routes.py", 2),
        ("test_a2a_resume_context_rejection.py", 8),
        ("test_a2a_initial_delivery.py", 7),
    ],
)
def test_actual_a2a_postgresql_lifecycle_gate_selects_all_principal_cases(
    engine, fixture, expected
):
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/postgres-ci.yml").read_text(encoding="utf-8")
    )
    job = workflow["jobs"][f"postgres-{engine}-lifecycle"]
    command = next(step["run"] for step in job["steps"] if "pytest" in step.get("run", ""))
    test_area = "core" if fixture == "test_a2a_task_routes.py" else "contract"
    assert f"../../tests/{test_area}/{fixture}" in command
    arguments = shlex.split(command)
    selector = arguments[arguments.index("-k") + 1]
    engine_selector = (
        f"({selector})"
        if fixture
        in {
            "test_a2a_sdk_http_json_optional_methods.py",
            "test_a2a_task_routes.py",
        }
        else f"({selector}) and {engine}"
    )
    collected = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(ROOT / "tests" / test_area / fixture),
            "--collect-only",
            "-q",
            "-k",
            engine_selector,
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
    assert all(
        "postgresql" in node
        and (
            fixture in {"test_a2a_sdk_http_json_optional_methods.py", "test_a2a_task_routes.py"}
            or engine in node.lower()
        )
        for node in nodes
    )


@pytest.mark.parametrize("engine", ["adk", "langgraph"])
def test_a2a06_postgresql_recovery_witnesses_are_selected_for_each_engine(engine):
    """Keep archived workflow, owner, and skill-authority A2A06 witnesses in PostgreSQL CI."""
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/postgres-ci.yml").read_text(encoding="utf-8")
    )
    job = workflow["jobs"][f"postgres-{engine}-lifecycle"]
    command = next(step["run"] for step in job["steps"] if "pytest" in step.get("run", ""))
    fixture = "test_workflow_version_recovery.py"
    assert f"../../tests/contract/{fixture}" in command
    arguments = shlex.split(command)
    selector = arguments[arguments.index("-k") + 1]
    selected = f"({selector}) and {engine}"
    collected = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(ROOT / "tests/contract" / fixture),
            "--collect-only",
            "-q",
            "-k",
            selected,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert collected.returncode == 0, collected.stdout + collected.stderr
    nodes = [line for line in collected.stdout.splitlines() if "::test_" in line]
    a2a06_nodes = [
        node
        for node in nodes
        if any(
            test_name in node
            for test_name in (
                "test_a2a_skill_resume_keeps_archived_workflow_after_reopen",
                "test_a2a_resume_rejects_changed_owner_authority_before_mutation",
            )
        )
    ]
    assert len(a2a06_nodes) == 6, collected.stdout
    assert (
        sum(
            "test_a2a_skill_resume_keeps_archived_workflow_after_reopen" in node
            for node in a2a06_nodes
        )
        == 2
    )
    assert (
        sum(
            "test_a2a_resume_rejects_changed_owner_authority_before_mutation" in node
            for node in a2a06_nodes
        )
        == 4
    )
    assert all("postgresql" in node and engine in node.lower() for node in a2a06_nodes)


def test_a2a_postgresql_process_race_witness_is_selected_in_postgres_ci():
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/postgres-ci.yml").read_text(encoding="utf-8")
    )
    job = workflow["jobs"]["postgres-adk-lifecycle"]
    command = next(step["run"] for step in job["steps"] if "pytest" in step.get("run", ""))
    fixture = "test_a2a_initial_delivery_process_race.py"
    assert f"../../tests/contract/{fixture}" in command
    collected = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(ROOT / "tests/contract" / fixture),
            "--collect-only",
            "-q",
            "-k",
            "postgresql",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert collected.returncode == 0, collected.stdout + collected.stderr
    nodes = [line for line in collected.stdout.splitlines() if "::test_" in line]
    assert len(nodes) == 3, collected.stdout
    assert any(
        "test_old_initial_delivery_process_cannot_write_after_takeover" in node for node in nodes
    )
    assert any(
        "test_competing_initial_delivery_process_cannot_dispatch_while_owner_is_live" in node
        for node in nodes
    )
    assert any(
        "test_simultaneous_initial_delivery_claimants_dispatch_once" in node for node in nodes
    )
    assert all("postgresql" in node for node in nodes)


@pytest.mark.parametrize(
    "fixture,expected",
    [
        ("test_initial_delivery.py", 13),
        ("test_a2a_card_admission.py", 26),
    ],
)
def test_actual_a2a_postgresql_common_gate_retains_all_backend_cases(fixture, expected):
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/postgres-ci.yml").read_text(encoding="utf-8")
    )
    job = workflow["jobs"]["postgres-core"]
    command = next(
        step["run"]
        for step in job["steps"]
        if step.get("name") == "Run isolated PostgreSQL store tests"
    )
    assert f"tests/core/{fixture}" in command
    collected = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(ROOT / "tests/core" / fixture),
            "--collect-only",
            "-q",
            "-k",
            "postgresql",
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
