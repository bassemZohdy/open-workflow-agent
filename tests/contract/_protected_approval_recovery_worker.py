"""Actual worker stopped with protected request/decision proof durably admitted."""

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
from _protected_approval_fixtures import (
    actor,
    approval_snapshot,
    definition,
    expiry,
    handle,
    identities,
    runtime,
    waiting,
)
from _recovery_failure_artifact import failure_artifact
from _version_recovery_worker import native_snapshot
from open_workflow_agent.errors import ApprovalAuthorizationError, OwaError
from open_workflow_agent.protected_approval_store import REQUEST_POLICY
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.workflow import compile_workflow

RAW = {"original": None, "nested": [True, False]}


def ledger(directory):
    with sqlite3.connect(directory / "business.sqlite3") as connection:
        return connection.execute("SELECT body FROM commits ORDER BY rowid").fetchall()


async def run(directory, mode):
    scenario = json.loads((directory / "scenario.json").read_text(encoding="utf-8"))
    module, classname = {
        "adk": ("open_workflow_agent_adk", "AdkWorkflowEngine"),
        "langgraph": ("open_workflow_agent_langgraph", "LangGraphWorkflowEngine"),
    }[scenario["engine"]]
    engine = getattr(importlib.import_module(module), classname)()
    assert (
        engine.native.available
        if scenario["engine"] == "adk"
        else engine.native.task_nodes_available
    )
    services = runtime(directory, scenario["backend"])
    with sqlite3.connect(directory / "business.sqlite3") as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS commits(body TEXT NOT NULL)")

    async def business(request):
        assert request.method == "POST" and str(request.url) == "https://business.test/action"
        with sqlite3.connect(directory / "business.sqlite3") as connection:
            connection.execute("INSERT INTO commits(body) VALUES (?)", (request.content.decode(),))
        return httpx.Response(200, json={"committed": True})

    services.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(business)))
    services.tools.protocols = services.protocols
    await engine.initialize(services)
    # Keep a useful renewal window while the producer reaches its durable
    # approval barrier. Recovery waits for this original lease to expire after
    # the real process stop instead of assuming the subsecond lease survived
    # arbitrary native SDK and hosted-runner scheduling delays.
    engine._execution_ownership_seconds = 5.0 if mode == "start" else 30.0
    await services.stop_protected_approvals()
    try:
        if mode == "start":
            reached = asyncio.Event()
            original_receive = services.event_bus.receive_next

            async def park_before_selection(*args, **kwargs):
                reached.set()
                await asyncio.Event().wait()
                return await original_receive(*args, **kwargs)

            if scenario["case"] != "expired":
                services.event_bus.receive_next = park_before_selection
            due = expiry(30 if scenario["case"] == "expired" else 120)
            plan = compile_workflow(definition(scenario["subject"], due))
            invocation = handle(services, scenario["engine"], plan)
            task = asyncio.create_task(engine.invoke(plan, invocation, RAW))
            await waiting(services, invocation, task)
            if scenario["case"] != "expired":
                principal = actor(services, "reviewer-a")
                if scenario["case"] in {"reassign", "escalate"}:
                    target = "reviewer-b" if scenario["case"] == "reassign" else "lead"
                    await services.approvals.mutate_protected(
                        scenario["subject"],
                        action=scenario["case"],
                        actor=principal,
                        revision=1,
                        operation_key=scenario["subject"] + "-assignment",
                        assignee=target,
                    )
                else:
                    status = "rejected" if scenario["case"] == "rejected" else "approved"
                    await services.approvals.decide(
                        scenario["subject"],
                        decision=status,
                        operator_id=principal.identity,
                        actor=principal,
                        value=None,
                        revision=1,
                        operation_key=scenario["subject"] + "-decision",
                    )
                await asyncio.wait_for(reached.wait(), 30)
            record = services.approvals.store.get(scenario["subject"])
            assert not ledger(directory)
            ids = identities(services, invocation.invocation_id)
            origin = next(identity for identity in ids if identity.operation == REQUEST_POLICY)
            write_json(
                directory / "ready.json",
                {
                    "pid": os.getpid(),
                    "invocation_id": invocation.invocation_id,
                    "approval_id": scenario["subject"],
                    "expiry": record.expires_at,
                    "request_policy": origin.key,
                    "original_input": services.invocations.read_input(invocation),
                    "backend": services.effect_journal.connection.kind,
                },
            )
            while not (directory / "ack.json").exists():
                if task.done():
                    raise AssertionError(await task)
                await asyncio.sleep(0.01)
            assert json.loads((directory / "ack.json").read_text())["pid"] == os.getpid()
            write_json(directory / "acknowledged.json", {"pid": os.getpid()})
            await asyncio.Event().wait()
            return
        ready = json.loads((directory / "ready.json").read_text(encoding="utf-8"))
        invocation = services.invocations.get(ready["invocation_id"])
        assert invocation is not None
        assert services.effect_journal.connection.kind == scenario["backend"]
        assert services.invocations.read_input(invocation) == RAW
        assert services.approvals.store.get(scenario["subject"]).expires_at == ready["expiry"]
        damaged = scenario["case"] in {"request-row", "policy", "audit"}
        store, connection = services.approvals.protected_store, services.effect_journal.connection
        if scenario["case"] == "request-row":
            connection.execute(
                "DELETE FROM " + store.requests + " WHERE approval_id=?", (scenario["subject"],)
            )
        elif scenario["case"] == "policy":
            connection.execute(
                "DELETE FROM workflow_effects WHERE identity=?", (ready["request_policy"],)
            )
            connection.execute(
                "DELETE FROM workflow_effect_invocations WHERE identity=?",
                (ready["request_policy"],),
            )
        elif scenario["case"] == "audit":
            connection.execute(
                "UPDATE " + store.audit + " SET digest=? WHERE approval_id=?",
                ("f" * 64, scenario["subject"]),
            )
        connection.commit()
        before = approval_snapshot(services, scenario["subject"], invocation.invocation_id)
        native_before = native_snapshot(services, invocation, scenario["backend"])
        async with asyncio.timeout(45):
            while services.execution_ownership.connection.execute(
                "SELECT 1 FROM invocation_ownership WHERE invocation_id=? AND expires_at>"
                + services.execution_ownership._clock,
                (invocation.invocation_id,),
            ).fetchone():
                await asyncio.sleep(0.02)
        if scenario["case"] == "expired":
            async with asyncio.timeout(40):
                while time.time() < ready["expiry"]:
                    await asyncio.sleep(0.01)
            await services.start_protected_approvals()
        if scenario["case"] in {"reassign", "escalate"}:
            target = "reviewer-b" if scenario["case"] == "reassign" else "lead"
            record = services.approvals.store.get(scenario["subject"])
            assert record.status == "pending" and record.assignee == target
            assert record.assignment_revision == 2
            original_actor = actor(services, "reviewer-a")
            target_actor = actor(services, target)
            try:
                await services.approvals.decide(
                    scenario["subject"],
                    decision="approved",
                    operator_id=original_actor.identity,
                    actor=original_actor,
                    value=None,
                    revision=2,
                    operation_key=scenario["subject"] + "-wrong-actor",
                )
            except ApprovalAuthorizationError:
                pass
            else:
                raise AssertionError("previous assignee retained decision authority after recovery")
            await services.approvals.decide(
                scenario["subject"],
                decision="approved",
                operator_id=target_actor.identity,
                actor=target_actor,
                value=None,
                revision=2,
                operation_key=scenario["subject"] + "-decision",
            )
        if damaged:
            try:
                await asyncio.wait_for(
                    engine.resume(
                        invocation,
                        {"changed": True},
                        services.resolve_original_definition(invocation),
                    ),
                    30,
                )
            except OwaError as error:
                assert (
                    approval_snapshot(services, scenario["subject"], invocation.invocation_id)
                    == before
                )
                assert native_snapshot(services, invocation, scenario["backend"]) == native_before
                assert not ledger(directory) and services.model.calls == []
                write_json(
                    directory / "result.json",
                    {
                        "pid": os.getpid(),
                        "producer_pid": ready["pid"],
                        "rejected": True,
                        "code": error.code,
                        "before_unchanged": True,
                        "expiry": ready["expiry"],
                        "business_count": 0,
                    },
                )
            else:
                raise AssertionError("damaged protected proof resumed")
        else:
            result = await asyncio.wait_for(
                engine.resume(
                    invocation, {"changed": True}, services.resolve_original_definition(invocation)
                ),
                30,
            )
            expected = (
                "approved"
                if scenario["case"] in {"approved", "reassign", "escalate"}
                else "rejected"
                if scenario["case"] == "rejected"
                else "expired"
            )
            record = services.approvals.store.get(scenario["subject"])
            assert result.status == "completed" and record.status == expected
            assert record.expires_at == ready["expiry"] and services.model.calls == []
            calls = ledger(directory)
            assert len(calls) == (1 if expected == "approved" else 0)
            if calls:
                assert json.loads(calls[0][0])["request"] == RAW
            assert (
                await engine.resume(
                    invocation, {}, services.resolve_original_definition(invocation)
                )
                == result
            )
            assert len(ledger(directory)) == len(calls)
            write_json(
                directory / "result.json",
                {
                    "pid": os.getpid(),
                    "producer_pid": ready["pid"],
                    "status": expected,
                    "expiry": record.expires_at,
                    "business_count": len(calls),
                    "original_input": services.invocations.read_input(invocation),
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
            failure_artifact(error, stage=mode, include_traceback=True),
        )
        raise
