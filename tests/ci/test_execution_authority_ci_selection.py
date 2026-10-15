"""Keep original-authority and credential fixtures in required platform/backend jobs."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CORE = (
    "test_execution_authority.py",
    "test_execution_authorization.py",
    "test_execution_context_isolation.py",
    "test_memory_namespaces.py",
    "test_credential_projection.py",
    "test_api_execution_authority.py",
    "test_authenticated_catalog_metadata.py",
)
CONTRACT = (
    "test_execution_authority_isolation.py",
    "test_execution_authority_process_recovery.py",
)


def workflow(name):
    return yaml.load(
        (ROOT / ".github/workflows" / name).read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )


def command(job):
    return "\n".join(step.get("run", "") for step in job["steps"])


def test_windows_original_authority_uses_existing_native_job():
    job = workflow("ci.yml")["jobs"]["windows-native-timing"]
    assert job["runs-on"] == "windows-latest" and "needs" not in job
    assert job["timeout-minutes"] == "45"
    text = command(job)
    assert "--directory engines/langgraph --locked --extra sqlite" in text
    assert all(f"../../tests/core/{name}" in text for name in CORE)
    assert all(f"../../tests/contract/{name}" in text for name in CONTRACT)


def test_original_authority_postgresql_profiles_gate_images():
    jobs = workflow("postgres-ci.yml")["jobs"]
    common = command(jobs["postgres-core"])
    assert all(f"tests/core/{name}" in common for name in CORE)
    for engine in ("adk", "langgraph"):
        name = f"postgres-{engine}-operations"
        job = jobs[name]
        assert name in jobs["postgres-runtime"]["needs"] and "needs" not in job
        assert job["services"]["postgres"]["image"] == "postgres:16"
        shell_timeout = "25m" if engine == "adk" else "20m"
        job_timeout = "30" if engine == "adk" else "25"
        assert job["timeout-minutes"] == job_timeout
        text = command(job)
        assert f"--directory engines/{engine}" in text
        assert "--locked --exact --extra knowledge --extra postgres" in text
        assert f"--kill-after=30s {shell_timeout}" in text and "-k postgresql" in text
        assert all(f"../../tests/contract/{name}" in text for name in CONTRACT)


def test_authority_credential_and_process_changes_trigger_postgresql_gates():
    paths = workflow("postgres-ci.yml")["on"]["pull_request"]["paths"]
    assert all(
        value in paths
        for value in (
            "core/src/open_workflow_agent/execution_author*.py",
            "core/src/open_workflow_agent/execution_context.py",
            "core/src/open_workflow_agent/credential_guard.py",
            "tests/core/test_execution_author*.py",
            "tests/core/test_credential_projection.py",
            "tests/core/test_authenticated_catalog_metadata.py",
            "tests/contract/test_execution_author*.py",
            "tests/contract/_execution_authority_recovery_worker.py",
        )
    )
