"""Test-only native detached queue worker, stopped by its owning test process."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import sqlite3
import sys
from contextlib import closing
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import httpx
from _process_recovery_worker import write_json
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.child_dispatcher import ChildDispatcher
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.effects import EffectIdentity
from open_workflow_agent.errors import InvocationStateConflict
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.timer_dispatcher import TimerDispatcher
from open_workflow_agent.workflow import compile_workflow


async def run(directory: Path, mode: str) -> None:
    scenario = json.loads((directory / "scenario.json").read_text(encoding="utf-8"))
    engine_name, boundary, lifetime = scenario["engine"], scenario["boundary"], scenario["lifetime"]
    original = scenario["original"]
    saved = None if mode == "start" else json.loads((directory / "ready.json").read_text())

    async def no_automatic_delivery(_dispatcher: TimerDispatcher) -> None:
        return None

    # Producer and recovery must never dispatch another PG fixture's queued work
    # through this scenario's fake transport/model. Every delivery below is scoped.
    TimerDispatcher.start = no_automatic_delivery  # type: ignore[assignment]
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if scenario["backend"] == "postgresql" else None
    config = RuntimeConfig.model_validate(
        {
            "persistence": {"datasource": datasource},
            "child_workflows": {"max_depth": 2, "max_children": 1, "max_concurrency": 1},
        }
    )
    model = FakeModel()
    services = RuntimeServices(config, model=model, database_root=None if datasource else directory)
    module, class_name = {
        "adk": ("open_workflow_agent_adk", "AdkWorkflowEngine"),
        "langgraph": ("open_workflow_agent_langgraph", "LangGraphWorkflowEngine"),
        "agent-framework": ("open_workflow_agent_agent_framework", "AgentFrameworkWorkflowEngine"),
    }[engine_name]
    engine = getattr(importlib.import_module(module), class_name)()
    child_source: dict[str, Any] = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "detached-stop",
            "name": "child",
            "version": "1.0.0",
        },
        "do": [
            {
                "action": {
                    "call": "http",
                    "with": {
                        "method": "POST",
                        "endpoint": "https://child.test/action",
                        "body": "${ $workflow.input }",
                    },
                }
            }
        ],
    }
    services.workflow_catalog.register(child_source)
    if mode == "recover":
        services.workflow_catalog.register(
            {
                "document": {**child_source["document"], "version": "2.0.0"},
                "do": [{"changed": {"set": {"wrong_version": True}}}],
            }
        )
    parent_tasks: list[dict[str, Any]] = [
        {
            "start": {
                "run": {
                    "await": False,
                    "workflow": {
                        "namespace": "detached-stop",
                        "name": "child",
                        "version": "latest",
                    },
                }
            }
        }
    ]
    if lifetime == "cancel":
        parent_tasks.append({"pause": {"wait": {"seconds": 300}}})
    parent_plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "detached-stop",
                "name": "parent",
                "version": "1.0.0",
            },
            "do": parent_tasks,
        }
    )
    parent: Any = None
    decision: EffectIdentity | None = None
    dispatcher = ChildDispatcher(services, engine)
    with closing(sqlite3.connect(directory / "external.sqlite3")) as external:
        external.execute("CREATE TABLE IF NOT EXISTS calls(operation_id TEXT,body TEXT)")
        external.commit()

    async def handler(request: httpx.Request) -> httpx.Response:
        with closing(sqlite3.connect(directory / "external.sqlite3")) as external:
            with external:
                external.execute(
                    "INSERT INTO calls(operation_id,body) VALUES (?,?)",
                    (
                        request.headers["Idempotency-Key"],
                        request.content.decode(),
                    ),
                )
        return httpx.Response(200, json={"accepted": json.loads(request.content)})

    services.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(handler)))
    services.tools.protocols = services.protocols

    def evidence() -> dict[str, Any]:
        assert parent is not None and decision is not None
        receipt = services.effect_journal.get(decision)
        assert receipt is not None and receipt.status == "completed"
        child = services.invocations.get(receipt.output["child_id"])
        assert child is not None
        parent_record = services.invocations.get(parent.invocation_id)
        assert parent_record is not None
        run_identity = replace(decision, operation="run:workflow")
        binding = services.children.get(run_identity)
        assert binding is not None
        assert child.parent_invocation_id == parent.invocation_id
        assert child.parent_task_reference == "/do/0/start"
        assert child.owner_principal == parent_record.owner_principal == "detached-stop-owner"
        assert child.user_id == parent_record.user_id == scenario["original"]["request"]
        assert receipt.output["lifetime"] == "independent"
        budget_used = services.effect_journal.connection.execute(
            "SELECT used FROM child_workflow_budgets WHERE root_id = ?",
            (parent.invocation_id,),
        ).fetchone()[0]
        binding_count = services.effect_journal.connection.execute(
            "SELECT COUNT(*) FROM child_workflow_bindings WHERE root_id = ?",
            (parent.invocation_id,),
        ).fetchone()[0]
        return {
            "pid": os.getpid(),
            "native_available": True,
            "boundary": boundary,
            "parent_id": parent.invocation_id,
            "parent_status": parent_record.status,
            "parent_output": parent_record.output,
            "child_id": child.invocation_id,
            "child_status": child.status,
            "child_output": child.output,
            "child_input": services.invocations.read_input(child),
            "child_version": child.workflow_version,
            "child_fingerprint": child.workflow_fingerprint,
            "binding_key": binding.key,
            "request_digest": binding.request_digest,
            "binding_count": binding_count,
            "budget_used": budget_used,
            "decision": asdict(decision),
            "decision_receipt": asdict(receipt),
            "model_calls": len(model.calls),
        }

    def ready() -> None:
        write_json(directory / "ready.json", evidence())

    try:
        await engine.initialize(services)
        engine._execution_ownership_seconds = 5
        available = (
            engine.native.task_nodes_available
            if engine_name == "langgraph"
            else engine.native.available
        )
        assert available, "actual native SDK is required"
        original_notify, original_call = engine.executor._notify_receipt, engine.executor._call

        async def notify(state: Any, identity: EffectIdentity, receipt: Any) -> None:
            nonlocal decision
            if identity.operation == "policy:detached-child:v1":
                decision = identity
                if mode == "start" and boundary == "admission":
                    # The queue, immutable child input and decision receipt have
                    # committed; the native receipt frame has not been appended.
                    ready()
                    await asyncio.Event().wait()
            await original_notify(state, identity, receipt)

        async def call(call_name: str, payload: Any, state: Any) -> Any:
            value = await original_call(call_name, payload, state)
            if mode == "start" and boundary == "child-receipt":
                child_identity = EffectIdentity(
                    state.metadata["invocation_id"],
                    state.metadata["_workflow_fingerprint"],
                    str(
                        state.variables.get(
                            "_execution_reference", state.variables["_task_reference"]
                        )
                    ),
                    state.execution_path,
                    "call:" + call_name,
                )
                known = services.effect_journal.get(child_identity)
                assert known is not None and known.status == "completed"
                ready()
                await asyncio.Event().wait()
            return value

        engine.executor._notify_receipt, engine.executor._call = notify, call
        if mode == "start":
            parent = services.invocations.create(
                engine=engine_name,
                session_id=None,
                user_id=original["request"],
                owner_principal="detached-stop-owner",
                workflow_name=parent_plan.name,
                workflow_version=parent_plan.version,
                workflow_fingerprint=parent_plan.fingerprint,
            )
            result = await engine.invoke(parent_plan, parent, original)
            assert result.status == ("completed" if lifetime == "complete" else "waiting"), (
                result.error
            )
            if lifetime == "cancel":
                await engine.cancel(parent)
            if boundary == "parent-terminal":
                ready()
                await asyncio.Event().wait()
            assert decision is not None
            await dispatcher.tick(job_key=decision.key)
            async with asyncio.timeout(40):
                while not (directory / "ready.json").exists():
                    assert not dispatcher._errors, tuple(str(error) for error in dispatcher._errors)
                    await asyncio.sleep(0.01)
            await asyncio.Event().wait()
        assert saved is not None
        parent = services.invocations.get(saved["parent_id"])
        assert parent is not None
        decision = EffectIdentity.from_value(saved["decision"])
        if boundary == "admission":
            async with asyncio.timeout(45):
                while parent.status == "running":
                    try:
                        if lifetime == "complete":
                            await engine.resume(
                                parent, {"replacement": "must-not-be-used"}, parent_plan
                            )
                        else:
                            await engine.cancel(parent)
                    except InvocationStateConflict:
                        pass
                    services.invocations.refresh(parent)
                    await asyncio.sleep(0.05)
        assert parent.status == ("completed" if lifetime == "complete" else "cancelled"), (
            parent.error
        )
        async with asyncio.timeout(45):
            while True:
                current = evidence()
                if current["child_status"] in {"completed", "cancelled", "faulted"}:
                    break
                await dispatcher.tick(job_key=decision.key)
                await asyncio.sleep(0.05)
        assert current["child_status"] == "completed", list(dispatcher._errors)
        await dispatcher.tick(job_key=decision.key)
        async with asyncio.timeout(5):
            while dispatcher._workers:
                await asyncio.sleep(0.01)
        current = evidence()
        current["decision_unchanged"] = current["decision_receipt"] == saved["decision_receipt"]
        current["dispatcher_errors"] = [
            str(error)
            for error in dispatcher._errors
            if not isinstance(error, InvocationStateConflict)
        ]
        write_json(directory / "result.json", current)
    finally:
        await dispatcher.stop()
        await engine.shutdown()
        services.close()
        write_json(directory / (mode + "-cleaned.json"), {"pid": os.getpid()})


if __name__ == "__main__":
    task_directory, task_mode = Path(sys.argv[1]), sys.argv[2]
    try:
        asyncio.run(run(task_directory, task_mode))
    except BaseException as error:
        write_json(
            task_directory / (task_mode + "-failure.json"),
            {
                "pid": os.getpid(),
                "error": repr(error),
                "type": type(error).__name__,
            },
        )
        raise
