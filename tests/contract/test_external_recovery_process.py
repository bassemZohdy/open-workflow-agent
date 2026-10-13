"""Receiver-enforced dedupe/query proof after an actual native producer is killed."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest
from _external_receiver import TOKEN, TOKEN_ENV, receiver
from engine_cases import engine_cases
from test_process_crash_recovery import _environment

WORKER = Path(__file__).with_name("_external_recovery_worker.py")


def _failure(directory: Path, mode: str) -> str:
    path = directory / (mode + "-failure.json")
    return path.read_text() if path.exists() else "worker failed without result"


def _start(directory: Path, mode: str) -> subprocess.Popen[bytes]:
    environment = _environment()
    environment[TOKEN_ENV] = TOKEN
    return subprocess.Popen(
        [getattr(sys, "_base_executable", sys.executable), str(WORKER), str(directory), mode],
        cwd=directory,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


@pytest.mark.parametrize(
    "engine_name,engine_type", [case for case in engine_cases() if case[0] in {"adk", "langgraph"}]
)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("outcome", ["value", "null", "fault"])
@pytest.mark.parametrize("boundary", ["before-dispatch", "business-commit", "receipt", "terminal"])
def test_trusted_receiver_outcome_recovers_actual_native_process_stop_without_post_replay(
    tmp_path, engine_name, engine_type, backend, outcome, boundary
):
    if backend == "postgresql" and not os.getenv("OWA_TEST_POSTGRES_URL"):
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    native = engine_type().native
    if not (native.task_nodes_available if engine_name == "langgraph" else native.available):
        pytest.skip("independent actual native environment is required")
    with receiver(tmp_path, outcome=outcome, hold_response=boundary == "business-commit") as remote:
        (tmp_path / "scenario.json").write_text(
            json.dumps(
                {
                    "engine": engine_name,
                    "backend": backend,
                    "outcome": outcome,
                    "boundary": boundary,
                    "subject": str(uuid4()),
                    "business_endpoint": remote.business_endpoint,
                    "lookup_endpoint": remote.lookup_endpoint,
                    "certificate": str(remote.certificate),
                }
            ),
            encoding="utf-8",
        )
        first = _start(tmp_path, "start")
        try:
            deadline = time.monotonic() + 100
            while not (tmp_path / "ready.json").exists() or (
                boundary == "business-commit" and not remote.committed.is_set()
            ):
                assert first.poll() is None, _failure(tmp_path, "start")
                assert time.monotonic() < deadline, (
                    "producer did not acknowledge original action boundary"
                )
                time.sleep(0.02)
            ready = json.loads((tmp_path / "ready.json").read_text())
            assert ready["pid"] == first.pid and ready["native_available"]
            if boundary == "business-commit":
                assert ready["receipt_status"] == "pending" and remote.commits() == 1
            first.kill()
            assert first.wait(timeout=15) != 0
            assert not (tmp_path / "start-cleaned.json").exists()
        finally:
            if first.poll() is None:
                first.kill()
            first.wait(timeout=15)
            remote.release.set()
        requests_before = list(remote.requests)
        second = _start(tmp_path, "recover")
        try:
            assert second.wait(timeout=120) == 0, _failure(tmp_path, "recover")
            result = json.loads((tmp_path / "result.json").read_text())
            assert result["pid"] != first.pid
            assert result["invocation_id"] == ready["invocation_id"]
            assert result["operation_id"] == ready["operation_id"]
            assert result["request_digest"] == ready["request_digest"]
            assert result["owner"] == ready["owner"]
            assert result["original"] == ready["original"]
            assert result["duplicate"]["status"] == result["status"]
            assert result["duplicate"]["output"] == result["output"]
            assert result["duplicate"]["error"] == result["error"]
            assert result["model_calls"] == []
            assert all(method != "POST" for method, _ in remote.requests[len(requests_before) :])
            assert remote.commits() == (0 if boundary == "before-dispatch" else 1)
            if boundary == "business-commit":
                assert result["has_external_proof"]
                assert sum(method == "POST" for method, _ in remote.requests) == 1
                assert sum(method == "GET" for method, _ in remote.requests) >= 1
            if boundary != "before-dispatch":
                proof = remote.proof(ready["operation_id"])
                assert result["output"] == proof["output"]
                if outcome == "fault":
                    assert result["error"]["code"] == proof["error"]["code"]
                    assert result["error"]["details"]["status"] == 503
        finally:
            if second.poll() is None:
                second.kill()
            second.wait(timeout=15)
