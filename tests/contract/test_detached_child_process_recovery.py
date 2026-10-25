"""Actual process-stop qualification for durable independent detached children."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from engine_cases import engine_cases

pytestmark = pytest.mark.usefixtures("explicit_workflow_delivery")
ROOT = Path(__file__).resolve().parents[2]
WORKER = Path(__file__).with_name("_detached_child_recovery_worker.py")


def _start(directory: Path, mode: str) -> subprocess.Popen[bytes]:
    allowed = {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "LOCALAPPDATA",
        "HOME",
        "OWA_TEST_POSTGRES_URL",
    }
    environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    paths = [
        ROOT / "core/src",
        *(ROOT / "engines" / name / "src" for name in ("adk", "langgraph", "agent-framework")),
    ]
    paths.extend(Path(value) for value in sys.path if value and Path(value).is_dir())
    environment["PYTHONPATH"] = os.pathsep.join(str(path) for path in paths)
    environment["PYTHONUNBUFFERED"] = "1"
    return subprocess.Popen(
        [getattr(sys, "_base_executable", sys.executable), str(WORKER), str(directory), mode],
        cwd=directory,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


def _failure(directory: Path, mode: str) -> str:
    path = directory / (mode + "-failure.json")
    return path.read_text(encoding="utf-8") if path.exists() else "worker exited without evidence"


def _stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.kill()
    process.wait(timeout=15)


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("boundary", ["admission", "parent-terminal", "child-receipt"])
@pytest.mark.parametrize("lifetime", ["complete", "cancel"])
def test_detached_child_survives_actual_process_stop_and_independent_parent_lifetime(
    tmp_path: Path,
    engine_name: str,
    engine_type: Any,
    backend: str,
    boundary: str,
    lifetime: str,
) -> None:
    if backend == "postgresql" and not os.getenv("OWA_TEST_POSTGRES_URL"):
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    native = engine_type().native
    if not (native.task_nodes_available if engine_name == "langgraph" else native.available):
        pytest.skip("independent native dependency environment is required")
    original = {"request": str(uuid4()), "original": "must-survive"}
    (tmp_path / "scenario.json").write_text(
        json.dumps(
            {
                "engine": engine_name,
                "backend": backend,
                "boundary": boundary,
                "lifetime": lifetime,
                "original": original,
            }
        ),
        encoding="utf-8",
    )
    first = _start(tmp_path, "start")
    try:
        deadline = time.monotonic() + 60
        while not (tmp_path / "ready.json").exists():
            assert first.poll() is None, _failure(tmp_path, "start")
            assert time.monotonic() < deadline, "producer did not reach the committed boundary"
            time.sleep(0.02)
        ready = json.loads((tmp_path / "ready.json").read_text(encoding="utf-8"))
        assert ready["pid"] == first.pid and first.poll() is None
        assert ready["native_available"] and ready["boundary"] == boundary
        assert ready["child_status"] == "running"
        assert ready["child_input"] == original
        assert ready["binding_count"] == ready["budget_used"] == 1
        if boundary != "admission":
            assert ready["parent_status"] == (
                "completed" if lifetime == "complete" else "cancelled"
            )
        else:
            assert ready["parent_status"] == "running"
        assert ready["model_calls"] == 0
        first.kill()
        assert first.wait(timeout=15) != 0
        assert not (tmp_path / "start-cleaned.json").exists()
    finally:
        _stop(first)
    second = _start(tmp_path, "recover")
    try:
        assert second.wait(timeout=75) == 0, _failure(tmp_path, "recover")
        evidence = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
        assert evidence["pid"] != ready["pid"]
        assert evidence["child_id"] == ready["child_id"]
        assert evidence["parent_id"] == ready["parent_id"]
        assert evidence["binding_key"] == ready["binding_key"]
        assert evidence["request_digest"] == ready["request_digest"]
        assert evidence["child_fingerprint"] == ready["child_fingerprint"]
        assert evidence["child_version"] == "1.0.0"
        assert evidence["child_status"] == "completed"
        assert evidence["child_output"] == {"accepted": original}
        assert evidence["child_input"] == original
        assert evidence["parent_status"] == ("completed" if lifetime == "complete" else "cancelled")
        assert evidence["parent_output"] == (original if lifetime == "complete" else None)
        assert evidence["binding_count"] == evidence["budget_used"] == 1
        assert evidence["model_calls"] == 0
        assert evidence["decision_unchanged"] and not evidence["dispatcher_errors"]
        with closing(sqlite3.connect(tmp_path / "external.sqlite3")) as external:
            calls = external.execute(
                "SELECT operation_id,body FROM calls ORDER BY rowid"
            ).fetchall()
        assert len(calls) == len({row[0] for row in calls}) == 1
        assert json.loads(calls[0][1]) == original
        assert (tmp_path / "recover-cleaned.json").exists()
    finally:
        _stop(second)
