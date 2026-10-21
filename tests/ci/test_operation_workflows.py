"""Keep real backend/platform operation witnesses mandatory after authoring."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = (
    "test_openapi_calls.py",
    "test_openapi_process_recovery.py",
    "test_grpc_calls.py",
    "test_grpc_process_recovery.py",
    "test_executable_source_calls.py",
    "test_executable_source_process_recovery.py",
)


def workflow(name):
    return yaml.load(
        (ROOT / ".github/workflows" / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader
    )


def command(job):
    return "\n".join(step.get("run", "") for step in job["steps"])


def test_isolated_principal_postgresql_operation_profiles_gate_images():
    jobs = workflow("postgres-ci.yml")["jobs"]
    for engine in ("adk", "langgraph"):
        name = f"postgres-{engine}-operations"
        job = jobs[name]
        text = command(job)
        assert name in jobs["postgres-runtime"]["needs"] and "needs" not in job
        assert job["services"]["postgres"]["image"] == "postgres:16"
        assert f"--directory engines/{engine}" in text
        assert "--locked --exact --extra knowledge --extra postgres" in text
        shell_timeout = "25m" if engine == "adk" else "20m"
        job_timeout = "30" if engine == "adk" else "25"
        assert "-k postgresql" in text and f"--kill-after=30s {shell_timeout}" in text
        assert all(f"../../tests/contract/{name}" in text for name in CONTRACTS)
        assert "../../tests/core/test_executable_source_admission.py" in text
        assert job["timeout-minutes"] == job_timeout
    common = command(jobs["postgres-core"])
    assert all(
        f"tests/core/{name}" in common
        for name in (
            "test_openapi_admission.py",
            "test_executable_source_admission.py",
            "test_sandbox_jobs.py",
            "test_sandbox_job_dispatcher.py",
            "test_background_job_runtime.py",
        )
    )


def test_windows_requires_real_native_operations_and_supervisor_mutation_controls():
    job = workflow("ci.yml")["jobs"]["windows-operation-adapters"]
    assert job["runs-on"] == "windows-latest" and "needs" not in job
    tests = next(step["run"] for step in job["steps"] if "pytest" in step.get("run", ""))
    assert "\n" not in tests and "--directory engines/langgraph --locked --extra sqlite" in tests
    assert all(f"../../tests/contract/{name}" in tests for name in CONTRACTS)
    assert "../../tests/contract/test_background_job_recovery.py" in tests
    assert "../../tests/core/test_sandbox_job_mutation_fence.py" in tests
    assert "../../tests/core/test_internal_sandbox_jobs.py" in tests
    assert "../../tests/core/test_background_request_admission.py" in tests

    assert all(
        f"../../tests/core/{name}" in tests
        for name in (
            "test_executable_sources.py",
            "test_executable_source_plan.py",
            "test_executable_source_imports.py",
            "test_executable_source_admission.py",
            "test_internal_awaited_process_controls.py",
            "test_engine_shutdown_initialization.py",
        )
    )
    assert job["timeout-minutes"] == "40"


def test_windows_grpc_sqlite_matrix_requires_each_native_engine_and_only_sqlite_cases():
    job = workflow("ci.yml")["jobs"]["windows-grpc-sqlite-matrix"]
    assert job["runs-on"] == "windows-latest" and job["timeout-minutes"] == "25"
    assert job["strategy"]["fail-fast"] == "false"
    assert job["strategy"]["matrix"]["include"] == [
        {"engine": "adk", "extra": "native"},
        {"engine": "langgraph", "extra": "sqlite"},
    ]
    assert job["env"]["OWA_TEST_REQUIRED_ENGINE"] == "${{ matrix.engine }}"
    tests = next(step["run"] for step in job["steps"] if "pytest" in step.get("run", ""))
    assert "--directory engines/${{ matrix.engine }} --locked --extra ${{ matrix.extra }}" in tests
    assert all(
        f"../../tests/contract/{name}" in tests
        for name in ("test_grpc_calls.py", "test_grpc_process_recovery.py")
    )
    assert "-k sqlite" in tests
    assert "OWA_TEST_REQUIRED_ENGINE" in (ROOT / "tests/engine_cases.py").read_text(
        encoding="utf-8"
    )
    assert "required native" in (ROOT / "tests/contract/test_grpc_calls.py").read_text(
        encoding="utf-8"
    )
    assert "required native" in (ROOT / "tests/contract/test_grpc_process_recovery.py").read_text(
        encoding="utf-8"
    )


def test_windows_background_sqlite_matrix_requires_each_native_engine():
    job = workflow("ci.yml")["jobs"]["windows-background-sqlite-matrix"]
    assert job["runs-on"] == "windows-latest" and job["timeout-minutes"] == "30"
    assert job["strategy"]["fail-fast"] == "false"
    assert job["strategy"]["matrix"]["include"] == [
        {"engine": "adk", "extra": "native"},
        {"engine": "langgraph", "extra": "sqlite"},
    ]
    assert job["env"]["OWA_TEST_REQUIRED_ENGINE"] == "${{ matrix.engine }}"
    tests = next(step["run"] for step in job["steps"] if "pytest" in step.get("run", ""))
    assert "--directory engines/${{ matrix.engine }} --locked --extra ${{ matrix.extra }}" in tests
    assert "../../tests/contract/test_background_job_recovery.py" in tests
    source = (ROOT / "tests/contract/test_background_job_recovery.py").read_text(encoding="utf-8")
    assert "required native {engine_name} SDK is unavailable" in source
    assert 'os.getenv("OWA_TEST_REQUIRED_ENGINE") == engine_name' in source


def test_source_changes_trigger_required_postgresql_qualification():
    paths = workflow("postgres-ci.yml")["on"]["pull_request"]["paths"]
    assert all(
        path in paths
        for path in (
            "core/src/open_workflow_agent/executable_source*.py",
            "tests/core/test_executable_source*.py",
            "tests/core/test_internal_awaited_process_controls.py",
            "tests/contract/test_executable_source*.py",
            "tests/contract/_executable_source*.py",
        )
    )
