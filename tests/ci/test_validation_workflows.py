"""Preserve backend coverage and exact-source release gates during CI cleanup."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github/workflows"


def _workflow(name):
    # BaseLoader preserves GitHub's YAML 1.2 `on` instead of YAML 1.1 booleans.
    return yaml.load((WORKFLOWS / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _runs(job):
    return "\n".join(step.get("run", "") for step in job["steps"])


def test_feature_push_does_not_duplicate_pr_validation_or_remove_main_acceptance():
    for name in ("ci.yml", "security.yml"):
        events = _workflow(name)["on"]
        assert events["push"]["branches"] == ["main"]
        assert "pull_request" in events
        assert "workflow_dispatch" in events
    security = _workflow("security.yml")
    assert security["on"]["schedule"] == [{"cron": "0 6 * * 1"}]
    profiles = security["jobs"]["dependencies"]["strategy"]["matrix"]["include"]
    assert len(profiles) == 7
    assert {profile["project"] for profile in profiles} == {
        ".",
        "core",
        "engines/adk",
        "engines/langgraph",
        "engines/agent-framework",
        "sandbox-controller",
        "kubernetes-sandbox-controller",
    }
    for name in ("postgres-ci.yml", "external-sandbox-ci.yml"):
        events = _workflow(name)["on"]
        assert events["push"]["branches"] == ["main"]
        assert "paths" not in events["push"]


def test_root_and_both_principal_environments_keep_full_shared_coverage():
    jobs = _workflow("ci.yml")["jobs"]
    root_tests = next(step for step in jobs["root"]["steps"] if step.get("name") == "Tests")
    assert "pytest tests -vv" in root_tests["run"]
    assert "--cov-fail-under=90" in root_tests["run"]
    engines = jobs["engines"]
    assert {entry["engine"] for entry in engines["strategy"]["matrix"]["include"]} == {
        "adk",
        "langgraph",
    }
    commands = _runs(engines)
    assert "../../tests/contract" in commands and "../../tests/ctk" in commands
    assert "--locked" in commands
    assert "--iterations 10 --concurrency 4" in commands
    assert "test_expressions.py" in _runs(jobs["windows-expressions"])
    windows = "\n".join(
        _runs(jobs[name])
        for name in ("windows-native", "windows-native-recovery", "windows-native-timing")
    )
    for filename in (
        "test_native_task_graph.py",
        "test_process_crash_recovery.py",
        "test_durable_timer_process_recovery.py",
        "test_durable_event_process_recovery.py",
        "test_detached_children.py",
    ):
        assert filename in windows
    assert jobs["windows-expressions"]["timeout-minutes"] == "20"
    assert jobs["windows-native"]["timeout-minutes"] == "20"
    assert jobs["windows-native-recovery"]["timeout-minutes"] == "20"
    assert jobs["windows-native-timing"]["timeout-minutes"] == "20"


def test_postgresql_profiles_run_independently_and_gate_both_images():
    jobs = _workflow("postgres-ci.yml")["jobs"]
    profiles = {"postgres-core", "postgres-langgraph", "postgres-adk", "postgres-agent-framework"}
    assert set(jobs["postgres-runtime"]["needs"]) == profiles
    assert jobs["postgres-runtime"]["strategy"]["fail-fast"] == "false"
    for name in profiles:
        assert "needs" not in jobs[name]
        assert jobs[name]["services"]["postgres"]["image"] == "postgres:16"
        assert jobs[name]["timeout-minutes"] == "20"
    assert "--kill-after=30s 14m" in _runs(jobs["postgres-adk"])
    assert "--kill-after=30s 14m" in _runs(jobs["postgres-langgraph"])


def test_windows_timing_folded_command_passes_all_fixtures_to_pytest():
    job = _workflow("ci.yml")["jobs"]["windows-native-timing"]
    command = next(
        step["run"]
        for step in job["steps"]
        if step.get("name") == "Verify Windows native timers events and scheduling"
    )
    # A more-indented YAML line retains a newline in a folded scalar. On
    # PowerShell a following .py path becomes a second script launch, rather
    # than a pytest argument, and can wait for file association indefinitely.
    assert "\n" not in command
    assert "../../tests/contract/test_child_lifecycle_acceptance.py" in command
    assert "../../tests/contract/test_event_backend_admission.py" in command


def test_independent_principal_jobs_finish_both_profiles_before_workflow_success():
    workflow = _workflow("ci.yml")
    for name in ("engines", "containers"):
        job = workflow["jobs"][name]
        assert "needs" not in job
        assert job["strategy"]["fail-fast"] == "false"
        assert "needs." not in _runs(job)
        assert all("download-artifact" not in step.get("uses", "") for step in job["steps"])
    release = _workflow("release.yml")
    assert "github.event.workflow_run.conclusion == 'success'" in release["jobs"]["prepare"]["if"]
    assert "github.event.workflow_run.event == 'push'" in release["jobs"]["prepare"]["if"]


def test_optional_framework_is_checked_when_common_semantics_or_fixtures_change():
    workflow = _workflow("agent-framework-ci.yml")
    for event in ("push", "pull_request"):
        paths = set(workflow["on"][event]["paths"])
        assert {"core/**", "tests/contract/**", "tests/ctk/**", "tests/engine_cases.py"} <= paths
    assert "workflow_dispatch" in workflow["on"]
    commands = _runs(workflow["jobs"]["native"])
    assert "--locked" in commands and "--extra native" in commands
    assert "../../tests/agent_framework" in commands and "../../tests/contract" in commands


def test_stale_pr_cancellation_does_not_cancel_main_acceptance():
    for name in (
        "ci.yml",
        "postgres-ci.yml",
        "external-sandbox-ci.yml",
        "agent-framework-ci.yml",
        "docs.yml",
        "security.yml",
    ):
        workflow = _workflow(name)
        assert workflow["permissions"] == {"contents": "read"}
        assert workflow["concurrency"]["cancel-in-progress"] == (
            "${{ github.ref != 'refs/heads/main' }}"
        )
    release = _workflow("release.yml")
    assert release["concurrency"]["cancel-in-progress"] == "false"
    assert release["concurrency"]["group"] == "release-main"
