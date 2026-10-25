"""A real producer stop must not duplicate committed native channel deltas."""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from test_process_crash_recovery import _environment
from test_protected_approval_process_recovery import (
    ProducerIdentity,
    failure,
    stop_owned_launcher,
)

WORKER = Path(__file__).with_name("_native_channel_recovery_worker.py")


def launch(directory: Path, mode: str) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [getattr(sys, "_base_executable", sys.executable), str(WORKER), str(directory), mode],
        cwd=directory,
        env=_environment(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("native_command", [False, True])
@pytest.mark.parametrize("nested_reader", [False, True])
def test_process_stop_preserves_committed_native_channel_delta(
    tmp_path: Path, backend: str, native_command: bool, nested_reader: bool
) -> None:
    required_modules = ["langgraph.checkpoint.memory", "langgraph.func"]
    required_modules.append(f"langgraph.checkpoint.{backend}.aio")
    for module in required_modules:
        try:
            importlib.import_module(module)
        except ImportError:
            pytest.skip("independent LangGraph checkpoint dependency environment is required")
    if backend == "postgresql" and not os.getenv("OWA_TEST_POSTGRES_URL"):
        pytest.skip("actual PostgreSQL datasource required")
    (tmp_path / "scenario.json").write_text(
        json.dumps(
            {"backend": backend, "native_command": native_command, "nested_reader": nested_reader}
        ),
        encoding="utf-8",
    )
    producer = recovery = identity = None
    try:
        producer = launch(tmp_path, "start")
        deadline = time.monotonic() + 45
        while not (tmp_path / "ready.json").exists():
            assert producer.poll() is None, failure(tmp_path, "start")
            assert time.monotonic() < deadline, "producer did not reach the native wait checkpoint"
            time.sleep(0.02)
        evidence = read(tmp_path / "ready.json")
        assert evidence["backend"] == backend
        assert evidence["status"] == "waiting"
        assert evidence["channel_values"] == {"items": ["seed", "first"]}
        assert evidence["snapshots_checked"] > 0
        assert evidence["checkpoint_scopes"] == (2 if nested_reader else 1)
        identity = ProducerIdentity(evidence["pid"])
        (tmp_path / "ack.json").write_text(json.dumps({"pid": evidence["pid"]}), encoding="utf-8")
        deadline = time.monotonic() + 15
        while not (tmp_path / "acknowledged.json").exists():
            assert producer.poll() is None, "producer exited before stop barrier"
            assert time.monotonic() < deadline, "producer did not acknowledge stop barrier"
            time.sleep(0.02)
        identity.kill()
        assert producer.wait(timeout=15) != 0
        assert not (tmp_path / "start-cleaned.json").exists()

        recovery = launch(tmp_path, "recover")
        assert recovery.wait(timeout=90) == 0, failure(tmp_path, "recover")
        result = read(tmp_path / "result.json")
        assert result["pid"] != evidence["pid"]
        assert result["producer_pid"] == evidence["pid"]
        assert result["status"] == "completed"
        assert result["output"] == {"items": ["seed", "first", "second"]}
        assert result["duplicate_output"] == result["output"]
    finally:
        if identity is not None:
            identity.close()
        stop_owned_launcher(producer)
        stop_owned_launcher(recovery)
