"""Staged adapter proofs stay in existing platform and actual database gates."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CORE = (
    "test_bounded_memory.py",
    "test_provider_model_spi.py",
    "test_provider_service_composition.py",
    "test_event_transport.py",
)
CONTRACT = ("test_selected_adapter_contracts.py", "test_selected_adapter_process_recovery.py")


def workflow(name):
    return yaml.load(
        (ROOT / ".github/workflows" / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader
    )


def commands(job):
    return "\n".join(step.get("run", "") for step in job["steps"])


def test_selected_adapters_reuse_bounded_windows_core_and_real_native_jobs():
    jobs = workflow("ci.yml")["jobs"]
    core = jobs["windows-expressions"]
    native = jobs["windows-native"]
    assert core["runs-on"] == native["runs-on"] == "windows-latest"
    assert core["timeout-minutes"] == native["timeout-minutes"] == "20"
    assert all(f"tests/core/{name}" in commands(core) for name in CORE)
    text = commands(native)
    assert "--directory engines/langgraph" in text and "--locked --extra sqlite" in text
    assert all(f"../../tests/contract/{name}" in text for name in CONTRACT)
    # A blank line inside a folded scalar would split the PowerShell command.
    step = next(
        step
        for step in native["steps"]
        if "test_selected_adapter_contracts.py" in step.get("run", "")
    )
    assert "\n" not in step["run"]


def test_selected_adapters_gate_actual_postgresql_before_runtime_acceptance():
    jobs = workflow("postgres-ci.yml")["jobs"]
    core = jobs["postgres-core"]
    assert core["services"]["postgres"]["image"] == "postgres:16"
    assert all(f"tests/core/{name}" in commands(core) for name in CORE)
    for engine in ("adk", "langgraph"):
        name = f"postgres-{engine}-operations"
        job = jobs[name]
        assert name in jobs["postgres-runtime"]["needs"]
        assert job["services"]["postgres"]["image"] == "postgres:16"
        assert job["timeout-minutes"] == "25"
        text = commands(job)
        assert f"--directory engines/{engine}" in text
        assert "--locked --exact --extra knowledge --extra postgres" in text
        assert "20m" in text and "-k postgresql" in text
        assert all(f"../../tests/contract/{fixture}" in text for fixture in CONTRACT)
        step = next(
            step
            for step in job["steps"]
            if "test_selected_adapter_contracts.py" in step.get("run", "")
        )
        assert "OWA_TEST_POSTGRES_URL" in step["env"]
    paths = workflow("postgres-ci.yml")["on"]["pull_request"]["paths"]
    assert all(
        path in paths
        for path in (
            "core/src/open_workflow_agent/bounded_memory.py",
            "core/src/open_workflow_agent/memory.py",
            "core/src/open_workflow_agent/model_responses.py",
            "core/src/open_workflow_agent/catalog.py",
            "core/src/open_workflow_agent/event_transport.py",
            "tests/contract/test_selected_adapter*.py",
            "tests/contract/_selected_adapter*.py",
            "tests/ci/test_selected_adapter_ci_selection.py",
        )
    )
    assert all(f"tests/core/{fixture}" in paths for fixture in CORE)


def test_selected_native_fixtures_remain_in_both_full_engine_suites():
    job = workflow("ci.yml")["jobs"]["engines"]
    text = commands(job)
    assert "../../tests/${{ matrix.test_dir }} ../../tests/contract" in text
    for fixture in CONTRACT:
        assert f"--ignore=../../tests/contract/{fixture}" not in text
    # The common fixture still requires actual SDKs and backend truth; a CI
    # selector must not turn a missing SDK or database into a passing oracle.
    source = (ROOT / "tests/contract/_selected_adapter_fixtures.py").read_text(encoding="utf-8")
    assert "from test_execution_authority_isolation import" in source
    assert (
        "services.effect_journal.connection.kind == services.invocations.connection.kind == backend"
        in source
    )
    shared = (ROOT / "tests/contract/test_execution_authority_isolation.py").read_text(
        encoding="utf-8"
    )
    assert "def actual_native(" in shared
    assert "OWA_TEST_POSTGRES_URL" in shared
