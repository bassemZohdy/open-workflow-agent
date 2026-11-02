"""Native Send needs actual SDK platform and durable-store recovery cells."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SELECTOR = "../../tests/langgraph/test_native_send.py"


def test_native_send_windows_and_postgresql_selections_remain_required():
    for filename, job_name in (
        ("ci.yml", "windows-native"),
        ("postgres-ci.yml", "postgres-langgraph"),
    ):
        workflow = yaml.load(
            (ROOT / ".github/workflows" / filename).read_text(), Loader=yaml.BaseLoader
        )
        job = workflow["jobs"][job_name]
        commands = "\n".join(step.get("run", "") for step in job["steps"])
        assert SELECTOR in commands
        if filename == "postgres-ci.yml":
            assert (
                "-k 'postgresql and (langgraph or completed_native_scope_dependencies)'" in commands
            )
            assert "--extra postgres" in commands
            assert "tests/langgraph/test_native_send.py" in workflow["on"]["pull_request"]["paths"]
            assert job["services"]["postgres"]["image"] == "postgres:16"
            native_step = next(step for step in job["steps"] if SELECTOR in step.get("run", ""))
            assert native_step["env"]["OWA_TEST_REQUIRED_ENGINE"] == "langgraph"
        else:
            assert job["runs-on"] == "windows-latest" and "--extra sqlite" in commands


def test_send_lineage_has_a_bounded_cross_backend_postgres_job():
    workflow = yaml.load(
        (ROOT / ".github/workflows/postgres-ci.yml").read_text(), Loader=yaml.BaseLoader
    )
    job = workflow["jobs"]["postgres-native-send-lineage"]
    step = next(
        step
        for step in job["steps"]
        if step.get("name") == "Verify Send lineage on SQLite and PostgreSQL"
    )
    assert job["services"]["postgres"]["image"] == "postgres:16"
    assert job["timeout-minutes"] == "40"
    assert "OWA_TEST_POSTGRES_URL" in step["env"]
    assert "timeout --signal=TERM --kill-after=30s 30m" in step["run"]
    assert "../../tests/langgraph/test_native_lineage_send.py" in step["run"]
    assert "--extra postgres" in step["run"] and "--extra sqlite" in step["run"]
    assert "tests/langgraph/test_native_lineage_send.py" in workflow["on"]["pull_request"]["paths"]


def test_lineage_state_controls_have_a_separate_cross_backend_job():
    workflow = yaml.load(
        (ROOT / ".github/workflows/postgres-ci.yml").read_text(), Loader=yaml.BaseLoader
    )
    job = workflow["jobs"]["postgres-native-lineage-controls"]
    step = next(
        step
        for step in job["steps"]
        if step.get("name") == "Verify lineage state controls on SQLite and PostgreSQL"
    )
    assert job["services"]["postgres"]["image"] == "postgres:16"
    assert job["timeout-minutes"] == "45"
    assert "OWA_TEST_POSTGRES_URL" in step["env"]
    assert "timeout --signal=TERM --kill-after=30s 35m" in step["run"]
    assert "../../tests/langgraph/test_native_lineage_state_updates.py" in step["run"]
    assert "test_unsupported_required_extensions_do_not_reserve_a_target" in step["run"]
    assert "--extra postgres" in step["run"] and "--extra sqlite" in step["run"]
    paths = workflow["on"]["pull_request"]["paths"]
    assert "tests/langgraph/test_native_lineage_state_updates.py" in paths
    assert "tests/langgraph/lineage_target_update_worker.py" in paths
    assert "tests/langgraph/test_native_lineage_preflight.py" in paths
