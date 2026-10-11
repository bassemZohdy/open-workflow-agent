"""Tests for CLI worker and dashboard subcommands."""

from __future__ import annotations

from pathlib import Path

import pytest

from openworkflow_adk.cli import main
from tests.conftest import require_docker

pytestmark = [pytest.mark.integration, require_docker()]


@pytest.fixture
def workflow_dir(tmp_path) -> Path:
    workflow = tmp_path / "wf.yaml"
    workflow.write_text(
        """
document:
  dsl: '1.0.3'
  namespace: demo
  name: polled
  version: '1.0.0'
do:
  - finish:
      set:
        value: '"ok"'
"""
    )
    return tmp_path


def test_cli_worker_start_parses_args(monkeypatch, workflow_dir, postgres_url) -> None:
    import asyncio

    stop_event = asyncio.Event()
    calls = []

    async def fake_run_forever(self) -> None:
        calls.append("run_forever")
        stop_event.set()

    monkeypatch.setattr(
        "openworkflow_adk.ops.polling_worker.PostgresPollingWorker.run_forever", fake_run_forever
    )

    monkeypatch.setattr(
        "sys.argv",
        [
            "owf-adk",
            "worker",
            "start",
            "--directory",
            str(workflow_dir),
            "--postgres-url",
            postgres_url,
            "--namespace",
            "cli-worker",
        ],
    )
    main()
    assert calls == ["run_forever"]


def test_cli_dashboard_parses_args(monkeypatch, tmp_path, postgres_url) -> None:
    workflow = tmp_path / "wf.yaml"
    workflow.write_text(
        """
document:
  dsl: '1.0.3'
  namespace: demo
  name: dash
  version: '1.0.0'
do:
  - finish:
      set:
        value: 1
"""
    )
    calls = []

    def fake_serve(*args, **kwargs) -> None:
        calls.append((args, kwargs))

    monkeypatch.setattr("openworkflow_adk.server.serve", fake_serve)
    monkeypatch.setattr(
        "sys.argv",
        [
            "owf-adk",
            "dashboard",
            str(workflow),
            "--postgres-url",
            postgres_url,
            "--namespace",
            "cli-dash",
        ],
    )
    main()
    assert len(calls) == 1
    assert calls[0][1].get("history_config") is not None
