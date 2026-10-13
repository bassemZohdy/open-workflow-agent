"""Actual native OS stop, retained embedded transport proof and original clocks."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from _process_recovery_worker import write_json
from _selected_adapter_fixtures import ENGINES, RAW, actual_native
from test_process_crash_recovery import _environment
from test_protected_approval_process_recovery import (
    ProducerIdentity,
    failure,
    ready,
    stop_owned_launcher,
)

WORKER = Path(__file__).with_name("_selected_adapter_recovery_worker.py")


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
@pytest.mark.parametrize("boundary", ["inbox-before-ack", "listen-known", "model-known", "outbox"])
def test_real_stop_between_common_admission_ack_or_native_projection_keeps_original_event(
    tmp_path, engine_name, engine_type, backend, boundary
):
    if backend == "postgresql" and not os.getenv("OWA_TEST_POSTGRES_URL"):
        pytest.skip("actual PostgreSQL datasource required")
    actual_native(engine_type(), engine_name)
    write_json(
        tmp_path / "scenario.json",
        {
            "engine": engine_name,
            "backend": backend,
            "boundary": boundary,
            "subject": uuid4().hex,
        },
    )
    producer = recovery = identity = None
    try:
        producer = launch(tmp_path, "start")
        before = ready(tmp_path, "ready.json", producer)
        assert before["backend"] == backend and before["native"]["present"]
        assert before["authority"]["principal"]["identity"] == "alice"
        assert before["original_input"] == RAW and before["clock"]["profile"] == "owa/timing/v1"
        identity = ProducerIdentity(before["pid"])
        write_json(tmp_path / "ack.json", {"pid": before["pid"]})
        assert ready(tmp_path, "acknowledged.json", producer)["pid"] == before["pid"]
        identity.kill()
        assert producer.wait(timeout=15) != 0 and not (tmp_path / "start-cleaned.json").exists()
        recovery = launch(tmp_path, "recover")
        assert recovery.wait(timeout=100) == 0, failure(tmp_path, "recover")
        after = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
        assert after["pid"] != before["pid"] and after["producer_pid"] == before["pid"]
        assert after["status"] == "completed" and after["provider_count"] == 1
        assert after["new_model_calls"] == (0 if boundary == "model-known" else 1)
        assert after["original_input"] == before["original_input"]
        assert after["clock"] == before["clock"] and after["authority"] == before["authority"]
        assert after["admission"] == before["admission"]
    finally:
        if identity is not None:
            identity.close()
        stop_owned_launcher(producer)
        stop_owned_launcher(recovery)
