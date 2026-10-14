"""Actual SDK-acknowledged initial delivery survives OS stop and deployment change."""

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
WORKER = Path(__file__).with_name("_a2a_initial_delivery_worker.py")


def start(directory, mode):
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
    if process.poll() is None:
        process.kill()
    process.wait(timeout=15)


def failure(directory, mode):
    path = directory / (mode + "-failure.json")
    if not path.exists():
        return "worker exited without evidence"
    payload = json.loads(path.read_text(encoding="utf-8"))
    diagnostic = directory / "start-admission-error.json"
    if mode == "start" and diagnostic.exists() and isinstance(payload, dict):
        payload["admission_error"] = json.loads(diagnostic.read_text(encoding="utf-8"))
    profile_diff = directory / "runtime-profile-diff.json"
    if mode == "recover" and profile_diff.exists() and isinstance(payload, dict):
        payload["runtime_profile_diff"] = json.loads(profile_diff.read_text(encoding="utf-8"))
    return json.dumps(payload)


def case(directory, engine_name, engine_type, backend, boundary, negative):
    if backend == "postgresql" and not os.getenv("OWA_TEST_POSTGRES_URL"):
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    native = engine_type().native
    if not (native.available if engine_name == "adk" else native.task_nodes_available):
        pytest.skip("actual independent native environment is required")
    subject = str(uuid4())
    original = "original-" + subject
    (directory / "scenario.json").write_text(
        json.dumps(
            {
                "engine": engine_name,
                "backend": backend,
                "boundary": boundary,
                "negative": negative,
                "subject": subject,
                "original": original,
            }
        ),
        encoding="utf-8",
    )
    producer = start(directory, "start")
    try:
        deadline = time.monotonic() + 100
        while True:
            assert producer.poll() is None, failure(directory, "start")
            assert time.monotonic() < deadline, (
                "SDK ACK producer did not reach its committed boundary"
            )
            try:
                ready = json.loads((directory / "ready.json").read_text(encoding="utf-8"))
                break
            except (FileNotFoundError, PermissionError):
                time.sleep(0.02)
        assert ready["pid"] == producer.pid and ready["native_available"]
        assert ready["input"] == {"question": original}
        assert ready["session_id"] == "context-" + subject
        assert ready["owner"] == "initial-owner" and ready["version"] == "1.0.0"
        assert ready["delivery"] == "ready" and ready["model_calls"] == 0
        assert ready["clock"] == {"profile": "owa/timing/v1", "started_at": 1000.0}
        assert ready["commits"] == (1 if boundary == "known-receipt" else 0)
        if boundary == "before-sdk":
            assert not any(row["count"] for row in ready["native"]["rows"].values())
        else:
            assert any(row["count"] for row in ready["native"]["rows"].values())
        assert bool(ready["receipt_digest"]) == (boundary == "known-receipt")
        producer.kill()
        assert producer.wait(timeout=15) != 0
        assert not (directory / "start-cleaned.json").exists()
    finally:
        stop(producer)
    recovery = start(directory, "recover")
    try:
        assert recovery.wait(timeout=90) == 0, failure(directory, "recover")
        result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
        assert result["pid"] == recovery.pid != ready["pid"]
        if negative != "none":
            assert result["rejected_unchanged"] and result["commits"] == 0
        else:
            for field in (
                "invocation_id",
                "session_id",
                "reference",
                "version",
                "fingerprint",
                "owner",
                "input",
                "clock",
            ):
                assert result[field] == ready[field]
            assert result["output"] == {"version": "1.0.0", "original": {"question": original}}
            assert (
                result["commits"] == 1
                and result["model_calls"] == 0
                and result["delivery"] == "settled"
            )
            assert result["generation"] > ready["generation"]
            with closing(sqlite3.connect(directory / "receiver.sqlite3")) as receiver:
                rows = receiver.execute("SELECT operation_id,body FROM commits").fetchall()
            assert len(rows) == 1 and rows[0][0]
            assert json.loads(rows[0][1]) == {
                "version": "1.0.0",
                "original": {"question": original},
            }
        assert (directory / "recover-cleaned.json").exists()
    finally:
        stop(recovery)


@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("boundary", ["before-sdk", "known-receipt", "partial-native"])
def test_sdk_async_ack_survives_real_stop_with_original_definition_and_clock(
    tmp_path, engine_name, engine_type, backend, boundary
):
    case(tmp_path, engine_name, engine_type, backend, boundary, "none")


@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("negative", ["revoked", "archive", "queue"])
def test_stopped_delivery_invalid_authority_or_evidence_rejects_before_lease_or_native_mutation(
    tmp_path, engine_name, engine_type, backend, negative
):
    case(tmp_path, engine_name, engine_type, backend, "before-sdk", negative)
