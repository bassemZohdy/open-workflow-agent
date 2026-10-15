"""Actual OS-stop gRPC business uncertainty and native receipt recovery."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path
from uuid import uuid4

import pytest
from engine_cases import engine_cases
from test_process_crash_recovery import _environment

ENGINES = [case for case in engine_cases() if case[0] in {"adk", "langgraph"}]
WORKER = Path(__file__).with_name("_grpc_recovery_worker.py")
RECEIVER = Path(__file__).with_name("_grpc_recovery_peer.py")


def start(directory, mode):
    command = [
        getattr(sys, "_base_executable", sys.executable),
        str(RECEIVER if mode == "receiver" else WORKER),
        str(directory),
    ]
    if mode != "receiver":
        command.append(mode)
    return subprocess.Popen(
        command,
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


def read_boundary(directory, filename, process, mode):
    deadline = time.monotonic() + 100
    while True:
        failure = directory / (mode + "-failure.json")
        assert process.poll() is None, (
            failure.read_text(encoding="utf-8")
            if failure.exists()
            else f"{mode} exited without evidence"
        )
        assert time.monotonic() < deadline, f"{mode} did not reach its finite committed boundary"
        try:
            return json.loads((directory / filename).read_text(encoding="utf-8"))
        except (FileNotFoundError, PermissionError):
            time.sleep(0.02)


def case(directory, engine_name, engine_type, backend, boundary, outcome, negative):
    if backend == "postgresql" and not os.getenv("OWA_TEST_POSTGRES_URL"):
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    native = engine_type().native
    if not (native.available if engine_name == "adk" else native.task_nodes_available):
        pytest.skip("actual independent native SDK is required")
    subject = str(uuid4())
    (directory / "scenario.json").write_text(
        json.dumps(
            {
                "engine": engine_name,
                "backend": backend,
                "boundary": boundary,
                "outcome": outcome,
                "negative": negative,
                "subject": subject,
            }
        ),
        encoding="utf-8",
    )
    receiver = start(directory, "receiver")
    producer = recovery = None
    try:
        peer = read_boundary(directory, "receiver-ready.json", receiver, "receiver")
        assert peer["pid"] == receiver.pid
        producer = start(directory, "start")
        ready = read_boundary(directory, "ready.json", producer, "start")
        assert ready["pid"] == producer.pid != receiver.pid and ready["native_available"]
        assert ready["backend"] == backend and ready["input"] == {"request": subject}
        assert ready["version"] == "1.0.0" and ready["owner"] == "original-owner"
        assert ready["clock"] == {"profile": "owa/timing/v1", "started_at": 1000.0}
        assert ready["deadlines"] == [
            {"profile": "owa/deadline/v1", "started_at": 1000.0, "duration": 120.0}
        ]
        assert ready["commits"] == 1 and ready["model_calls"] == 0
        assert ready["operation_id"] and any(row["count"] for row in ready["native"].values())
        assert ready["receipt_status"] == (
            "pending" if boundary == "pending" else "faulted" if outcome == "fault" else "completed"
        )
        assert bool(ready["receipt_digest"]) == (boundary == "known")
        with closing(sqlite3.connect(directory / "receiver.sqlite3", timeout=3)) as ledger:
            rows = ledger.execute("SELECT operation_id,body,pid FROM commits").fetchall()
        assert len(rows) == 1
        assert rows[0][0] == ready["operation_id"] and rows[0][2] == receiver.pid
        assert json.loads(rows[0][1]) == {"version": "1.0.0", "original": {"request": subject}}
        producer.kill()
        assert producer.wait(timeout=15) != 0
        assert not (directory / "start-cleaned.json").exists()
        assert receiver.poll() is None
        recovery = start(directory, "recover")
        assert recovery.wait(timeout=90) == 0, (
            (directory / "recover-failure.json").read_text(encoding="utf-8")
            if (directory / "recover-failure.json").exists()
            else "recovery exited without evidence"
        )
        result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
        assert result["pid"] == recovery.pid and result["pid"] not in {ready["pid"], receiver.pid}
        assert result["commits"] == 1 and result["model_calls"] == 0
        if negative != "none":
            assert result["rejected_unchanged"]
        else:
            for key in (
                "invocation_id",
                "session_id",
                "reference",
                "fingerprint",
                "version",
                "owner",
                "input",
                "operation_id",
                "clock",
                "deadlines",
                "identity",
                "receipt_digest",
                "intent_digest",
                "grpc_profile",
            ):
                assert result[key] == ready[key]
            assert result["generation"] > ready["generation"]
            if boundary == "pending":
                assert result["status"] == "faulted" and result["receipt_status"] == "pending"
                assert "unresolved" in json.dumps(result["error"]).lower()
            elif outcome == "fault":
                assert result["status"] == "faulted" and result["receipt_status"] == "faulted"
                assert result["error"]["code"] == "tool_error"
                assert "private controlled" not in json.dumps(result["error"])
            else:
                assert result["status"] == "completed" and result["receipt_status"] == "completed"
                assert result["output"] == {"version": "1.0.0", "original": {"request": subject}}
        assert (directory / "recover-cleaned.json").exists()
        with closing(sqlite3.connect(directory / "receiver.sqlite3", timeout=3)) as ledger:
            assert ledger.execute("SELECT operation_id,body,pid FROM commits").fetchall() == rows
    finally:
        stop(recovery)
        stop(producer)
        stop(receiver)


@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize(
    "boundary,outcome", [("pending", "value"), ("known", "value"), ("known", "fault")]
)
def test_real_stop_preserves_original_grpc_commit_receipt_and_native_identity(
    tmp_path, engine_name, engine_type, backend, boundary, outcome
):
    case(tmp_path, engine_name, engine_type, backend, boundary, outcome, "none")


@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("negative", ["archive-binding", "descriptor-profile"])
def test_stopped_grpc_recovery_rejects_damaged_binding_or_profile_before_any_mutation(
    tmp_path, engine_name, engine_type, backend, negative
):
    case(tmp_path, engine_name, engine_type, backend, "known", "value", negative)
