from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from engine_cases import engine_cases

ROOT = Path(__file__).resolve().parents[2]
WORKER = Path(__file__).with_name("_timing_recovery_worker.py")


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
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    paths = [
        ROOT / "core/src",
        *(ROOT / "engines" / name / "src" for name in ("adk", "langgraph", "agent-framework")),
    ]
    paths.extend(Path(value) for value in sys.path if value and Path(value).is_dir())
    env["PYTHONPATH"] = os.pathsep.join(str(path) for path in paths)
    env["PYTHONUNBUFFERED"] = "1"
    return subprocess.Popen(
        [getattr(sys, "_base_executable", sys.executable), str(WORKER), str(directory), mode],
        cwd=directory,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


def _stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.kill()
    process.wait(timeout=15)


@pytest.mark.parametrize(
    "engine_name,engine_type", [case for case in engine_cases() if case[0] in {"adk", "langgraph"}]
)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("boundary", ["workflow-deadline", "task-deadline", "retry-delay"])
def test_actual_process_stop_preserves_deadlines_and_retry_choice(
    tmp_path: Path, engine_name: str, engine_type: Any, backend: str, boundary: str
) -> None:
    if backend == "postgresql" and not os.getenv("OWA_TEST_POSTGRES_URL"):
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    native = engine_type().native
    if not (native.task_nodes_available if engine_name == "langgraph" else native.available):
        pytest.skip("independent native engine environment is required")
    (tmp_path / "scenario.json").write_text(
        json.dumps({"engine": engine_name, "backend": backend, "boundary": boundary})
    )
    first = _start(tmp_path, "start")
    try:
        deadline = time.monotonic() + 45
        while not (tmp_path / "ready.json").exists():
            assert first.poll() is None, (
                (tmp_path / "start-failure.json").read_text()
                if (tmp_path / "start-failure.json").exists()
                else "worker exited"
            )
            assert time.monotonic() < deadline
            time.sleep(0.02)
        ready = json.loads((tmp_path / "ready.json").read_text())
        assert ready["pid"] == first.pid and first.poll() is None
        first.kill()
        assert first.wait(timeout=15) != 0
        assert not (tmp_path / "start-cleaned.json").exists()
    finally:
        _stop(first)
    recovered = _start(tmp_path, "recover")
    try:
        assert recovered.wait(timeout=45) == 0, (
            (tmp_path / "recover-failure.json").read_text()
            if (tmp_path / "recover-failure.json").exists()
            else "recovery failed"
        )
        data = json.loads((tmp_path / "result.json").read_text())
        if boundary == "retry-delay":
            assert data["result"]["status"] == "completed"
            assert data["result"]["output"] == {"done": True}
            assert data["counts"] == {"calls": 2, "draws": 1}
        else:
            assert data["result"]["status"] == "faulted"
            assert "timed out" in data["result"]["error"]["message"]
            assert data["counts"] == {"calls": 0, "draws": 0}
    finally:
        _stop(recovered)
