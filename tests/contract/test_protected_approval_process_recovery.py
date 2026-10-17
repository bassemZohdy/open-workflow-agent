"""Kill a real producer and recover protected controls in another actual process."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest
from _process_recovery_worker import write_json
from _protected_approval_fixtures import ENGINES, actual_native
from test_process_crash_recovery import _environment

WORKER = Path(__file__).with_name("_protected_approval_recovery_worker.py")


def launch(directory, mode):
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


def stop_owned_launcher(process):
    if process is not None:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=15)


def ready(directory, filename, producer):
    deadline = time.monotonic() + 100
    while True:
        assert producer.poll() is None, failure(directory, "start")
        assert time.monotonic() < deadline, "producer did not reach its finite durable boundary"
        try:
            return json.loads((directory / filename).read_text(encoding="utf-8"))
        except (FileNotFoundError, PermissionError):
            time.sleep(0.02)


def failure(directory, mode):
    path = directory / (mode + "-failure.json")
    return path.read_text(encoding="utf-8") if path.exists() else "worker failed without a result"


class ProducerIdentity:
    """Capture the actual acknowledged worker handle before any stop/reopen."""

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
            assert callable(getattr(os, "pidfd_open", None))
            assert callable(getattr(signal, "pidfd_send_signal", None))
            self.fd = os.pidfd_open(pid)

    def kill(self):
        if self.handle is not None:
            assert self.kernel.WaitForSingleObject(self.handle, 0) == 258
            assert self.kernel.TerminateProcess(self.handle, 99)
            assert self.kernel.WaitForSingleObject(self.handle, 10000) == 0
        else:
            signal.pidfd_send_signal(self.fd, signal.SIGKILL)

    def close(self):
        if self.handle is not None:
            if self.kernel.WaitForSingleObject(self.handle, 0) == 258:
                self.kernel.TerminateProcess(self.handle, 99)
                self.kernel.WaitForSingleObject(self.handle, 10000)
            self.kernel.CloseHandle(self.handle)
        elif self.fd is not None:
            try:
                signal.pidfd_send_signal(self.fd, signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.close(self.fd)


@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize(
    "case", ["approved", "rejected", "expired", "request-row", "policy", "audit"]
)
def test_actual_stop_retains_original_protected_decision_or_rejects_damaged_proof(
    tmp_path, engine_name, engine_type, backend, case
):
    if backend == "postgresql" and not os.getenv("OWA_TEST_POSTGRES_URL"):
        pytest.skip("actual PostgreSQL datasource required")
    actual_native(engine_type(), engine_name)
    subject = str(uuid4())
    write_json(
        tmp_path / "scenario.json",
        {
            "engine": engine_name,
            "backend": backend,
            "case": case,
            "subject": subject,
        },
    )
    producer = recovery = identity = None
    try:
        producer = launch(tmp_path, "start")
        evidence = ready(tmp_path, "ready.json", producer)
        assert evidence["approval_id"] == subject and evidence["backend"] == backend
        assert evidence["original_input"] == {"original": None, "nested": [True, False]}
        identity = ProducerIdentity(evidence["pid"])
        write_json(tmp_path / "ack.json", {"pid": evidence["pid"]})
        assert ready(tmp_path, "acknowledged.json", producer)["pid"] == evidence["pid"]
        identity.kill()
        assert producer.wait(timeout=15) != 0
        assert not (tmp_path / "start-cleaned.json").exists()
        recovery = launch(tmp_path, "recover")
        assert recovery.wait(timeout=100) == 0, failure(tmp_path, "recover")
        result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
        assert result["pid"] != evidence["pid"] and result["producer_pid"] == evidence["pid"]
        assert result["expiry"] == evidence["expiry"]
        assert result["business_count"] == (1 if case == "approved" else 0)
        if case in {"request-row", "policy", "audit"}:
            assert result["rejected"] is True and result["before_unchanged"] is True
        else:
            assert result["status"] == case
            assert result["original_input"] == evidence["original_input"]
    finally:
        if identity is not None:
            identity.close()
        stop_owned_launcher(producer)
        stop_owned_launcher(recovery)
