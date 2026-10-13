"""Keep real backend/platform operation witnesses mandatory after authoring."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = (
    "test_openapi_calls.py",
    "test_openapi_process_recovery.py",
    "test_grpc_calls.py",
    "test_grpc_process_recovery.py",
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
        assert "-k postgresql" in text and "--kill-after=30s 20m" in text
        assert all(f"../../tests/contract/{name}" in text for name in CONTRACTS)
    common = command(jobs["postgres-core"])
    assert all(
        f"tests/core/{name}" in common
        for name in (
            "test_openapi_admission.py",
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
