"""Real OS-stop/native independent-job lifetime qualification, embedded SQLite only."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest
from engine_cases import engine_cases
from test_process_crash_recovery import _environment

WORKER = Path(__file__).with_name("_background_job_recovery_worker.py")


def launch(directory, mode):
    # The supervisor uses isolated Python and installed bundled runtime. Retain
    # this exact editable SDK environment, rather than a base-only interpreter.
    return subprocess.Popen(
        [sys.executable, str(WORKER), str(directory), mode],
        cwd=directory,
        env=_environment(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def stop(process):
    if process.poll() is None:
        process.kill()
    process.wait(timeout=15)


def kill_published_worker(pid):
    assert type(pid) is int and pid > 0 and pid != os.getpid()
    if os.name != "nt":
        import signal

        os.kill(pid, signal.SIGKILL)
        return
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateProcess.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x0001 | 0x00100000, False, pid)
    assert handle, "published producer process handle unavailable"
    try:
        assert kernel.TerminateProcess(handle, 1)
        assert kernel.WaitForSingleObject(handle, 15000) == 0
    finally:
        kernel.CloseHandle(handle)


def await_file(directory, name, process, budget=100):
    deadline = time.monotonic() + budget
    while not (directory / name).exists():
        failure = directory / (
            ("start" if name in {"ready.json", "terminal-observed.json"} else "resume")
            + "-failure.json"
        )
        assert process.poll() is None, failure.read_text() if failure.exists() else "worker ended"
        assert time.monotonic() < deadline, "worker did not reach the durable boundary"
        time.sleep(0.05)
    return json.loads((directory / name).read_text())


def require_native_engine(engine_name, native):
    available = native.available if engine_name == "adk" else native.task_nodes_available
    if available:
        return
    if os.getenv("OWA_TEST_REQUIRED_ENGINE") == engine_name:
        pytest.fail(f"required native {engine_name} SDK is unavailable")
    pytest.skip("actual independent native SDK environment is required")


@pytest.mark.parametrize(
    "engine_name,engine_type", [case for case in engine_cases() if case[0] in {"adk", "langgraph"}]
)
@pytest.mark.parametrize("boundary", ["receipt", "completed", "cancelled", "drained"])
def test_real_native_parent_stop_completion_cancel_and_drain_leave_one_independent_job(
    tmp_path, engine_name, engine_type, boundary
):
    native = engine_type().native
    require_native_engine(engine_name, native)
    (tmp_path / "scenario.json").write_text(
        json.dumps({"engine": engine_name, "boundary": boundary})
    )
    with sqlite3.connect(tmp_path / "business.sqlite3") as receiver:
        receiver.execute("CREATE TABLE commits(value TEXT NOT NULL)")
    producer = launch(tmp_path, "start")
    recovery = None
    try:
        ready = await_file(tmp_path, "ready.json", producer)
        assert len({ready["producer_pid"], ready["supervisor_pid"], ready["child_pid"]}) == 3
        with sqlite3.connect(tmp_path / "business.sqlite3") as receiver:
            assert receiver.execute("SELECT COUNT(*) FROM commits").fetchone()[0] == 0
        kill_published_worker(
            ready["producer_pid"]
        )  # Kill actual worker, not a Windows venv launcher.
        producer.wait(timeout=15)
        assert not (tmp_path / "result.json").exists()
        recovery = launch(tmp_path, "resume")
        result = await_file(tmp_path, "result.json", recovery, budget=150)
        recovery.wait(timeout=20)
        assert recovery.returncode == 0
        assert (
            len(
                {
                    ready["producer_pid"],
                    result["recovery_pid"],
                    ready["supervisor_pid"],
                    ready["child_pid"],
                }
            )
            == 4
        )
        assert result["commits"] == 1 and result["control"] is None
        assert result["admission_digest"] == ready["admission_digest"]
        assert result["deadline_at"] == ready["deadline_at"]
    finally:
        (tmp_path / "release-job").touch()
        stop(producer)
        if recovery is not None:
            stop(recovery)


@pytest.mark.parametrize(
    "engine_name,engine_type", [case for case in engine_cases() if case[0] in {"adk", "langgraph"}]
)
@pytest.mark.parametrize(
    "damage",
    ["job", "policy", "action", "action_index", "policy_index", "archive", "input", "profile"],
)
def test_actual_native_background_corruption_rejects_before_parent_ownership_or_job_launch(
    tmp_path, engine_name, engine_type, damage
):
    native = engine_type().native
    require_native_engine(engine_name, native)
    (tmp_path / "scenario.json").write_text(
        json.dumps({"engine": engine_name, "boundary": "receipt", "damage": damage})
    )
    with sqlite3.connect(tmp_path / "business.sqlite3") as receiver:
        receiver.execute("CREATE TABLE commits(value TEXT NOT NULL)")
    producer = launch(tmp_path, "start")
    recovery = None
    try:
        ready = await_file(tmp_path, "ready.json", producer)
        assert ready["producer_pid"] != os.getpid()
        assert any(ready["native"].values())
        kill_published_worker(ready["producer_pid"])
        producer.wait(timeout=15)
        with sqlite3.connect(tmp_path / "runtime" / "runtime.sqlite3") as common:
            if damage == "job":
                common.execute("DELETE FROM sandbox_jobs WHERE job_id=?", (ready["job_id"],))
            elif damage in {"policy", "action", "action_index", "policy_index"}:
                selected = (
                    "policy:sandbox-job-admission:v1"
                    if damage.startswith("policy")
                    else "run:script"
                )
                rows = common.execute(
                    "SELECT identity,payload FROM workflow_effect_invocations "
                    "WHERE invocation_id=?",
                    (ready["invocation_id"],),
                ).fetchall()
                key = next(
                    key for key, payload in rows if json.loads(payload)["operation"] == selected
                )
                table = (
                    "workflow_effect_invocations"
                    if damage.endswith("index")
                    else "workflow_effects"
                )
                common.execute(f"DELETE FROM {table} WHERE identity=?", (key,))
            elif damage == "archive":
                common.execute(
                    "DELETE FROM recovery_definitions WHERE fingerprint=?", (ready["fingerprint"],)
                )
            elif damage == "input":
                common.execute(
                    "DELETE FROM invocation_inputs WHERE invocation_id=?", (ready["invocation_id"],)
                )
            common.commit()
        recovery = launch(tmp_path, "resume")
        result = await_file(tmp_path, "result.json", recovery)
        recovery.wait(timeout=20)
        assert recovery.returncode == 0 and result["rejected"] is True
        assert result["commits"] == 0 and result["recovery_pid"] != ready["producer_pid"]
        assert not (tmp_path / "child-ready.json").exists()
    finally:
        stop(producer)
        if recovery is not None:
            stop(recovery)


@pytest.mark.parametrize(
    "engine_name,engine_type", [case for case in engine_cases() if case[0] in {"adk", "langgraph"}]
)
def test_real_native_producer_stop_after_terminal_observation_recovers_receipt(
    tmp_path, engine_name, engine_type
):
    native = engine_type().native
    require_native_engine(engine_name, native)
    (tmp_path / "scenario.json").write_text(
        json.dumps({"engine": engine_name, "boundary": "terminal-observed"})
    )
    with sqlite3.connect(tmp_path / "business.sqlite3") as receiver:
        receiver.execute("CREATE TABLE commits(value TEXT NOT NULL)")
    producer = launch(tmp_path, "start")
    recovery = None
    try:
        ready = await_file(tmp_path, "ready.json", producer)
        assert len({ready["producer_pid"], ready["supervisor_pid"], ready["child_pid"]}) == 3
        assert ready["binding"] and ready["submit_json"]

        # Let the independent process finish. The worker's synchronous _record
        # barrier publishes only after inspect returned the authenticated
        # terminal observation, then holds the producer before any terminal
        # receipt can be committed.
        (tmp_path / "release-job").touch()
        boundary = await_file(tmp_path, "terminal-observed.json", producer, budget=100)
        observation = boundary["observation"]
        assert observation["state"] == "terminal" and observation["outcome"]["exit_code"] == 0
        assert boundary["job_id"] == ready["job_id"]
        assert boundary["producer_pid"] == ready["producer_pid"]
        with sqlite3.connect(tmp_path / "runtime" / "runtime.sqlite3") as common:
            status, submit_json, terminal_json = common.execute(
                "SELECT status,submit_json,terminal_json FROM sandbox_job_delivery WHERE job_id=?",
                (ready["job_id"],),
            ).fetchone()
        assert status == "accepted" and submit_json == ready["submit_json"]
        assert terminal_json is None
        kill_published_worker(ready["producer_pid"])
        producer.wait(timeout=15)
        assert not (tmp_path / "result.json").exists()

        recovery = launch(tmp_path, "resume")
        result = await_file(tmp_path, "result.json", recovery, budget=150)
        recovery.wait(timeout=20)
        assert recovery.returncode == 0
        assert result["recovery_pid"] != ready["producer_pid"]
        assert result["observation"] == observation
        assert result["binding"] == ready["binding"]
        assert result["submit_json"] == ready["submit_json"]
        assert result["terminal"]["reason"] == observation["reason"] == "exited"
        assert result["terminal"]["outcome"] == observation["outcome"]
        assert result["terminal"]["binding"] == ready["binding"]
        assert result["recovery_submit_calls"] == 0
        assert result["commits"] == 1
        assert result["admission_digest"] == ready["admission_digest"]
        assert result["deadline_at"] == ready["deadline_at"]
    finally:
        stop(producer)
        if recovery is not None:
            stop(recovery)
