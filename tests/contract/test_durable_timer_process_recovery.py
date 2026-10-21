"""Force-stop actual SDK processes at committed timer and schedule boundaries."""

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

ROOT = Path(__file__).resolve().parents[2]
WORKER = Path(__file__).with_name("_durable_timer_recovery_worker.py")


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
    paths = [ROOT / "core/src", *(ROOT / "engines" / name / "src" for name in ("adk", "langgraph"))]
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
    return path.read_text() if path.exists() else "worker exited without a result"


def _stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.kill()
    process.wait(timeout=15)


@pytest.mark.parametrize(
    "engine_name,engine_type", [case for case in engine_cases() if case[0] in {"adk", "langgraph"}]
)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize(
    "boundary", ["waiting", "wake-receipt", "downstream-receipt", "slot-claimed", "slot-bound"]
)
def test_original_timer_and_atomic_slot_binding_survive_actual_native_process_stop(
    tmp_path: Path, engine_name: str, engine_type: Any, backend: str, boundary: str
) -> None:
    if backend == "postgresql" and not os.getenv("OWA_TEST_POSTGRES_URL"):
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    native = engine_type().native
    if not (native.task_nodes_available if engine_name == "langgraph" else native.available):
        pytest.skip("independent native engine environment is required")
    subject = str(uuid4())
    (tmp_path / "scenario.json").write_text(
        json.dumps(
            {"engine": engine_name, "backend": backend, "boundary": boundary, "subject": subject}
        ),
        encoding="utf-8",
    )
    first = _start(tmp_path, "start")
    try:
        deadline = time.monotonic() + 60
        while not (tmp_path / "ready.json").exists():
            assert first.poll() is None, _failure(tmp_path, "start")
            assert time.monotonic() < deadline, "worker did not reach committed boundary"
            time.sleep(0.02)
        ready = json.loads((tmp_path / "ready.json").read_text())
        assert ready["pid"] == first.pid and first.poll() is None and ready["native_available"]
        if boundary == "waiting":
            assert ready["nonresident"] and ready["due_at"] == 1060
        if boundary == "slot-claimed":
            assert ready["invocation_id"] is None
        if boundary == "slot-bound":
            assert ready["invocation_id"] is not None
        first.kill()
        assert first.wait(timeout=15) != 0
        assert not (tmp_path / "start-cleaned.json").exists()
    finally:
        _stop(first)
    second = _start(tmp_path, "recover")
    try:
        assert second.wait(timeout=75) == 0, _failure(tmp_path, "recover")
        evidence = json.loads((tmp_path / "result.json").read_text())
        assert evidence["pid"] != first.pid
        assert evidence["status"] == evidence["duplicate"]["status"] == "completed"
        assert evidence["output"] == evidence["duplicate"]["output"] == {"accepted": True}
        assert evidence["original"] == {"request": subject, "original": "must-survive"}
        assert evidence["fingerprint"] == ready["fingerprint"]
        if ready["invocation_id"] is not None:
            assert evidence["invocation_id"] == ready["invocation_id"]
        if boundary.startswith("slot-"):
            assert evidence["occurrence_key"] == ready["occurrence_key"]
            assert evidence["claim_generation"] > ready["claim_generation"]
        if not boundary.startswith("slot-"):
            assert evidence["armed_at"] == 1000 and evidence["due_at"] == 1060
            assert evidence["timer_key"] == ready["timer_key"]
        with closing(sqlite3.connect(tmp_path / "external.sqlite3")) as external:
            calls = external.execute(
                "SELECT operation_id,body FROM calls ORDER BY rowid"
            ).fetchall()
        assert len(calls) == len({row[0] for row in calls}) == 2
        bodies = [json.loads(row[1]) for row in calls]
        assert bodies == [
            {"phase": "before", "original": evidence["original"]},
            {"phase": "after", "original": evidence["original"]},
        ]
    finally:
        _stop(second)
