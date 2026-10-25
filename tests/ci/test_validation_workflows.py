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
    assert "--cov-precision=2" in root_tests["run"]
    assert "--kill-after=30s 45m" in root_tests["run"]
    assert jobs["root"]["timeout-minutes"] == "60"
    assert jobs["engines"]["timeout-minutes"] == "50"
    assert "--kill-after=30s 45m" in _runs(jobs["engines"])
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
        "test_native_channel_process_recovery.py",
        "test_process_crash_recovery.py",
        "test_durable_timer_process_recovery.py",
        "test_durable_event_process_recovery.py",
        "test_detached_children.py",
    ):
        assert filename in windows
    assert jobs["windows-expressions"]["timeout-minutes"] == "20"
    assert jobs["windows-native"]["timeout-minutes"] == "20"
    assert jobs["windows-native-recovery"]["timeout-minutes"] == "35"
    assert jobs["windows-native-timing"]["timeout-minutes"] == "60"


def test_postgresql_profiles_run_independently_and_gate_both_images():
    jobs = _workflow("postgres-ci.yml")["jobs"]
    profiles = {
        "postgres-core",
        "postgres-langgraph",
        "postgres-adk",
        "postgres-agent-framework",
        "postgres-adk-lifecycle",
        "postgres-langgraph-lifecycle",
        "postgres-adk-operations",
        "postgres-langgraph-operations",
    }
    assert set(jobs["postgres-runtime"]["needs"]) == profiles
    assert jobs["postgres-runtime"]["strategy"]["fail-fast"] == "false"
    for name in profiles:
        assert "needs" not in jobs[name]
        assert jobs[name]["services"]["postgres"]["image"] == "postgres:16"
        assert jobs[name]["timeout-minutes"] == (
            "45"
            if name in {"postgres-adk", "postgres-langgraph"}
            else "35"
            if name == "postgres-adk-operations"
            else "25"
            if name == "postgres-langgraph-operations"
            else "20"
        )
    assert "--kill-after=30s 30m" in _runs(jobs["postgres-adk"])
    assert "--kill-after=30s 30m" in _runs(jobs["postgres-langgraph"])
    assert "../../tests/contract/test_native_channel_process_recovery.py" in _runs(
        jobs["postgres-langgraph"]
    )
    assert "--kill-after=30s 12m" in _runs(jobs["postgres-agent-framework"])
    assert "--kill-after=30s 30m" in _runs(jobs["postgres-adk-operations"])
    assert "--kill-after=30s 20m" in _runs(jobs["postgres-langgraph-operations"])


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


def test_lifecycle_native_profiles_keep_actual_receiver_and_cancellation_proofs():
    fixtures = {
        "test_external_operation_keys.py",
        "test_external_recovery_process.py",
        "test_external_recovery_native_corruption.py",
        "test_host_drain_child_controls.py",
        "test_explicit_compensation.py",
        "test_workflow_version_recovery.py",
        "test_a2a_resume_context_rejection.py",
        "test_workflow_version_process_recovery.py",
        "test_mcp_sdk_calls.py",
        "test_mcp_stdio_integration.py",
        "test_a2a_workflow_calls.py",
        "test_a2a_sdk_inbound.py",
        "test_a2a_sdk_http_json_optional_methods.py",
        "test_a2a_sdk_malformed_streaming_admission.py",
        "test_a2a_initial_delivery.py",
    }
    workflow = _workflow("postgres-ci.yml")
    for engine in ("adk", "langgraph"):
        job = workflow["jobs"][f"postgres-{engine}-lifecycle"]
        command = _runs(job)
        assert "--kill-after=30s 14m" in command
        assert f"--directory engines/{engine}" in command
        assert "--locked --exact --extra knowledge --extra postgres" in command
        assert "-k postgresql" in command
        assert all(f"../../tests/contract/{name}" in command for name in fixtures)
    process_race = "test_a2a_initial_delivery_process_race.py"
    windows = _workflow("ci.yml")["jobs"]["windows-native-lifecycle"]
    windows_command = _runs(windows)
    assert windows["runs-on"] == "windows-latest"
    assert f"../../tests/contract/{process_race}" in windows_command
    # Keep the actual-PostgreSQL process-race parameter selected on the ADK
    # job while adding the SQLite parameter to the Windows native lane.
    adk_postgres = _runs(workflow["jobs"]["postgres-adk-lifecycle"])
    assert f"../../tests/contract/{process_race}" in adk_postgres
    assert "-k postgresql" in adk_postgres
    core = _runs(workflow["jobs"]["postgres-core"])
    assert "tests/core/test_external_recovery.py" in core
    assert "tests/core/test_invocation_cancellation_controls.py" in core
    assert "tests/core/test_recovery_definitions.py" in core
    assert "tests/core/test_recovery_diagnostics.py" in core
    assert "tests/core/test_timer_archive_preflight.py" in core
    assert "tests/core/test_schedule_archive_preflight.py" in core
    assert "tests/core/test_initial_delivery.py" in core
    assert "tests/core/test_a2a_card_admission.py" in core
    paths = set(workflow["on"]["pull_request"]["paths"])
    assert {f"tests/contract/{name}" for name in fixtures} <= paths
    assert "tests/contract/_a2a_initial_delivery_race_worker.py" in paths
    windows = _workflow("ci.yml")["jobs"]["windows-native-lifecycle"]
    assert windows["timeout-minutes"] == "30" and "needs" not in windows
    command = next(step["run"] for step in windows["steps"] if "pytest" in step.get("run", ""))
    assert "\n" not in command
    assert "--with apsw==3.54.0.0" in command
    assert all(f"../../tests/contract/{name}" in command for name in fixtures)
    assert "../../tests/core/test_native_sqlite_inspection.py" in command

    native = _workflow("ci.yml")["jobs"]["native-lifecycle"]
    assert "needs" not in native and native["strategy"]["fail-fast"] == "false"
    assert {entry["engine"] for entry in native["strategy"]["matrix"]["include"]} == {
        "adk",
        "langgraph",
    }
    command = _runs(native)
    assert "--kill-after=30s 10m" in command and "--locked --extra knowledge" in command
    assert "--with apsw==3.54.0.0" in command
    assert all(f"../../tests/contract/{name}" in command for name in fixtures)
    assert "../../tests/contract/test_a2a_native_host_drain.py" in command
    assert "../../tests/core/test_native_sqlite_inspection.py" in command
    main = _runs(_workflow("ci.yml")["jobs"]["engines"])
    assert all(f"--ignore=../../tests/contract/{name}" in main for name in fixtures)


def test_windows_mcp_controls_include_real_transport_and_cleanup_boundaries():
    job = _workflow("ci.yml")["jobs"]["windows-mcp"]
    assert job["runs-on"] == "windows-latest" and job["timeout-minutes"] == "15"
    assert "needs" not in job
    command = _runs(job)
    for fixture in (
        "test_mcp_sdk_http.py",
        "test_async_authentication.py",
        "test_authentication_tls.py",
        "test_sandbox_stdio.py",
        "test_mcp_stdio_frames.py",
        "test_mcp_deployment_admission.py",
        "test_mcp_capabilities.py",
        "test_mcp_stdio_integration.py",
        "test_a2a_sdk_http.py",
        "test_a2a_credential_authority.py",
        "test_a2a_async.py",
        "test_a2a_inbound_v1.py",
        "test_a2a.py",
        "test_a2a_http_json_optional_methods.py",
        "test_a2a_card_admission.py",
        "test_a2a_skills.py",
        "test_initial_delivery.py",
    ):
        assert fixture in command
    assert "test_native_sqlite_inspection.py" not in command
    assert "tests/contract/test_a2a_sdk_http_json_optional_methods.py" in command
    assert (
        "tests/core/test_a2a_streaming.py::test_stream_skips_malformed_event_and_closes_after_authoritative_terminal"
        in command
    )
    assert "uv run --locked pytest -vv" in command


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
    assert workflow["jobs"]["native"]["timeout-minutes"] == "50"
    commands = _runs(workflow["jobs"]["native"])
    assert "--kill-after=30s 40m" in commands
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
