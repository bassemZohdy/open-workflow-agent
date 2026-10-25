"""A different real SDK process resumes original correlated history once."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest
from contract.test_process_crash_recovery import _environment
from engine_cases import engine_cases
from open_workflow_agent.tracing import invocation_trace_id, valid_traceparent

ENGINES = [case for case in engine_cases() if case[0] in {"adk", "langgraph"}]
WORKER = Path(__file__).with_name("_traced_history_recovery_worker.py")


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("name,engine_type", ENGINES)
def test_native_process_stop_retains_correlated_events_without_business_redispatch(
    tmp_path, backend, name, engine_type
):
    run_case(tmp_path, backend, name, engine_type)


def run_case(tmp_path, backend, name, engine_type, *, native_send=False):
    engine = engine_type()
    available = engine.native.available if name == "adk" else engine.native.task_nodes_available
    if not available:
        if os.getenv("OWA_TEST_REQUIRED_ENGINE") == name:
            pytest.fail("selected native tracing SDK is unavailable")
        pytest.skip("actual independent native SDK required")
    if backend == "postgresql" and not os.getenv("OWA_TEST_POSTGRES_URL"):
        pytest.skip("actual PostgreSQL datasource required")
    (tmp_path / "scenario.json").write_text(
        json.dumps(
            {"engine": name, "backend": backend, "name": uuid4().hex, "native_send": native_send}
        ),
        encoding="utf-8",
    )

    def launch(mode):
        return subprocess.Popen(
            [getattr(sys, "_base_executable", sys.executable), str(WORKER), str(tmp_path), mode],
            cwd=tmp_path,
            env=_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def read(name):
        return json.loads((tmp_path / name).read_text(encoding="utf-8"))

    producer = recovery = None
    try:
        producer = launch("start")
        deadline = time.monotonic() + 100
        while not (tmp_path / "ready.json").exists():
            assert producer.poll() is None, read("start-failure.json")
            assert time.monotonic() < deadline, "producer missed its finite committed boundary"
            time.sleep(0.02)
        first = read("ready.json")
        assert producer.pid == first["pid"] and first["status"] == "waiting"
        producer.kill()
        assert producer.wait(timeout=15) != 0
        assert not (tmp_path / "start-cleaned.json").exists()
        recovery = launch("recover")
        assert recovery.wait(timeout=100) == 0, read("recover-failure.json")
        final = read("result.json")
        assert final["pid"] != first["pid"] and final["status"] == "completed"
        assert final["invocation_id"] == first["invocation_id"]
        assert final["fingerprint"] == first["fingerprint"]
        assert all(event in final["events"] for event in first["events"])
        trace = invocation_trace_id(first["invocation_id"])
        for evidence in (first, final):
            assert evidence["spans"] and {span["trace_id"] for span in evidence["spans"]} == {trace}
            assert {span["kind"] for span in evidence["spans"]} == {"invocation", "task"}
            for event in evidence["events"]:
                if "traceparent" in event:
                    assert valid_traceparent(event["traceparent"])
                    assert event["traceparent"].split("-")[1] == trace
        assert {span["span_id"] for span in first["spans"]}.isdisjoint(
            span["span_id"] for span in final["spans"]
        )
        business = (tmp_path / "business.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(business) == 1 and valid_traceparent(json.loads(business[0])["traceparent"])
        assert "must-not-retain" not in json.dumps(first) + json.dumps(final)
    finally:
        for process in (producer, recovery):
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=15)
