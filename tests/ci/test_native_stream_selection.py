"""The native stream profile must run its actual cross-store acceptance tests."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_postgres_job_runs_stream_profiles_on_both_stores_without_skips():
    workflow = yaml.load(
        (ROOT / ".github/workflows/postgres-ci.yml").read_text(), Loader=yaml.BaseLoader
    )
    job = workflow["jobs"]["postgres-native-stream"]
    assert job["services"]["postgres"]["image"] == "postgres:16"
    assert job["runs-on"] == "ubuntu-latest"
    assert job["timeout-minutes"] == "20"
    step = next(
        item
        for item in job["steps"]
        if item.get("name") == "Verify native stream profiles on both stores"
    )
    assert "OWA_TEST_POSTGRES_URL" in step["env"]
    command = step["run"]
    assert "--extra postgres" in command and "--extra sqlite" in command
    assert "timeout --signal=TERM --kill-after=30s 15m" in command
    assert "--junitxml=" in command and 'get("skipped", "0")' in command
    assert "test_task_stream_observes_replay_target_without_repeating_retained_effects" in command
    assert "test_task_stream_remains_available_during_native_send_fanout" in command
    assert "test_checkpoint_stream_references_root_history_only_with_opt_in" in command
    assert "test_checkpoint_stream_fails_admission_when_dependencies_are_missing" in command
    assert "test_checkpoint_reference_does_not_grant_state_inspection" in command
    assert "test_debug_stream_projects_timestamped_task_and_root_checkpoint_events" in command
    assert "test_debug_stream_requires_base_profile_before_invocation" in command
    assert "test_custom_observation_does_not_publish_or_duplicate_business_events" in command
    assert "test_actual_process_exit_loses_observation_replay_but_not_native_resume" in command
    for path in (
        "tests/langgraph/test_native_stream_api.py",
        "tests/langgraph/test_native_stream_projection.py",
        "tests/core/test_native_streams.py",
        "tests/core/test_native_stream_registry.py",
        "tests/core/test_native_stream_transport.py",
        "tests/core/test_native_custom_stream_config.py",
    ):
        assert "../../" + path in command
        assert path in workflow["on"]["pull_request"]["paths"]
    assert (
        "tests/langgraph/native_stream_restart_worker.py" in workflow["on"]["pull_request"]["paths"]
    )
