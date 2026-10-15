"""Real OS-stop source capture/recovery; no receiver deduplication or fake SDK."""

from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest
from engine_cases import engine_cases
from test_executable_source_calls import URI, initialize_ledger, ledger
from test_process_crash_recovery import _environment

ENGINES = [case for case in engine_cases() if case[0] in {"adk", "langgraph"}]
WORKER = Path(__file__).with_name("_executable_source_recovery_worker.py")


def launch(directory, mode):
    # Real interpreter identity, with only the explicit test SDK/source path.
    # Runtime scripts still launch exclusively through common SandboxManager.
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


def stop(process):
    if process is not None:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=15)


class ChildIdentity:
    """Hold a live OS identity before producer death; never adopt a saved PID."""

    def __init__(self, pid):
        self.handle = self.fd = None
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            self.kernel.OpenProcess.restype = wintypes.HANDLE
            self.kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
            self.kernel.TerminateProcess.restype = wintypes.BOOL
            self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            self.kernel.WaitForSingleObject.restype = wintypes.DWORD
            self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            self.handle = self.kernel.OpenProcess(0x0001 | 0x00100000, False, pid)
            assert self.handle
        else:
            assert callable(getattr(os, "pidfd_open", None)), "actual Linux process handle required"
            self.fd = os.pidfd_open(pid)

    def close_and_stop_fixture(self):
        if self.handle is not None:
            if self.kernel.WaitForSingleObject(self.handle, 0) == 0x102:
                assert self.kernel.TerminateProcess(self.handle, 1)
                assert self.kernel.WaitForSingleObject(self.handle, 15000) == 0
            self.kernel.CloseHandle(self.handle)
            self.handle = None
        if self.fd is not None:
            try:
                signal.pidfd_send_signal(self.fd, signal.SIGKILL)
            except ProcessLookupError:
                pass
            finally:
                os.close(self.fd)
                self.fd = None


def read_boundary(directory, filename, process, mode):
    deadline = time.monotonic() + 100
    while True:
        failure = directory / (mode + "-failure.json")
        assert process.poll() is None, (
            failure.read_text(encoding="utf-8") if failure.exists() else "worker exited"
        )
        assert time.monotonic() < deadline, "worker did not reach its finite durable boundary"
        try:
            return json.loads((directory / filename).read_text(encoding="utf-8"))
        except (FileNotFoundError, PermissionError):
            time.sleep(0.02)


@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize(
    "boundary,damage",
    [
        ("before-dispatch", "none"),
        ("pending", "none"),
        ("known", "none"),
        ("known", "registry"),
        ("known", "policy"),
        ("known", "index"),
    ],
)
def test_real_stop_preserves_original_source_and_never_relaunches_uncertain_script(
    tmp_path, engine_name, engine_type, backend, boundary, damage
):
    if backend == "postgresql" and not os.getenv("OWA_TEST_POSTGRES_URL"):
        pytest.skip("actual PostgreSQL datasource is required")
    native = engine_type().native
    if not (native.available if engine_name == "adk" else native.task_nodes_available):
        pytest.skip("actual independent native SDK environment is required")
    subject = str(uuid4())
    (tmp_path / "scenario.json").write_text(
        json.dumps(
            {
                "engine": engine_name,
                "backend": backend,
                "boundary": boundary,
                "damage": damage,
                "subject": subject,
            }
        ),
        encoding="utf-8",
    )
    initialize_ledger(tmp_path)
    if boundary == "pending":
        (tmp_path / "hold-business").touch()
    producer = recovery = child = None
    try:
        producer = launch(tmp_path, "start")
        ready = read_boundary(tmp_path, "ready.json", producer, "start")
        assert ready["pid"] == producer.pid != os.getpid() and ready["backend"] == backend
        assert ready["version"] == "1.0.0" and ready["owner"] == "original-owner"
        assert ready["input"] == {"subject": subject, "nullValue": None}
        assert ready["source_binding"]["uri"] == URI
        assert ready["source_binding"]["version"] == "1.0.0"
        assert ready["deadline_at"] == ready["clock"]["started_at"] + 120
        assert ready["lease_expires_at"] > time.time() and ready["model_calls"] == 0
        assert any(value["count"] for value in ready["native"].values())
        assert ready["commits"] == (0 if boundary == "before-dispatch" else 1)
        assert ready["receipt_status"] == ("completed" if boundary == "known" else "pending")
        rows = ledger(tmp_path)
        if rows:
            assert json.loads(rows[0][1]) == ready["input"] and rows[0][2] != producer.pid
        if boundary == "pending":
            child = ChildIdentity(ready["child_pid"])
        producer.kill()
        assert producer.wait(timeout=15) != 0 and not (tmp_path / "start-cleaned.json").exists()
        if child is not None:
            # Test cleanup only. No claim that POSIX invocation-owner death is
            # hard isolation or automatically kills a session child.
            child.close_and_stop_fixture()
        recovery = launch(tmp_path, "recover")
        assert recovery.wait(timeout=100) == 0, (
            (tmp_path / "recover-failure.json").read_text(encoding="utf-8")
            if (tmp_path / "recover-failure.json").exists()
            else "recovery worker exited"
        )
        result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
        assert result["pid"] == recovery.pid and result["pid"] not in {ready["pid"], os.getpid()}
        assert result["commits"] == ready["commits"] and result["model_calls"] == 0
        if damage != "none":
            assert result["rejected_unchanged"]
        else:
            for name in (
                "invocation_id",
                "session_id",
                "reference",
                "fingerprint",
                "version",
                "owner",
                "input",
                "identity",
                "policy_digest",
                "source_binding",
                "deadline_at",
                "clock",
                "intent_digest",
                "receipt_digest",
            ):
                assert result[name] == ready[name]
            assert result["generation"] > ready["generation"]
            if boundary == "known":
                assert (
                    result["status"] == "completed"
                    and json.loads(result["output"]) == ready["input"]
                )
                assert result["receipt_status"] == "completed"
            else:
                assert result["status"] == "faulted" and result["receipt_status"] == "pending"
                assert any(
                    word in json.dumps(result["error"]).lower()
                    for word in ("unresolved", "recovery", "uncertain")
                )
        assert ledger(tmp_path) == rows and (tmp_path / "recover-cleaned.json").exists()
        with sqlite3.connect(tmp_path / "business.sqlite3") as connection:
            assert (
                connection.execute("SELECT COUNT(*) FROM commits").fetchone()[0] == ready["commits"]
            )
    finally:
        stop(recovery)
        stop(producer)
        if child is not None:
            child.close_and_stop_fixture()
