"""Initial-delivery ownership fencing across independent database processes."""

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
from test_process_crash_recovery import _environment

WORKER = Path(__file__).with_name("_a2a_initial_delivery_race_worker.py")


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


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _wait_for(path: Path, process: subprocess.Popen[bytes], timeout: float) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return _read(path)
        if process.poll() is not None:
            failure = path.parent / ("-".join(path.stem.split("-")[:-1]) + "-failure.json")
            details = _read(failure) if failure.exists() else {"exit_code": process.returncode}
            pytest.fail(f"{process.args[-1]} process exited before {path.name}: {details}")
        time.sleep(0.02)
    pytest.fail(f"timed out waiting for {path.name}")


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.asyncio
async def test_old_initial_delivery_process_cannot_write_after_takeover(
    tmp_path: Path,
    backend: str,
) -> None:
    """A later process settles delivery once; the paused old owner stays fenced."""
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    scenario = {
        "backend": backend,
        "invocation_id": "",
        "input": {"request": "deliver exactly once"},
    }
    (tmp_path / "scenario.json").write_text(json.dumps(scenario), encoding="utf-8")

    old = _start(tmp_path, "old")
    current: subprocess.Popen[bytes] | None = None
    try:
        old_ready = _wait_for(tmp_path / "old-ready.json", old, 60)
        assert old_ready["pid"] == old.pid
        assert old_ready["generation"] > 0

        if backend == "postgresql":
            import psycopg

            assert datasource is not None
            with closing(psycopg.connect(datasource, autocommit=True)) as connection:
                updated = connection.execute(
                    "UPDATE owa_runtime.invocation_ownership "
                    "SET expires_at=0 WHERE invocation_id=%s",
                    (old_ready["invocation_id"],),
                )
                updated_count = updated.rowcount
        else:
            database = tmp_path / "runtime" / "runtime.sqlite3"
            with closing(sqlite3.connect(database, timeout=10)) as connection:
                updated = connection.execute(
                    "UPDATE invocation_ownership SET expires_at=0 WHERE invocation_id=?",
                    (old_ready["invocation_id"],),
                )
                connection.commit()
                updated_count = updated.rowcount
        assert updated_count == 1

        current = _start(tmp_path, "current")
        recovered = _wait_for(tmp_path / "current-result.json", current, 60)
        assert current.wait(timeout=15) == 0
        assert recovered["pid"] == current.pid != old.pid
        assert recovered["status"] == "completed"
        assert recovered["output"] == scenario["input"]
        assert recovered["generation"] > old_ready["generation"]
        assert recovered["delivery_status"] == "settled"
        assert recovered["commits"] == 1

        (tmp_path / "release-old").write_text("release", encoding="utf-8")
        stale = _wait_for(tmp_path / "old-result.json", old, 60)
        assert old.wait(timeout=15) == 0
        assert stale["pid"] == old.pid
        assert stale["error_type"] == "InvocationStateConflict"
        assert stale["snapshot"] == recovered["snapshot"]
        assert stale["commits"] == 1

        with closing(sqlite3.connect(tmp_path / "receiver.sqlite3")) as receiver:
            commits = receiver.execute("SELECT operation_id,body FROM commits").fetchall()
        assert len(commits) == 1
        assert json.loads(commits[0][1]) == {"input": scenario["input"]}
    finally:
        (tmp_path / "release-old").write_text("release", encoding="utf-8")
        for process in (old, current):
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=15)


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.asyncio
async def test_competing_initial_delivery_process_cannot_dispatch_while_owner_is_live(
    tmp_path: Path,
    backend: str,
) -> None:
    """A competing PID cannot enter dispatch while the durable owner is paused."""
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    scenario = {
        "backend": backend,
        "invocation_id": "",
        "input": {"request": "deliver exactly once"},
    }
    (tmp_path / "scenario.json").write_text(json.dumps(scenario), encoding="utf-8")

    owner = _start(tmp_path, "competing-owner")
    competitor: subprocess.Popen[bytes] | None = None
    try:
        owner_ready = _wait_for(tmp_path / "owner-ready.json", owner, 60)
        assert owner_ready["pid"] == owner.pid
        assert owner_ready["generation"] > 0

        competitor = _start(tmp_path, "competing-worker")
        competing_result = _wait_for(tmp_path / "competitor-result.json", competitor, 60)
        assert competitor.wait(timeout=15) == 0
        assert competing_result["pid"] == competitor.pid != owner.pid
        assert competing_result["error_type"] == "InvocationStateConflict"
        assert competing_result["snapshot"] == owner_ready["snapshot"]
        with closing(sqlite3.connect(tmp_path / "receiver.sqlite3")) as receiver:
            assert receiver.execute("SELECT COUNT(*) FROM commits").fetchone()[0] == 0

        (tmp_path / "release-owner").write_text("release", encoding="utf-8")
        settled = _wait_for(tmp_path / "owner-result.json", owner, 60)
        assert owner.wait(timeout=15) == 0
        assert settled["pid"] == owner.pid
        assert settled["status"] == "completed"
        assert settled["output"] == scenario["input"]
        assert settled["generation"] == owner_ready["generation"]
        assert settled["delivery_status"] == "settled"
        assert settled["commits"] == 1
        assert settled["snapshot"] != owner_ready["snapshot"]

        with closing(sqlite3.connect(tmp_path / "receiver.sqlite3")) as receiver:
            commits = receiver.execute("SELECT operation_id,body FROM commits").fetchall()
        assert len(commits) == 1
        assert json.loads(commits[0][1]) == {"input": scenario["input"]}
    finally:
        (tmp_path / "release-owner").write_text("release", encoding="utf-8")
        for process in (owner, competitor):
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=15)
