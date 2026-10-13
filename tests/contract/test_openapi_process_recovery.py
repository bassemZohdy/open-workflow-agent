"""Actual OS stops retain immutable OpenAPI decisions and original outcomes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest
from _openapi_receiver import receiver
from engine_cases import engine_cases
from test_process_crash_recovery import _environment

WORKER = Path(__file__).with_name("_openapi_recovery_worker.py")
CA_ENV, CERT_ENV, KEY_ENV = "OPENAPI_STOP_CA", "OPENAPI_STOP_CERT", "OPENAPI_STOP_KEY"
CASES = [
    ("before-dispatch", "value", "changed"),
    ("before-dispatch", "value", "deleted"),
    ("business-commit", "value", "deleted"),
    *(
        ("receipt", outcome, metadata)
        for outcome in ("value", "null", "fault")
        for metadata in ("changed", "deleted")
    ),
    *(("terminal", outcome, "deleted") for outcome in ("value", "null", "fault")),
]


def failure(directory, mode):
    path = directory / (mode + "-failure.json")
    return path.read_text() if path.exists() else "worker ended without result"


def launch(directory, mode, remote):
    environment = _environment()
    environment.update(
        {
            CA_ENV: str(remote.certificate),
            CERT_ENV: str(remote.certificate),
            KEY_ENV: str(remote.key),
        }
    )
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


def stop(process):
    if process.poll() is None:
        process.kill()
    process.wait(timeout=15)


@pytest.mark.parametrize(
    "engine_name,engine_type", [case for case in engine_cases() if case[0] in {"adk", "langgraph"}]
)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("boundary,outcome,metadata", CASES)
def test_original_operation_survives_native_producer_stop_without_rediscovery_or_repeat_dispatch(
    tmp_path, engine_name, engine_type, backend, boundary, outcome, metadata
):
    if backend == "postgresql" and not os.getenv("OWA_TEST_POSTGRES_URL"):
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    native = engine_type().native
    if not (native.available if engine_name == "adk" else native.task_nodes_available):
        pytest.skip("independent actual native SDK environment is required")
    with receiver(tmp_path, outcome=outcome, hold_response=boundary == "business-commit") as remote:
        (tmp_path / "scenario.json").write_text(
            json.dumps(
                {
                    "engine": engine_name,
                    "backend": backend,
                    "boundary": boundary,
                    "outcome": outcome,
                    "subject": str(uuid4()),
                    "document_uri": remote.document_uri,
                    "endpoint": remote.endpoint,
                }
            )
        )
        producer = launch(tmp_path, "start", remote)
        try:
            deadline = time.monotonic() + 100
            while not (tmp_path / "ready.json").exists() or (
                boundary == "business-commit" and not remote.committed.is_set()
            ):
                assert producer.poll() is None, failure(tmp_path, "start")
                assert time.monotonic() < deadline, (
                    "producer did not reach its original durable boundary"
                )
                time.sleep(0.02)
            ready = json.loads((tmp_path / "ready.json").read_text())
            assert (
                ready["pid"] == producer.pid
                and ready["native_available"]
                and ready["backend"] == backend
            )
            assert (
                ready["native_rows"].get("sessions" if engine_name == "adk" else "checkpoints", 0)
                > 0
            )
            assert ready["identity"]["task_reference"] == "/do/0/action"
            assert ready["identity"]["operation"] == "call:openapi"
            assert ready["operation_id"] != "commit" and ready["operation_id"].startswith(
                ready["invocation_id"] + ":"
            )
            assert ready["receipt_status"] == (
                None
                if boundary == "before-dispatch"
                else "pending"
                if boundary == "business-commit"
                else "faulted"
                if outcome == "fault"
                else "completed"
            )
            assert len(remote.commits()) == (0 if boundary == "before-dispatch" else 1)
            producer.kill()
            assert producer.wait(timeout=15) != 0 and not (tmp_path / "start-cleaned.json").exists()
        finally:
            stop(producer)
            remote.release.set()
        requests_before = list(remote.requests)
        assert [request[0] for request in requests_before].count("GET") == 1
        assert requests_before[0] == ("GET", "/spec.json", False)
        remote.changed, remote.deleted = metadata == "changed", metadata == "deleted"
        recovery = launch(tmp_path, "recover", remote)
        try:
            assert recovery.wait(timeout=120) == 0, failure(tmp_path, "recover")
            result = json.loads((tmp_path / "result.json").read_text())
            assert (
                result["pid"] != producer.pid
                and result["native_available"]
                and result["backend"] == backend
            )
            assert (
                result["native_rows"].get("sessions" if engine_name == "adk" else "checkpoints", 0)
                > 0
            )
            for field in (
                "invocation_id",
                "session_id",
                "fingerprint",
                "original",
                "principal",
                "identity",
                "policy",
                "policy_proof",
                "request_digest",
                "operation_id",
                "deadline_at",
            ):
                assert result[field] == ready[field]
            assert result["status"] == (
                "faulted" if boundary == "business-commit" or outcome == "fault" else "completed"
            )
            assert result["duplicate"]["status"] == result["status"]
            assert result["duplicate"]["output"] == result["output"]
            assert result["duplicate"].get("error") == result["error"]
            assert result["model_calls"] == []
            assert not any(
                request[0] == "GET" for request in remote.requests[len(requests_before) :]
            )
            posts = [request for request in remote.requests if request[0] == "POST"]
            assert posts == [("POST", ready["operation_id"], True)]
            commits = remote.commits()
            assert len(commits) == 1 and commits[0][0] == ready["operation_id"]
            assert json.loads(commits[0][1]) == {
                "case": [ready["original"]["case"]],
                "value": ["original-value"],
            }
            if boundary == "business-commit":
                assert (
                    result["receipt_status"] == "pending"
                    and result["business_owner"] == ready["business_owner"]
                )
                assert (
                    "unresolved" in result["error"]["message"].lower()
                    or "recovery" in result["error"]["message"].lower()
                )
            else:
                assert result["receipt_status"] == (
                    "faulted" if outcome == "fault" else "completed"
                )
                assert result["output"] == (
                    None
                    if outcome in {"null", "fault"}
                    else {"committed": True, "original": "original-value"}
                )
                if ready["business_proof"] is not None:
                    assert result["business_proof"] == ready["business_proof"]
            if boundary == "terminal":
                assert result["generation"] == ready["generation"]
            else:
                assert result["generation"] > ready["generation"]
            assert (tmp_path / "recover-cleaned.json").exists()
        finally:
            stop(recovery)
