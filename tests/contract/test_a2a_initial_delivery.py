"""Actual SDK-acknowledged initial delivery survives OS stop and deployment change."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from engine_cases import engine_cases
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.engine import PortableWorkflowEngine
from open_workflow_agent.errors import InvocationStateConflict
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.recovery_definitions import PROFILE, RecoveryDefinitionArchive
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.timer_dispatcher import TimerDispatcher
from open_workflow_agent.workflow import compile_workflow
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
        assert ready["skill_id"] == "retained"
        assert ready["delivery"] == "ready" and ready["model_calls"] == 0
        assert ready["clock"] == {"profile": "owa/timing/v1", "started_at": 1000.0}
        assert ready["commits"] == (1 if boundary in {"known-receipt", "remote-accepted"} else 0)
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
        elif boundary == "remote-accepted":
            assert result["status"] == "faulted"
            assert result["task_state"] not in {
                "TASK_STATE_COMPLETED",
                "TASK_STATE_CANCELED",
            }
            # The durable initial-delivery queue settles after recording the
            # failed invocation; the unresolved external effect remains pending.
            assert result["delivery"] == "settled"
            assert result["pending_effect_status"] == "pending"
            assert result["requests"] == result["commits"] == 1
            assert result["model_calls"] == 0
            pending_before = [row for row in result["before"]["effects"] if row[1] == "pending"]
            pending_after = [row for row in result["after"]["effects"] if row[1] == "pending"]
            assert len(pending_before) == len(pending_after) == 1
            assert pending_before == pending_after
            with closing(sqlite3.connect(directory / "receiver.sqlite3")) as receiver:
                rows = receiver.execute("SELECT operation_id,body FROM commits").fetchall()
                requests = receiver.execute(
                    "SELECT operation_id,body FROM requests ORDER BY rowid"
                ).fetchall()
            assert len(rows) == len(requests) == 1
            assert requests[0] == rows[0]
            assert json.loads(rows[0][1]) == {
                "version": "1.0.0",
                "original": {"question": original},
            }
        else:
            assert result["skill_id"] == ready["skill_id"] == "retained"
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
                requests = receiver.execute(
                    "SELECT operation_id,body FROM requests ORDER BY rowid"
                ).fetchall()
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
@pytest.mark.parametrize(
    "boundary", ["before-sdk", "remote-accepted", "known-receipt", "partial-native"]
)
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


@pytest.mark.asyncio
async def test_stale_initial_delivery_generation_cannot_write_after_takeover(tmp_path, monkeypatch):
    """A fenced-out dispatcher cannot alter the newer dispatcher's committed result."""

    async def no_timer_workers(_dispatcher):
        pass

    monkeypatch.setattr(TimerDispatcher, "start", no_timer_workers)
    model = FakeModel()
    source = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "a2a-initial-race",
            "name": "initial-race",
            "version": "1.0.0",
        },
        "do": [
            {
                "commit": {
                    "call": "http",
                    "with": {
                        "method": "POST",
                        "endpoint": "https://initial-race.test/delivery",
                        "body": {"input": "${ $workflow.input }"},
                    },
                }
            },
            {"finish": {"set": "${ $workflow.input }"}},
        ],
    }
    services = RuntimeServices(
        RuntimeConfig.model_validate(
            {"model": {"provider": "fake"}, "workflow": {"definition": source}}
        ),
        model=model,
        database_root=tmp_path / "runtime",
    )
    services.recovery_definitions = RecoveryDefinitionArchive(
        services.effect_journal,
        services.invocations,
        profile_provider=lambda engine: {
            "profile": PROFILE,
            "engine": engine,
            "backend": "sqlite",
            "artifact": "initial-delivery-generation-fence",
            "continuation_supported": True,
        },
    )
    deliveries: list[dict[str, object]] = []

    async def record_delivery(request: httpx.Request) -> httpx.Response:
        deliveries.append(json.loads(request.content))
        return httpx.Response(200, json={"accepted": True})

    services.protocols = ProtocolServices(
        HttpClient(transport=httpx.MockTransport(record_delivery))
    )
    services.tools.protocols = services.protocols
    engines = [PortableWorkflowEngine(), PortableWorkflowEngine()]
    stale_started = asyncio.Event()
    release_stale = asyncio.Event()
    try:
        for engine in engines:
            await engine.initialize(services)
        plan = compile_workflow(source)
        handle = services.invocations.create(
            engine="base",
            session_id="race-context",
            user_id="race-user",
            owner_principal="race-owner",
            workflow_name=plan.name,
            workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
        )
        original_input = {"request": "deliver once"}
        await engines[0].prepare_initial_delivery(plan, handle, original_input, skill_id="race")
        original_invoke = engines[0]._invoke_owned

        async def pause_stale_generation(*args, **kwargs):
            stale_started.set()
            await release_stale.wait()
            return await original_invoke(*args, **kwargs)

        monkeypatch.setattr(engines[0], "_invoke_owned", pause_stale_generation)

        def authorize(_handle, _plan):
            return None

        stale_dispatch = asyncio.create_task(
            engines[0].dispatch_initial_delivery(handle, authorize)
        )
        await asyncio.wait_for(stale_started.wait(), timeout=10)
        old_generation = handle._execution_lease.generation

        # Simulate an expired worker lease at the durable store boundary. The
        # second dispatcher must claim a newer generation before it can write.
        services.invocations.connection.execute(
            "UPDATE invocation_ownership SET expires_at=0 WHERE invocation_id=?",
            (handle.invocation_id,),
        )
        services.invocations.connection.commit()
        current_handle = services.invocations.get(handle.invocation_id)
        result = await engines[1].dispatch_initial_delivery(current_handle, authorize)
        assert result.status == "completed" and result.output == original_input
        assert deliveries == [{"input": original_input}]
        assert services.initial_deliveries.get(current_handle).status == "settled"
        assert model.calls == []
        committed = {
            "invocation": services.invocations.connection.execute(
                "SELECT * FROM invocations WHERE invocation_id=?", (handle.invocation_id,)
            ).fetchall(),
            "ownership": services.invocations.connection.execute(
                "SELECT * FROM invocation_ownership WHERE invocation_id=?",
                (handle.invocation_id,),
            ).fetchall(),
            "delivery": services.initial_deliveries.connection.execute(
                "SELECT * FROM initial_invocation_deliveries WHERE invocation_id=?",
                (handle.invocation_id,),
            ).fetchall(),
            "effects": services.effect_journal.connection.execute(
                "SELECT i.identity,e.payload FROM workflow_effect_invocations i "
                "JOIN workflow_effects e ON e.identity=i.identity "
                "WHERE i.invocation_id=? ORDER BY i.identity",
                (handle.invocation_id,),
            ).fetchall(),
        }
        new_generation = committed["ownership"][0][4]
        assert new_generation > old_generation

        release_stale.set()
        with pytest.raises(InvocationStateConflict):
            await stale_dispatch
        after_stale = {
            "invocation": services.invocations.connection.execute(
                "SELECT * FROM invocations WHERE invocation_id=?", (handle.invocation_id,)
            ).fetchall(),
            "ownership": services.invocations.connection.execute(
                "SELECT * FROM invocation_ownership WHERE invocation_id=?",
                (handle.invocation_id,),
            ).fetchall(),
            "delivery": services.initial_deliveries.connection.execute(
                "SELECT * FROM initial_invocation_deliveries WHERE invocation_id=?",
                (handle.invocation_id,),
            ).fetchall(),
            "effects": services.effect_journal.connection.execute(
                "SELECT i.identity,e.payload FROM workflow_effect_invocations i "
                "JOIN workflow_effects e ON e.identity=i.identity "
                "WHERE i.invocation_id=? ORDER BY i.identity",
                (handle.invocation_id,),
            ).fetchall(),
        }
        assert after_stale == committed
        assert deliveries == [{"input": original_input}]
        assert services.invocations.get(handle.invocation_id).output == original_input
    finally:
        release_stale.set()
        await asyncio.gather(*(engine.shutdown() for engine in engines), return_exceptions=True)
        services.close()
