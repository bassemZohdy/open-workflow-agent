"""Actual producer stopped after native wait; original authority survives reopen."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import httpx
from _process_recovery_worker import write_json
from _recovery_failure_artifact import failure_artifact
from open_workflow_agent.errors import OwaError
from open_workflow_agent.execution_authority import authority_scope, current_authority
from open_workflow_agent.workflow import compile_workflow
from test_execution_authority_isolation import (
    CATALOG,
    ENDPOINT,
    RAW,
    actual_native,
    authority,
    corrupt,
    create,
    definition,
    identities,
    invoke_as,
    runtime,
    snapshot,
)


def ledger(directory):
    with sqlite3.connect(directory / "business.sqlite3", timeout=3) as db:
        return db.execute(
            "SELECT actor,body,operation_key,pid FROM commits ORDER BY rowid"
        ).fetchall()


async def run(directory, mode):
    scenario = json.loads((directory / "scenario.json").read_text(encoding="utf-8"))
    name = scenario["engine"]
    module, cls = {
        "adk": ("open_workflow_agent_adk", "AdkWorkflowEngine"),
        "langgraph": ("open_workflow_agent_langgraph", "LangGraphWorkflowEngine"),
    }[name]
    engine = getattr(importlib.import_module(module), cls)()
    actual_native(engine, name)
    with sqlite3.connect(directory / "business.sqlite3") as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS commits("
            "actor TEXT,body TEXT,operation_key TEXT,pid INTEGER)"
        )

    pending_sent = asyncio.Event()

    async def dispatch(request):
        assert request.method == "POST" and str(request.url) == ENDPOINT
        actor = current_authority().principal.identity
        with sqlite3.connect(directory / "business.sqlite3", timeout=3) as db:
            db.execute(
                "INSERT INTO commits VALUES (?,?,?,?)",
                (
                    actor,
                    request.content.decode(),
                    request.headers["Idempotency-Key"],
                    os.getpid(),
                ),
            )
        if mode == "start" and scenario["case"] == "pending":
            pending_sent.set()
            await asyncio.Event().wait()
        return httpx.Response(200, json={"actor": actor, "request": json.loads(request.content)})

    services = runtime(directory / "runtime", scenario["backend"], dispatch=dispatch)
    await engine.initialize(services)
    await engine._timer_dispatcher.stop()
    try:
        if mode == "start":
            public = definition(
                [
                    {"first": {"call": CATALOG, "with": {"input": "${ . }"}}},
                    {"pause": {"wait": {"seconds": 1}}},
                    {
                        "last": {
                            "call": "http",
                            "with": {
                                "method": "POST",
                                "endpoint": ENDPOINT,
                                "body": "${ $workflow.input }",
                            },
                        }
                    },
                ],
                name=scenario["name"],
            )
            if scenario["case"] == "pending":
                public["do"].pop(1)
            plan = compile_workflow(public)
            handle = create(services, name, plan)
            execution = asyncio.create_task(invoke_as(engine, services, plan, handle))
            if scenario["case"] == "pending":
                await asyncio.wait_for(pending_sent.wait(), 40)
                assert len(ledger(directory)) == 1 and not execution.done()
                due_at = None
            else:
                result = await execution
                assert result.status == "waiting", result.error
                assert ledger(directory) == []
                timers = services.timers.pending_for_invocation(
                    handle.invocation_id, plan.fingerprint
                )
                assert len(timers) == 1
                due_at = timers[0].due_at
            assert len(services.model.calls) == 1
            original = services.execution_authorizations.verify_invocation(handle)
            write_json(
                directory / "ready.json",
                {
                    "pid": os.getpid(),
                    "invocation_id": handle.invocation_id,
                    "backend": services.effect_journal.connection.kind,
                    "fingerprint": plan.fingerprint,
                    "authority": original.as_value(),
                    "original_input": services.invocations.read_input(handle),
                    "due_at": due_at,
                    "session_id": handle.session_id,
                    "user_id": handle.user_id,
                    "native": snapshot(services, handle, scenario["backend"])[1],
                },
            )
            while not (directory / "ack.json").exists():
                await asyncio.sleep(0.01)
            assert json.loads((directory / "ack.json").read_text())["pid"] == os.getpid()
            write_json(directory / "acknowledged.json", {"pid": os.getpid()})
            await asyncio.Event().wait()
        else:
            ready = json.loads((directory / "ready.json").read_text())
            handle = services.invocations.get(ready["invocation_id"])
            plan = services.resolve_original_definition(handle)
            assert handle.session_id == ready["session_id"] and handle.user_id == ready["user_id"]
            assert plan.fingerprint == ready["fingerprint"]
            if scenario["case"] != "resume":
                corrupt(services, handle, scenario["case"])
                before = snapshot(services, handle, scenario["backend"])
                with authority_scope(authority(services, "bob")):
                    try:
                        await engine.resume(handle, {"replace": "caller"}, plan)
                    except OwaError:
                        pass
                    else:
                        raise AssertionError("damaged original authority accepted")
                assert snapshot(services, handle, scenario["backend"]) == before
                assert len(ledger(directory)) == (1 if scenario["case"] == "pending" else 0)
                assert services.model.calls == []
                write_json(
                    directory / "result.json",
                    {
                        "pid": os.getpid(),
                        "producer_pid": ready["pid"],
                        "rejected": True,
                        "unchanged": True,
                        "business_count": len(ledger(directory)),
                        "due_at": ready["due_at"],
                        "original_input": services.invocations.read_input(handle),
                    },
                )
            else:
                async with asyncio.timeout(20):
                    while time.time() < ready["due_at"]:
                        await asyncio.sleep(0.01)
                with authority_scope(authority(services, "bob")):
                    result = await engine.resume(handle, {"replace": "caller"}, plan)
                assert result.status == "completed", result.error
                assert result.output == {"actor": "alice", "request": RAW}
                original = services.execution_authorizations.verify_invocation(handle)
                assert original.as_value() == ready["authority"] and services.model.calls == []
                business = next(
                    identity
                    for identity in identities(services, handle)
                    if identity.operation == "call:http"
                )
                receipt = services.effect_journal.get(business)
                records = ledger(directory)
                assert len(records) == 1 and records[0][0] == "alice"
                assert records[0][2] == receipt.operation_id and records[0][3] == os.getpid()
                assert await engine.resume(handle, {}, plan) == result
                assert ledger(directory) == records
                write_json(
                    directory / "result.json",
                    {
                        "pid": os.getpid(),
                        "producer_pid": ready["pid"],
                        "status": result.status,
                        "authority": original.as_value(),
                        "business_count": len(records),
                        "operation_key": receipt.operation_id,
                        "due_at": ready["due_at"],
                        "original_input": services.invocations.read_input(handle),
                    },
                )
    finally:
        await engine.shutdown()
        services.close()
        if mode == "start":
            write_json(directory / "start-cleaned.json", {"pid": os.getpid()})


if __name__ == "__main__":
    directory, mode = Path(sys.argv[1]), sys.argv[2]
    try:
        asyncio.run(run(directory, mode))
    except BaseException as error:
        write_json(
            directory / (mode + "-failure.json"),
            failure_artifact(error, stage=mode),
        )
        raise
