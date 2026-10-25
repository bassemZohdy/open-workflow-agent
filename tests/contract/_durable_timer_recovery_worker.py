"""Owned native OS-stop timer/slot worker with deterministic external outcomes."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

import httpx
import pytest
from _process_recovery_worker import write_json
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.errors import InvocationStateConflict
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.scheduling import WorkflowScheduler
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import WorkflowExecutor, compile_workflow


async def run(directory: Path, mode: str) -> None:
    scenario = json.loads((directory / "scenario.json").read_text(encoding="utf-8"))
    engine_name, boundary = scenario["engine"], scenario["boundary"]
    saved = None if mode == "start" else json.loads((directory / "ready.json").read_text())
    now = 1000.0 if mode == "start" else 1020.0 if boundary == "waiting" else 1160.0
    original_initialize = WorkflowExecutor.__init__

    def initialize(executor: WorkflowExecutor, *args: Any, **kwargs: Any) -> None:
        original_initialize(executor, *args, **kwargs)
        executor._absolute_clock = lambda: now

    clock_patch = pytest.MonkeyPatch()
    clock_patch.setattr(WorkflowExecutor, "__init__", initialize)
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if scenario["backend"] == "postgresql" else None
    config = (
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}})
        if datasource
        else RuntimeConfig()
    )
    services = RuntimeServices(
        config, model=FakeModel(), database_root=None if datasource else directory
    )
    module, class_name = {
        "adk": ("open_workflow_agent_adk", "AdkWorkflowEngine"),
        "langgraph": ("open_workflow_agent_langgraph", "LangGraphWorkflowEngine"),
    }[engine_name]
    engine = getattr(importlib.import_module(module), class_name)()
    handle: Any = None
    schedule_id: str | None = None
    slot_claim: Any = None
    registration: Any = None
    plan_source: dict[str, Any] = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "timers",
            "name": "timer-process",
            "version": "1.0.0",
        },
        "do": [
            {
                "before": {
                    "call": "http",
                    "with": {
                        "method": "POST",
                        "endpoint": "https://service.test/action",
                        "body": {"phase": "before", "original": "${ $workflow.input }"},
                    },
                }
            },
            {"pause": {"wait": {"seconds": 60}}},
            {
                "after": {
                    "call": "http",
                    "with": {
                        "method": "POST",
                        "endpoint": "https://service.test/action",
                        "body": {"phase": "after", "original": "${ $workflow.input }"},
                    },
                }
            },
        ],
    }
    if boundary.startswith("slot-"):
        plan_source["schedule"] = {"after": {"seconds": 10}}
    plan = compile_workflow(plan_source)
    original = {"request": scenario["subject"], "original": "must-survive"}
    with closing(sqlite3.connect(directory / "external.sqlite3")) as external:
        external.execute("CREATE TABLE IF NOT EXISTS calls(operation_id TEXT,body TEXT)")
        external.commit()

    async def handler(request: httpx.Request) -> httpx.Response:
        with closing(sqlite3.connect(directory / "external.sqlite3")) as external:
            with external:
                external.execute(
                    "INSERT INTO calls(operation_id,body) VALUES (?,?)",
                    (request.headers["Idempotency-Key"], request.content.decode()),
                )
        return httpx.Response(200, json={"accepted": True})

    services.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(handler)))
    services.tools.protocols = services.protocols

    def ready() -> None:
        write_json(
            directory / "ready.json",
            {
                "pid": os.getpid(),
                "boundary": boundary,
                "native_available": True,
                "invocation_id": handle.invocation_id if handle is not None else None,
                "schedule_id": schedule_id,
                "occurrence_key": slot_claim.occurrence_key if slot_claim is not None else None,
                "claim_generation": slot_claim.generation if slot_claim is not None else None,
                "fingerprint": plan.fingerprint,
                "timer_key": registration.timer_key if registration is not None else None,
                "due_at": registration.due_at if registration is not None else None,
                "original": original,
                "nonresident": handle is not None
                and handle.invocation_id not in engine._active
                and handle.invocation_id not in engine._ownership_tasks
                and handle._execution_lease is None,
            },
        )

    try:
        await engine.initialize(services)
        engine._execution_ownership_seconds = 10
        available = (
            engine.native.task_nodes_available
            if engine_name == "langgraph"
            else engine.native.available
        )
        assert available, "actual native SDK is required"
        dispatcher = getattr(engine, "_timer_dispatcher", None)
        assert dispatcher is not None
        await dispatcher.stop()
        scheduler = WorkflowScheduler(services, engine, clock=lambda: now)
        original_notify, original_call = engine.executor._notify_receipt, engine.executor._call

        async def notify(state: Any, identity: Any, receipt: Any) -> None:
            await original_notify(state, identity, receipt)
            if (
                mode == "start"
                and boundary == "wake-receipt"
                and identity.operation == "wait:wake:v1"
            ):
                assert receipt.status == "completed"
                ready()
                await asyncio.Event().wait()

        async def call(call_name: str, payload: Any, state: Any) -> Any:
            value = await original_call(call_name, payload, state)
            if (
                mode == "start"
                and boundary == "downstream-receipt"
                and payload.get("body", {}).get("phase") == "after"
            ):
                ready()
                await asyncio.Event().wait()
            return value

        engine.executor._notify_receipt, engine.executor._call = notify, call
        if mode == "start":
            if boundary.startswith("slot-"):
                created = services.schedules.create(
                    plan,
                    original,
                    now=1000,
                    owner_principal="process-owner",
                    event_scope="process-owner",
                )
                schedule_id = created.schedule_id
                now = 1010.0
                selected = services.schedules.claim_due(
                    now=now, owner="killed-slot-owner", lease_seconds=30, schedule_id=schedule_id
                )
                assert selected is not None and selected._claim is not None
                slot_claim = selected._claim
                if boundary == "slot-bound":
                    handle = services.schedules.bind_invocation(
                        selected, engine=engine_name, now=now
                    )
                    assert services.invocations.read_input(handle) == original
                ready()
                await asyncio.Event().wait()
            handle = services.invocations.create(
                engine=engine_name,
                session_id=None,
                user_id=None,
                workflow_name=plan.name,
                workflow_version=plan.version,
                workflow_fingerprint=plan.fingerprint,
                owner_principal="process-owner",
            )
            result = await asyncio.wait_for(engine.invoke(plan, handle, original), timeout=30)
            assert result.status == "waiting", result.error
            registrations = services.timers.pending_for_invocation(
                handle.invocation_id, plan.fingerprint
            )
            assert len(registrations) == 1
            registration = registrations[0]
            assert registration.armed_at == 1000 and registration.due_at == 1060
            if boundary == "waiting":
                ready()
                assert json.loads((directory / "ready.json").read_text())["nonresident"]
                await asyncio.Event().wait()
            now = 1060.0
            await dispatcher.tick(now=now, invocation_id=handle.invocation_id)
            async with asyncio.timeout(30):
                while not (directory / "ready.json").exists():
                    assert not dispatcher._errors, tuple(str(error) for error in dispatcher._errors)
                    await asyncio.sleep(0.01)
            await asyncio.Event().wait()
        assert saved is not None
        if boundary.startswith("slot-"):
            schedule_id = saved["schedule_id"]
            assert schedule_id is not None
            selected = services.schedules.claim_due(
                now=now, owner="recovery-slot-owner", lease_seconds=30, schedule_id=schedule_id
            )
            assert selected is not None
            assert selected._claim is not None
            assert selected._claim.occurrence_key == saved["occurrence_key"]
            assert selected._claim.generation > saved["claim_generation"]
            slot_claim = selected._claim
            bound = services.schedules.bind_invocation(selected, engine=engine_name, now=now)
            if saved["invocation_id"] is not None:
                assert bound.invocation_id == saved["invocation_id"]
            handle = bound
            await scheduler._execute(selected)
            schedule_record = services.schedules.get(schedule_id)
            assert schedule_record is not None
            assert handle.invocation_id == schedule_record.last_invocation_id
            registrations = services.timers.pending_for_invocation(
                handle.invocation_id, plan.fingerprint
            )
            assert len(registrations) == 1
            registration = registrations[0]
            now = registration.due_at
        else:
            handle = services.invocations.get(saved["invocation_id"])
            assert handle is not None
            registration = services.timers.registration_for_key(saved["timer_key"])
            assert registration.due_at == saved["due_at"] == 1060
            if boundary == "waiting":
                assert registration.due_at - now == 40
                await dispatcher.tick(now=now, invocation_id=handle.invocation_id)
                waiting_handle = services.invocations.get(handle.invocation_id)
                assert waiting_handle is not None and waiting_handle.status == "waiting"
                with closing(sqlite3.connect(directory / "external.sqlite3")) as external:
                    assert external.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 1
                now = 1060.0
        async with asyncio.timeout(45):
            while True:
                current = services.invocations.get(handle.invocation_id)
                assert current is not None
                if current.status in {"completed", "faulted", "cancelled"}:
                    break
                try:
                    if boundary == "downstream-receipt":
                        await engine.resume(current, None, plan)
                    else:
                        await dispatcher.tick(now=now, invocation_id=handle.invocation_id)
                except InvocationStateConflict:
                    pass
                await asyncio.sleep(0.05)
        assert current.status == "completed", current.error
        duplicate = await engine.resume(current, {"duplicate": True}, plan)
        assert duplicate.status == "completed"
        write_json(
            directory / "result.json",
            {
                "pid": os.getpid(),
                "invocation_id": current.invocation_id,
                "fingerprint": current.workflow_fingerprint,
                "output": current.output,
                "original": services.invocations.read_input(current),
                "status": current.status,
                "due_at": registration.due_at,
                "armed_at": registration.armed_at,
                "timer_key": registration.timer_key,
                "occurrence_key": slot_claim.occurrence_key if slot_claim is not None else None,
                "claim_generation": slot_claim.generation if slot_claim is not None else None,
                "duplicate": duplicate.as_dict(),
            },
        )
    finally:
        if mode == "recover" and schedule_id is not None:
            services.schedules.cancel(schedule_id)
        await engine.shutdown()
        services.close()
        clock_patch.undo()
        write_json(directory / (mode + "-cleaned.json"), {"pid": os.getpid()})


if __name__ == "__main__":
    directory, mode = Path(sys.argv[1]), sys.argv[2]
    try:
        asyncio.run(run(directory, mode))
    except BaseException as exc:
        write_json(
            directory / (mode + "-failure.json"), {"type": type(exc).__name__, "message": str(exc)}
        )
        raise
