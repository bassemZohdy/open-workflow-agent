"""Force an OS process stop at durable boundaries, then recover in a new process."""

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

import pytest
from engine_cases import engine_cases
from open_workflow_agent.effects import EffectJournal
from open_workflow_agent.persistence import InvocationStore

ROOT = Path(__file__).resolve().parents[2]
WORKER = Path(__file__).with_name("_process_recovery_worker.py")


def _environment() -> dict[str, str]:
    # The test worker needs SDK dependencies, not the runtime's credentials.
    allowed = {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "LOCALAPPDATA",
        "HOME",
        # Runtime recovery profiles include platform.machine(); preserve the
        # non-secret Windows architecture declarations in isolated workers.
        "PROCESSOR_ARCHITECTURE",
        "PROCESSOR_ARCHITEW6432",
        "OWA_TEST_POSTGRES_URL",
    }
    environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    paths = [
        ROOT / "core/src",
        *(ROOT / "engines" / engine / "src" for engine in ("adk", "langgraph", "agent-framework")),
    ]
    paths.extend(Path(value) for value in sys.path if value and Path(value).is_dir())
    environment["PYTHONPATH"] = os.pathsep.join(str(path) for path in paths)
    environment["PYTHONUNBUFFERED"] = "1"
    return environment


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _failure(directory: Path, mode: str) -> Any:
    path = directory / (mode + "-failure.json")
    return _read(path) if path.exists() else {"stage": mode, "result": "worker exited"}


def _start(directory: Path, mode: str) -> subprocess.Popen[bytes]:
    # Windows venv python.exe is a launcher with a different PID from its worker.
    # Use the real interpreter so Popen.kill targets the native runtime process.
    python = getattr(sys, "_base_executable", sys.executable)
    return subprocess.Popen(
        [python, str(WORKER), str(directory), mode],
        cwd=directory,
        env=_environment(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


def _stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.kill()
    process.wait(timeout=15)


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize(
    "boundary,outcome",
    [
        ("wait", "value"),
        ("receipt", "value"),
        ("receipt", "null"),
        ("receipt", "fault"),
        ("pending", "value"),
        ("terminal", "value"),
        ("terminal", "null"),
        ("terminal", "fault"),
    ],
)
def test_forced_process_stop_recovers_committed_state_without_repeating_actions(
    tmp_path: Path,
    engine_name: str,
    engine_type: type[Any],
    backend: str,
    boundary: str,
    outcome: str,
) -> None:
    if backend == "postgresql" and not os.getenv("OWA_TEST_POSTGRES_URL"):
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    native = engine_type().native
    if not (native.task_nodes_available if engine_name == "langgraph" else native.available):
        pytest.skip("independent native dependency environment is required")
    scenario = {"engine": engine_name, "backend": backend, "boundary": boundary, "outcome": outcome}
    (tmp_path / "scenario.json").write_text(json.dumps(scenario), encoding="utf-8")
    with closing(sqlite3.connect(tmp_path / "external.sqlite3")) as external:
        external.execute("CREATE TABLE calls(operation_id TEXT, body TEXT)")
        external.commit()

    first = _start(tmp_path, "start")
    try:
        deadline = time.monotonic() + 45
        while not (tmp_path / "ready.json").exists():
            assert first.poll() is None, _failure(tmp_path, "start")
            assert time.monotonic() < deadline, "worker did not reach durable boundary"
            time.sleep(0.02)
        ready = _read(tmp_path / "ready.json")
        assert ready["native_available"] and ready["pid"] == first.pid
        assert ready["boundary"] == boundary and first.poll() is None
        first.kill()
        assert first.wait(timeout=15) != 0
        assert not (tmp_path / "start-cleaned.json").exists(), "forced stop must bypass cleanup"
    finally:
        _stop(first)

    database = (
        os.environ["OWA_TEST_POSTGRES_URL"]
        if backend == "postgresql"
        else tmp_path / "runtime.sqlite3"
    )
    store = InvocationStore(database)
    journal = EffectJournal(database)
    try:
        before = store.get(ready["invocation_id"])
        assert before is not None
        assert store.read_input(before) == {"request": "original"}
        expected_before = (
            "waiting"
            if boundary == "wait"
            else "faulted"
            if boundary == "terminal" and outcome == "fault"
            else "completed"
            if boundary == "terminal"
            else "running"
        )
        assert before.status == ready["status"] == expected_before
        receipt = None
        if ready["effect_key"]:
            row = journal.connection.execute(
                "SELECT payload FROM workflow_effects WHERE identity = ?", (ready["effect_key"],)
            ).fetchone()
            assert row is not None
            receipt = json.loads(row[0])
            assert receipt["status"] == (
                "pending"
                if boundary == "pending"
                else "faulted"
                if outcome == "fault"
                else "completed"
            )
    finally:
        store.close()
        journal.close()

    recovered = _start(tmp_path, "recover")
    try:
        assert recovered.wait(timeout=45) == 0, _failure(tmp_path, "recover")
    finally:
        _stop(recovered)
    assert (tmp_path / "recover-cleaned.json").exists()
    result = _read(tmp_path / "result.json")
    assert result["pid"] != ready["pid"]
    assert result["original"] == {"request": "original"}
    assert result["result"] == result["duplicate"]
    expected = "faulted" if boundary == "pending" or outcome == "fault" else "completed"
    assert result["result"]["status"] == result["saved"] == expected
    if boundary == "wait":
        assert result["result"]["output"] == {"original": {"request": "original"}, "resumed": None}
    elif expected == "completed":
        assert result["result"]["output"] == (None if outcome == "null" else {"accepted": True})
    elif boundary == "pending":
        assert "unresolved" in result["result"]["error"]["message"]
    else:
        error = ready["error"]
        if boundary == "receipt":
            error = {**error, "details": {**error["details"], "instance": "/do/0/action"}}
        assert result["result"]["error"] == error
    if boundary == "terminal":
        assert result["result"]["output"] == before.output
        assert result["result"].get("error") == before.error
    assert before.engine_execution_reference not in json.dumps(result["result"])
    with closing(sqlite3.connect(tmp_path / "external.sqlite3")) as external:
        calls = external.execute("SELECT operation_id, body FROM calls").fetchall()
    assert len(calls) == (0 if boundary == "wait" else 1), (
        "recovery must not repeat external dispatch"
    )
    if calls:
        assert calls[0][0] and json.loads(calls[0][1]) == {"request": "original"}
    journal = EffectJournal(database)
    try:
        if ready["effect_key"]:
            after = journal.connection.execute(
                "SELECT payload FROM workflow_effects WHERE identity = ?", (ready["effect_key"],)
            ).fetchone()
            assert json.loads(after[0]) == receipt, "recovery retains the first action record"
    finally:
        journal.close()
