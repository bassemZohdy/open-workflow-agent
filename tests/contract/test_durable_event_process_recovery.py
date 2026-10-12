"""Kill native workers and recover durable events in separate actual processes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from engine_cases import engine_cases

ROOT = Path(__file__).resolve().parents[2]
WORKER = Path(__file__).with_name("_durable_event_recovery_worker.py")
LEGACY_SOURCE = "9e121d5db8d8f8a70ec409fa4961bbb166df472a"


def _start(directory: Path, mode: str, source_root: Path = ROOT) -> subprocess.Popen[bytes]:
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
        source_root / "core/src",
        *(source_root / "engines" / name / "src" for name in ("adk", "langgraph")),
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


def _legacy_source(directory: Path) -> Path:
    """Archive repository-owned predecessor code; no network or copied checkpoint."""
    archive = directory / "legacy.zip"
    archived = subprocess.run(
        [
            "git",
            "archive",
            "--format=zip",
            f"--output={archive}",
            LEGACY_SOURCE,
            "core/src",
            "engines/adk/src",
            "engines/langgraph/src",
            "resources",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert archived.returncode == 0, archived.stderr
    source = directory / "legacy-source"
    source.mkdir()
    with zipfile.ZipFile(archive) as saved:
        assert all(
            (source / name).resolve().is_relative_to(source.resolve()) for name in saved.namelist()
        )
        saved.extractall(source)
    return source


def _stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.kill()
    process.wait(timeout=15)


@pytest.mark.parametrize(
    "engine_name,engine_type", [case for case in engine_cases() if case[0] in {"adk", "langgraph"}]
)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize(
    "boundary", ["waiting", "selected", "partial-all", "foreach-outcome", "legacy-scalar"]
)
def test_durable_events_survive_actual_native_process_stop(
    tmp_path: Path, engine_name: str, engine_type: Any, backend: str, boundary: str
) -> None:
    if backend == "postgresql" and not os.getenv("OWA_TEST_POSTGRES_URL"):
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    native = engine_type().native
    if not (native.task_nodes_available if engine_name == "langgraph" else native.available):
        pytest.skip("independent native engine environment is required")
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
    legacy = boundary == "legacy-scalar"
    source = _legacy_source(tmp_path) if legacy else ROOT
    first = _start(tmp_path, "legacy-start" if legacy else "start", source)
    try:
        deadline = time.monotonic() + 60
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
        if legacy:
            assert ready["legacy_output"] == {"order": "chosen", "value": 1}
            assert ready["core_source"].startswith(str(source.resolve()))
        first.kill()
        assert first.wait(timeout=15) != 0
        assert not (tmp_path / (("legacy-start" if legacy else "start") + "-cleaned.json")).exists()
    finally:
        _stop(first)
    if not legacy:
        publisher = _start(tmp_path, "publish")
        try:
            assert publisher.wait(timeout=30) == 0, (
                (tmp_path / "publish-failure.json").read_text()
                if (tmp_path / "publish-failure.json").exists()
                else "separate publisher failed"
            )
            publication = json.loads((tmp_path / "published.json").read_text())
            assert publication["pid"] != first.pid
        finally:
            _stop(publisher)
    recovered = _start(tmp_path, "recover")
    try:
        assert recovered.wait(timeout=60) == 0, (
            (tmp_path / "recover-failure.json").read_text()
            if (tmp_path / "recover-failure.json").exists()
            else "recovery failed"
        )
        evidence = json.loads((tmp_path / "result.json").read_text())
        if legacy:
            assert evidence["rejection"] == "invalid_invocation_state"
            assert evidence["unchanged_parent"] and evidence["counts"] == {"calls": 0}
            assert evidence["pid"] != first.pid
            return
        result = evidence["result"]
        assert result["status"] == "completed", result.get("error")
        assert evidence["pid"] != first.pid
        assert evidence["counts"] == {"calls": 2 if boundary == "foreach-outcome" else 1}
        assert result["output"] == (
            {"items": [{"processed": 1}, {"processed": 2}]}
            if boundary == "foreach-outcome"
            else {"done": True}
        )
    finally:
        _stop(recovered)
