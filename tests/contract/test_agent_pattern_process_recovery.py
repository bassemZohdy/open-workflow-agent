"""Real OS-stop pattern recovery with original role, identity and clocks."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from _agent_pattern_fixtures import ENGINES, PATTERNS, actual_native, scripts
from _process_recovery_worker import write_json
from test_process_crash_recovery import _environment
from test_protected_approval_process_recovery import (
    ProducerIdentity,
    failure,
    ready,
    stop_owned_launcher,
)

WORKER = Path(__file__).with_name("_agent_pattern_recovery_worker.py")
CASES = [(pattern, "known") for pattern in PATTERNS] + [
    ("assistant", "pending"),
    ("assistant", "openapi-pending"),
    ("assistant", "openapi-known"),
    ("pipeline", "role-change"),
    ("assistant", "model-change"),
    ("assistant", "tool-change"),
]


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
    "pattern,boundary", CASES, ids=[pattern + "-" + boundary for pattern, boundary in CASES]
)
def test_killed_pattern_producer_retains_original_role_receipts_or_explicit_uncertainty(
    tmp_path, engine_name, engine_type, backend, pattern, boundary
):
    if backend == "postgresql" and not os.getenv("OWA_TEST_POSTGRES_URL"):
        pytest.skip("actual PostgreSQL datasource required")
    actual_native(engine_type(), engine_name)
    write_json(
        tmp_path / "scenario.json",
        {
            "engine": engine_name,
            "backend": backend,
            "pattern": pattern,
            "boundary": boundary,
        },
    )
    producer = recovery = identity = None
    try:
        producer = launch(tmp_path, "start")
        evidence = ready(tmp_path, "ready.json", producer)
        assert evidence["backend"] == backend and evidence["native"]["present"]
        assert evidence["authority"]["principal"]["identity"] == "alice"
        assert evidence["original_input"]["question"] == "hello"
        assert evidence["clock"]["profile"] == "owa/timing/v1"
        assert evidence["receipt_status"] == (
            "pending" if boundary in {"pending", "openapi-pending"} else "completed"
        )
        assert evidence["provider_count"] >= 1
        if boundary == "openapi-pending":
            assert evidence["receipt_status"] == "pending"
            assert evidence["outer_agent_status"] == "pending"
            assert evidence["business_count"] == 1
        elif boundary == "openapi-known":
            assert evidence["receipt_status"] == "completed"
            assert evidence["outer_agent_status"] == "completed"
            assert evidence["invocation_status"] == "waiting"
            assert evidence["business_count"] == 1
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
        if boundary in {"role-change", "model-change", "tool-change"}:
            assert result["rejected"] and result["unchanged"]
            assert result["provider_count"] == evidence["provider_count"]
        else:
            assert (
                result["authority"] == evidence["authority"]
                and result["clock"] == evidence["clock"]
            )
            assert result["original_input"] == evidence["original_input"]
            assert result["status"] == (
                "faulted" if boundary in {"pending", "openapi-pending"} else "completed"
            )
            if boundary in {"pending", "openapi-pending"}:
                assert (
                    result["provider_count"] == 1 and sum(result["recovery_counts"].values()) == 0
                )
                if boundary == "openapi-pending":
                    assert result["business_count"] == 1
            elif boundary == "openapi-known":
                assert result["provider_count"] == 2
                assert sum(result["recovery_counts"].values()) == 0
                assert result["business_count"] == 1
            else:
                assert result["provider_count"] == sum(
                    len(values)
                    for role, values in scripts(pattern).items()
                    if not (pattern == "expert_dispatch" and role == "finance")
                )
                for role, count in result["before_counts"].items():
                    assert (
                        result["provider_counts"][role] == count + result["recovery_counts"][role]
                    )
    finally:
        if identity is not None:
            identity.close()
        stop_owned_launcher(producer)
        stop_owned_launcher(recovery)
