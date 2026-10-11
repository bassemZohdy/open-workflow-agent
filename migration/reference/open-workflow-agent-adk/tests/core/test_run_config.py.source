import pytest

from openworkflow_adk.run_config import RunConfig


def test_run_config_uses_environment_defaults() -> None:
    environ = {
        "WORKFLOW_SUSPEND_WAIT_SECONDS": "42",
        "WORKFLOW_CHECKPOINT_INTERVAL": "7",
    }
    with pytest.MonkeyPatch.context() as mp:
        for key, value in environ.items():
            mp.setenv(key, value)
        cfg = RunConfig()

    assert cfg.suspend_after == 42.0
    assert cfg.checkpoint_interval == 7


def test_run_config_explicit_values_override_environment() -> None:
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("WORKFLOW_SUSPEND_WAIT_SECONDS", "42")
        mp.setenv("WORKFLOW_CHECKPOINT_INTERVAL", "7")
        cfg = RunConfig(suspend_after=10.0, checkpoint_interval=3)

    assert cfg.suspend_after == 10.0
    assert cfg.checkpoint_interval == 3


def test_run_config_uses_builtin_defaults_when_env_missing(monkeypatch) -> None:
    monkeypatch.delenv("WORKFLOW_SUSPEND_WAIT_SECONDS", raising=False)
    monkeypatch.delenv("WORKFLOW_CHECKPOINT_INTERVAL", raising=False)
    cfg = RunConfig()

    assert cfg.suspend_after == 3600.0
    assert cfg.checkpoint_interval == 1
