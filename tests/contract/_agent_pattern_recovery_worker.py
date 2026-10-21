"""Independent producer stopped at an actual role intent or known-receipt boundary."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import sqlite3
import sys
import traceback
from pathlib import Path

import httpx
import pytest
from _agent_pattern_fixtures import (
    actual_native,
    compiled,
    configuration,
    create,
    deployed_openapi_tool,
    expected,
    invoke,
    original_authority,
    providers,
    receipts,
    register_workers,
    retained,
    role_configuration,
    runtime,
    scripts,
    source,
)
from _process_recovery_worker import write_json
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.errors import OwaError
from open_workflow_agent.execution_authority import authority_scope, current_authority
from open_workflow_agent.execution_context import current_action_context
from open_workflow_agent.workflow import compile_workflow
from test_agent_patterns import QUESTION, approval_input, decision, waiting
from test_execution_authority_isolation import snapshot

STOP_ROLE = {
    "assistant": "assistant",
    "pipeline": "drafter",
    "multi_perspective": "synthesizer",
    "refine_until_good": "drafter",
    "expert_dispatch": "router",
    "team_coordinator": "synthesizer",
    "plan_and_execute": "planner",
    "approval_gate": "approver-action",
}


def ledger(directory):
    with sqlite3.connect(directory / "provider.sqlite3", timeout=3) as db:
        return db.execute("SELECT role,prompt,response,pid FROM calls ORDER BY rowid").fetchall()


def business_ledger(directory):
    with sqlite3.connect(directory / "provider.sqlite3", timeout=3) as db:
        return db.execute(
            "SELECT method,url,body,pid FROM business_requests ORDER BY rowid"
        ).fetchall()


def compile_openapi_wait(services):
    workflow = source("assistant")
    workflow["do"].append({"afterTool": {"wait": {"seconds": 3600}}})
    return compile_workflow(workflow, enabled_function_calls=services.role_function_calls)


async def run(directory, mode):
    scenario = json.loads((directory / "scenario.json").read_text(encoding="utf-8"))
    pattern, backend = scenario["pattern"], scenario["backend"]
    engine_name = scenario["engine"]
    module, cls = {
        "adk": ("open_workflow_agent_adk", "AdkWorkflowEngine"),
        "langgraph": ("open_workflow_agent_langgraph", "LangGraphWorkflowEngine"),
    }[engine_name]
    engine = getattr(importlib.import_module(module), cls)()
    actual_native(engine, engine_name)
    with sqlite3.connect(directory / "provider.sqlite3") as db:
        # No uniqueness constraint or deduplication: every provider call is observable.
        db.execute(
            "CREATE TABLE IF NOT EXISTS calls(role TEXT,prompt TEXT,response TEXT,pid INTEGER)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS business_requests "
            "(method TEXT,url TEXT,body BLOB,pid INTEGER)"
        )
    script = scripts(pattern)
    if scenario["boundary"] in {"openapi-pending", "openapi-known"}:
        script["assistant"] = [
            {"tool_call": {"name": "lookup", "arguments": {}}},
            {"answer": "assistant"},
        ]
    roles, _ = providers(pattern)
    tool_binding_change = scenario["boundary"] == "tool-change"
    if scenario["boundary"] == "policy-broaden":
        roles["drafter"]["policy"] = {
            "rules": [
                {
                    "actions": ["catalog.call", "model.complete"],
                    "resources": ["*"],
                    "roles": ["worker"],
                    "scopes": ["execute"],
                    "audience": ["runtime"],
                }
            ]
        }
    if scenario["boundary"] in {"openapi-pending", "openapi-known", "tool-change"}:
        roles["assistant"] = role_configuration("assistant", tools=["lookup"])
    services = None
    handle = None
    original = None

    async def park(identity, receipt):
        assert services is not None and handle is not None
        proof = retained(services, handle, backend)
        lease = handle._execution_lease
        if scenario["boundary"] == "openapi-known":
            # This boundary is an already-suspended invocation. Durable wait
            # completion releases its execution lease before the producer parks.
            assert lease is None
            lease_until = None
        else:
            assert lease is not None
            services.execution_ownership.verify(lease)
            lease_row = services.execution_ownership.connection.execute(
                "SELECT expires_at FROM invocation_ownership "
                "WHERE invocation_id=? AND owner=? AND generation=?",
                (handle.invocation_id, lease.owner, lease.generation),
            ).fetchone()
            assert lease_row is not None
            lease_until = lease_row[0]
        rows = ledger(directory)
        agent_status = next(
            (
                item.status
                for action_identity, item in receipts(services, handle)
                if action_identity.operation == "call:agent-assistant:1.0.0@default"
            ),
            None,
        )
        write_json(
            directory / "ready.json",
            {
                "pid": os.getpid(),
                "invocation_id": handle.invocation_id,
                "backend": backend,
                "source": dict(plan.source),
                "original_input": original,
                "authority": proof["authority"],
                "clock": proof["clock"],
                "receipt_key": identity.key,
                "receipt_status": receipt.status,
                "outer_agent_status": agent_status,
                "invocation_status": services.invocations.get(handle.invocation_id).status,
                "receipts": proof["receipts"],
                "native": proof["native"],
                "lease_until": lease_until,
                "provider_count": len(rows),
                "provider_counts": {name: sum(row[0] == name for row in rows) for name in script},
                "business_count": len(business_ledger(directory)),
            },
        )
        while not (directory / "ack.json").exists():
            await asyncio.sleep(0.01)
        assert json.loads((directory / "ack.json").read_text())["pid"] == os.getpid()
        write_json(directory / "acknowledged.json", {"pid": os.getpid()})
        await asyncio.Event().wait()

    async def response(name, prompt):
        assert current_authority().principal.identity == "alice"
        with sqlite3.connect(directory / "provider.sqlite3", timeout=3) as db:
            count = db.execute("SELECT COUNT(*) FROM calls WHERE role=?", (name,)).fetchone()[0]
            # An unintended replay calls beyond the script and fails instead of
            # hiding duplicate dispatch through an idempotent test receiver.
            assert count < len(script[name]), (name, count)
            output = script[name][count]
            db.execute(
                "INSERT INTO calls VALUES (?,?,?,?)",
                (
                    name,
                    json.dumps(prompt, sort_keys=True),
                    json.dumps(output, sort_keys=True),
                    os.getpid(),
                ),
            )
        if mode == "start" and scenario["boundary"] == "pending":
            context = current_action_context()
            assert context is not None
            receipt = services.effect_journal.get(context.identity)
            assert receipt is not None and receipt.status == "pending"
            await park(context.identity, receipt)
        return output

    models = {name: FakeModel(lambda prompt, name=name: response(name, prompt)) for name in script}

    async def dispatch(request):
        assert request.method == "GET"
        assert str(request.url) == "https://patterns.test/lookup"
        assert request.content == b""
        with sqlite3.connect(directory / "provider.sqlite3", timeout=3) as db:
            db.execute(
                "INSERT INTO business_requests VALUES (?,?,?,?)",
                (request.method, str(request.url), request.content, os.getpid()),
            )
            db.commit()
        if mode == "start" and scenario["boundary"] == "openapi-pending":
            context = current_action_context()
            assert context is not None
            assert context.identity.operation == "call:agent-assistant:1.0.0@default"
            receipt = services.effect_journal.get(context.identity)
            assert receipt is not None and receipt.status == "pending"
            await park(context.identity, receipt)
        return httpx.Response(200, json={"result": "read"})

    config = configuration(
        backend,
        roles,
        approvals=pattern == "approval_gate",
        **(
            {"tools": [deployed_openapi_tool("lookup", "GET")]}
            if scenario["boundary"] in {"openapi-pending", "openapi-known", "tool-change"}
            else {}
        ),
    )
    if mode == "recover":
        role = config.agent.roles[STOP_ROLE[pattern]]
        if scenario["boundary"] == "role-change":
            role.instruction += " Different deployment revision."
        elif scenario["boundary"] == "policy-change":
            role.policy = {
                "rules": [
                    {
                        "actions": ["catalog.call"],
                        "resources": ["*"],
                        "roles": ["worker"],
                    }
                ]
            }
        elif scenario["boundary"] == "policy-broaden":
            role.policy = {
                "rules": [
                    {
                        "actions": ["catalog.call", "model.complete", "tool.invoke"],
                        "resources": ["*"],
                        "roles": ["worker"],
                        "scopes": ["execute"],
                        "audience": ["runtime"],
                    }
                ]
            }
        elif scenario["boundary"] == "model-change":
            role.model.name += "-changed"
        elif tool_binding_change:
            role.tools = []
    services = runtime(
        directory / "runtime",
        backend,
        roles,
        models,
        config=config,
        dispatch=dispatch if scenario["boundary"] in {"openapi-pending", "openapi-known"} else None,
    )
    await engine.initialize(services)
    # A finite original lease, renewed normally by the genuine producer. Allow
    # multi-node native patterns enough time to reach their durable receipt
    # barrier on a loaded runner. Recovery waits for this exact recorded lease;
    # it never rewrites it.
    engine._execution_ownership_seconds = 15
    await engine._timer_dispatcher.stop()
    if pattern == "team_coordinator":
        register_workers(services)
    try:
        if mode == "start":
            plan = (
                compile_openapi_wait(services)
                if scenario["boundary"] == "openapi-known"
                else compiled(services, pattern)
            )
            original = approval_input() if pattern == "approval_gate" else dict(QUESTION)
            handle = create(services, engine_name, plan)
            original_notify = engine.executor._notify_receipt

            async def notify(state, identity, receipt):
                await original_notify(state, identity, receipt)
                if (
                    scenario["boundary"] not in {"pending", "openapi-pending", "openapi-known"}
                    and receipt.status == "completed"
                    and identity.invocation_id == handle.invocation_id
                    and identity.operation == "call:agent-" + STOP_ROLE[pattern] + ":1.0.0@default"
                ):
                    await park(identity, receipt)

            engine.executor._notify_receipt = notify
            if scenario["boundary"] == "openapi-known":
                original_notify_waiting = engine.executor._notify_waiting
                waiting_notified = asyncio.Event()
                waiting_states = []

                async def notify_waiting(state):
                    await original_notify_waiting(state)
                    waiting_states.append(state)
                    waiting_notified.set()

                engine.executor._notify_waiting = notify_waiting
            task = asyncio.create_task(invoke(engine, services, plan, handle, original))
            if scenario["boundary"] == "openapi-known":
                async with asyncio.timeout(30):
                    await waiting_notified.wait()
                    while services.invocations.get(handle.invocation_id).status != "waiting":
                        await asyncio.sleep(0.01)
                waiting_result = await asyncio.wait_for(task, timeout=30)
                assert waiting_result.status == "waiting"
                state = waiting_states[-1]
                identity = engine.executor._effect_identity(state, "wait:arm:v1")
                receipt = services.effect_journal.get(identity)
                assert receipt is not None and receipt.status == "completed"
                agent_receipt = next(
                    (
                        item
                        for action_identity, item in receipts(services, handle)
                        if action_identity.operation == "call:agent-assistant:1.0.0@default"
                    ),
                    None,
                )
                assert agent_receipt is not None and agent_receipt.status == "completed"
                assert services.invocations.get(handle.invocation_id).status == "waiting"
                await park(identity, receipt)
            if pattern == "approval_gate":
                await waiting(services, handle, task)
                assert models["approver-action"].calls == []
                await decision(services, original)
            await task
            raise AssertionError("producer passed its protected stop boundary")
        else:
            ready = json.loads((directory / "ready.json").read_text(encoding="utf-8"))
            handle = services.invocations.get(ready["invocation_id"])
            original = ready["original_input"]
            before_counts = {
                name: sum(row[0] == name for row in ledger(directory)) for name in script
            }
            if scenario["boundary"] in {
                "role-change",
                "policy-change",
                "policy-broaden",
                "model-change",
                "tool-change",
            }:
                plan = compiled(services, pattern)
                before = snapshot(services, handle, backend)
                with pytest.raises(OwaError):
                    await engine.resume(handle, {"question": "replacement"}, plan)
                assert snapshot(services, handle, backend) == before
                assert all(model.calls == [] for model in models.values())
                write_json(
                    directory / "result.json",
                    {
                        "pid": os.getpid(),
                        "producer_pid": ready["pid"],
                        "rejected": True,
                        "unchanged": True,
                        "provider_count": len(ledger(directory)),
                    },
                )
                return
            plan = services.resolve_original_definition(handle)
            async with asyncio.timeout(40):
                while True:
                    live = services.execution_ownership.connection.execute(
                        "SELECT 1 FROM invocation_ownership WHERE invocation_id=? AND expires_at>"
                        + services.execution_ownership._clock,
                        (handle.invocation_id,),
                    ).fetchone()
                    if live is None:
                        break
                    await asyncio.sleep(0.02)
            with authority_scope(original_authority(services)):
                result = await engine.resume(handle, {"question": "replacement"}, plan)
            rows = ledger(directory)
            if scenario["boundary"] == "pending":
                assert (
                    result.status == "faulted"
                    and result.error["code"] == "workflow_execution_error"
                )
                assert len(rows) == 1 and all(model.calls == [] for model in models.values())
                assert (
                    services.effect_journal.connection.execute(
                        "SELECT status FROM workflow_effects WHERE identity=?",
                        (ready["receipt_key"],),
                    ).fetchone()[0]
                    == "pending"
                )
            elif scenario["boundary"] == "openapi-pending":
                assert (
                    result.status == "faulted"
                    and result.error["code"] == "workflow_execution_error"
                )
                assert len(rows) == 1 and len(business_ledger(directory)) == 1
                assert all(model.calls == [] for model in models.values())
                assert (
                    services.effect_journal.connection.execute(
                        "SELECT status FROM workflow_effects WHERE identity=?",
                        (ready["receipt_key"],),
                    ).fetchone()[0]
                    == "pending"
                )
            elif scenario["boundary"] == "openapi-known":
                assert result.status == "completed" and result.output == expected(pattern), (
                    result.error
                )
                assert len(rows) == 2 and len(business_ledger(directory)) == 1
                assert all(model.calls == [] for model in models.values())
                assert (
                    services.effect_journal.connection.execute(
                        "SELECT status FROM workflow_effects WHERE identity=?",
                        (ready["receipt_key"],),
                    ).fetchone()[0]
                    == "completed"
                )
                assert await engine.resume(handle, {"late": True}, plan) == result
                assert len(ledger(directory)) == 2 and len(business_ledger(directory)) == 1
            else:
                assert result.status == "completed" and result.output == expected(pattern), (
                    result.error
                )
                for name in script:
                    assert sum(row[0] == name for row in rows) == (
                        0
                        if pattern == "expert_dispatch" and name == "finance"
                        else len(script[name])
                    )
                remaining = len(script[STOP_ROLE[pattern]]) - before_counts[STOP_ROLE[pattern]]
                assert len(models[STOP_ROLE[pattern]].calls) == remaining
                assert await engine.resume(handle, {}, plan) == result
                assert ledger(directory) == rows
            proof = retained(services, handle, backend)
            assert proof["clock"] == ready["clock"] and proof["authority"] == ready["authority"]
            assert proof["input"] == original
            for key, dependency in ready["receipts"].items():
                assert proof["receipts"][key] == dependency
            write_json(
                directory / "result.json",
                {
                    "pid": os.getpid(),
                    "producer_pid": ready["pid"],
                    "status": result.status,
                    "original_input": proof["input"],
                    "authority": proof["authority"],
                    "clock": proof["clock"],
                    "provider_count": len(rows),
                    "business_count": len(business_ledger(directory)),
                    "before_counts": before_counts,
                    "recovery_counts": {name: len(model.calls) for name, model in models.items()},
                    "provider_counts": {
                        name: sum(row[0] == name for row in rows) for name in script
                    },
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
            {
                "exception": type(error).__name__,
                "error": error.as_dict() if isinstance(error, OwaError) else None,
                "message": str(error),
                "traceback": traceback.format_exc(),
            },
        )
        raise
