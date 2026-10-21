"""A killed producer retains admitted v1 while a separate public deployment is v2."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from test_process_crash_recovery import _environment
from test_workflow_version_recovery import ENGINES, _actual_native

WORKER = Path(__file__).with_name("_version_recovery_worker.py")


def _start(directory: Path, mode: str) -> subprocess.Popen[bytes]:
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


def _failure(directory: Path, mode: str) -> str:
    path = directory / (mode + "-failure.json")
    return path.read_text(encoding="utf-8") if path.exists() else "worker failed without a result"


@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("boundary", ["archive", "waiting"])
def test_actual_producer_stop_preserves_original_definition_and_public_recovery_policy(
    tmp_path: Path,
    engine_name: str,
    engine_type: Any,
    backend: str,
    boundary: str,
) -> None:
    if backend == "postgresql" and not os.getenv("OWA_TEST_POSTGRES_URL"):
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    _actual_native(engine_type(), engine_name)
    (tmp_path / "scenario.json").write_text(
        json.dumps(
            {
                "engine": engine_name,
                "backend": backend,
                "boundary": boundary,
                "subject": str(uuid4()),
            }
        ),
        encoding="utf-8",
    )
    first = _start(tmp_path, "start")
    try:
        deadline = time.monotonic() + 100
        while True:
            assert first.poll() is None, _failure(tmp_path, "start")
            assert time.monotonic() < deadline, (
                "producer did not acknowledge committed definition boundary"
            )
            try:
                # The producer fsyncs and closes a sibling temporary before
                # atomic replace. Windows may briefly deny an open of that
                # published file; retain the same producer and deadline.
                ready_text = (tmp_path / "ready.json").read_text(encoding="utf-8")
            except (FileNotFoundError, PermissionError):
                time.sleep(0.02)
                continue
            ready = json.loads(ready_text)
            break
        assert ready["pid"] == first.pid and ready["native_available"] and ready["binding_present"]
        assert ready["version"] == "1.0.0"
        assert ready["remote_commits"] == (0 if boundary == "archive" else 1)
        first.kill()
        assert first.wait(timeout=15) != 0
        assert not (tmp_path / "start-cleaned.json").exists()
    finally:
        if first.poll() is None:
            first.kill()
        first.wait(timeout=15)
    second = _start(tmp_path, "recover")
    try:
        assert second.wait(timeout=150) == 0, _failure(tmp_path, "recover")
        result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
        assert result["pid"] != ready["pid"]
        assert result["invocation_id"] == ready["invocation_id"]
        assert (
            result["session_id"] == ready["session_id"]
            and result["fingerprint"] == ready["fingerprint"]
        )
        assert result["version"] == "1.0.0" and result["original"] == ready["original"]
        assert result["output"] == {
            "version": "1.0.0",
            "original": ready["original"],
            "resume": None,
        }
        assert result["remote_commits"] == 2 and result["model_calls"] == []
        assert result["archive_only_rejected_without_mutation"] == (boundary == "archive")
    finally:
        if second.poll() is None:
            second.kill()
        second.wait(timeout=15)
