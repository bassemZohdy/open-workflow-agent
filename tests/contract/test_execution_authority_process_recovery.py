"""Actual OS stop and independent native recovery with immutable original claims."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from _process_recovery_worker import write_json
from test_execution_authority_isolation import ENGINES, RAW, actual_native
from test_process_crash_recovery import _environment
from test_protected_approval_process_recovery import (
    ProducerIdentity,
    failure,
    ready,
    stop_owned_launcher,
)

WORKER = Path(__file__).with_name("_execution_authority_recovery_worker.py")


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


@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize(
    "case", ["resume", "authority", "origin", "decision", "seal", "claims", "pending"]
)
def test_actual_process_stop_preserves_original_authority_or_rejects_corrupt_replay(
    tmp_path, engine_name, engine_type, backend, case
):
    if backend == "postgresql" and not os.getenv("OWA_TEST_POSTGRES_URL"):
        pytest.skip("actual PostgreSQL datasource required")
    actual_native(engine_type(), engine_name)
    write_json(
        tmp_path / "scenario.json",
        {
            "engine": engine_name,
            "backend": backend,
            "case": case,
            "name": uuid4().hex,
        },
    )
    producer = recovery = identity = None
    try:
        producer = launch(tmp_path, "start")
        evidence = ready(tmp_path, "ready.json", producer)
        assert evidence["backend"] == backend and evidence["native"]["present"]
        assert evidence["original_input"] == RAW
        assert evidence["authority"]["principal"]["identity"] == "alice"
        assert set(evidence["authority"]["principal"]["roles"]) == {"worker", "role-alice"}
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
        assert result["due_at"] == evidence["due_at"] and result["original_input"] == RAW
        if case == "resume":
            assert result["status"] == "completed" and result["business_count"] == 1
            assert result["authority"] == evidence["authority"] and result["operation_key"]
        else:
            assert result["rejected"] and result["unchanged"]
            assert result["business_count"] == (1 if case == "pending" else 0)
    finally:
        if identity is not None:
            identity.close()
        stop_owned_launcher(producer)
        stop_owned_launcher(recovery)
