"""Actual native producer stopped by its parent at durable child boundaries."""

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
from open_workflow_agent.errors import InvocationCancelled, InvocationStateConflict
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.timer_dispatcher import TimerDispatcher
from open_workflow_agent.workflow import WorkflowExecutor, compile_workflow


async def run(directory: Path, mode: str) -> None:
    scenario = json.loads((directory / "scenario.json").read_text())
    engine_name, boundary = scenario["engine"], scenario["boundary"]
    deadline_boundary = boundary in {"parent-task-deadline", "parent-workflow-deadline"}
    saved = None if mode == "start" else json.loads((directory / "ready.json").read_text())
    now = (
        1000.0
        if mode == "start"
        else 1031.0
        if deadline_boundary
        else 1020.0
        if boundary == "parent-waiting"
        else 1060.0
    )
    patches = pytest.MonkeyPatch()
    original_initialize = WorkflowExecutor.__init__

    def initialize(executor: WorkflowExecutor, *args: Any, **kwargs: Any) -> None:
        original_initialize(executor, *args, **kwargs)
        executor._absolute_clock = lambda: now

    async def explicit_delivery(_dispatcher: TimerDispatcher) -> None:
        return None

    patches.setattr(WorkflowExecutor, "__init__", initialize)
    patches.setattr(TimerDispatcher, "start", explicit_delivery)
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if scenario["backend"] == "postgresql" else None
    services = RuntimeServices(
        RuntimeConfig.model_validate(
            {
                "persistence": {"datasource": datasource},
                # Recovery must retain the original deployment policy pinned
                # by the archive, including its child quota authority.
                "child_workflows": {"max_depth": 32, "max_children": 1000},
            }
        ),
        model=FakeModel(),
        database_root=None if datasource else directory,
    )
    module, class_name = {
        "adk": ("open_workflow_agent_adk", "AdkWorkflowEngine"),
        "langgraph": ("open_workflow_agent_langgraph", "LangGraphWorkflowEngine"),
    }[engine_name]
    engine = getattr(importlib.import_module(module), class_name)()
    parent: Any = None
    child: Any = None
    registration: Any = None
    timer: Any = None

    def action(phase: str) -> dict[str, Any]:
        return {
            "call": "http",
            "with": {
                "method": "POST",
                "endpoint": "https://child.test/action",
                "body": {"phase": phase, "original": "${ $workflow.input }"},
            },
        }

    child_plan = services.workflow_catalog.register(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "child-waits-process",
                "name": "child",
                "version": "1.0.0",
            },
            "do": [{"pause": {"wait": {"seconds": 60}}}, {"action": action("child")}],
        }
    )
    parent_source = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "child-waits-process",
            "name": "parent",
            "version": "1.0.0",
        },
        "do": [
            *([] if deadline_boundary else [{"before": action("parent-before")}]),
            {
                "child": {
                    "run": {
                        "workflow": {
                            "namespace": "child-waits-process",
                            "name": "child",
                            "version": "1.0.0",
                            "input": {
                                "request": "${ $workflow.input.request }",
                                "original": "${ $workflow.input.original }",
                            },
                        }
                    }
                }
            },
            {"after": action("parent-after")},
        ],
    }
    if boundary == "parent-task-deadline":
        parent_source["do"][0]["child"]["timeout"] = {"after": {"seconds": 30}}
    elif boundary == "parent-workflow-deadline":
        parent_source["timeout"] = {"after": {"seconds": 30}}
    parent_plan = compile_workflow(parent_source)
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
                "native_available": True,
                "parent_id": parent.invocation_id,
                "child_id": child.invocation_id,
                "run_key": registration.identity.key,
                "timer_key": timer.timer_key,
                "due_at": timer.due_at,
                "deadline_at": registration.suspension.deadline_at,
                "budget": services.children.connection.execute(
                    "SELECT max_depth,max_children,used,policy_digest "
                    "FROM child_workflow_budgets WHERE root_id=?",
                    (parent.invocation_id,),
                ).fetchone(),
                "fingerprint": parent_plan.fingerprint,
                "original": original,
                "nonresident": all(
                    handle.invocation_id not in engine._active
                    and handle.invocation_id not in engine._ownership_tasks
                    and handle._execution_lease is None
                    for handle in (parent, child)
                ),
            },
        )

    async def drain(dispatcher: Any) -> None:
        async with asyncio.timeout(35):
            while dispatcher._workers:
                await asyncio.sleep(0.01)
        assert not dispatcher._errors, tuple(str(error) for error in dispatcher._errors)

    try:
        await engine.initialize(services)
        engine._execution_ownership_seconds = 10
        native = engine.native
        assert native.task_nodes_available if engine_name == "langgraph" else native.available
        dispatcher = engine._timer_dispatcher
        original_notify = engine.executor._notify_receipt

        async def notify(state: Any, identity: Any, receipt: Any) -> None:
            await original_notify(state, identity, receipt)
            if (
                mode == "start"
                and boundary == "parent-receipt"
                and identity.operation == "run:workflow"
                and identity.invocation_id == parent.invocation_id
                and receipt.status == "completed"
            ):
                ready()
                await asyncio.Event().wait()

        engine.executor._notify_receipt = notify
        if mode == "start":
            parent = services.invocations.create(
                engine=engine_name,
                session_id=None,
                user_id="correlation",
                owner_principal="principal",
                workflow_name=parent_plan.name,
                workflow_version=parent_plan.version,
                workflow_fingerprint=parent_plan.fingerprint,
            )
            result = await asyncio.wait_for(
                engine.invoke(parent_plan, parent, original), timeout=35
            )
            assert result.status == "waiting", result.error
            registration = services.child_waits.for_invocation(
                parent.invocation_id, parent_plan.fingerprint
            )[0]
            child = services.invocations.get(registration.suspension.child_id)
            timer = services.timers.pending_for_invocation(
                child.invocation_id, child_plan.fingerprint
            )[0]
            assert child.status == "waiting" and timer.armed_at == 1000 and timer.due_at == 1060
            if boundary == "parent-waiting" or deadline_boundary:
                if deadline_boundary:
                    assert registration.suspension.deadline_at == 1030
                ready()
                assert json.loads((directory / "ready.json").read_text())["nonresident"]
                await asyncio.Event().wait()
            if boundary == "parent-cancel-control":
                control = services.invocations.get(parent.invocation_id)
                services.invocations.update(
                    control,
                    status="cancelled",
                    error=InvocationCancelled("operator cancelled parent").as_dict(),
                )
                assert services.invocations.get(child.invocation_id).status == "waiting"
                ready()
                await asyncio.Event().wait()
            now = 1060
            await dispatcher.tick(now=now, invocation_id=child.invocation_id)
            await drain(dispatcher)
            assert services.invocations.get(child.invocation_id).status == "completed"
            if boundary == "child-terminal":
                ready()
                await asyncio.Event().wait()
            await dispatcher.child_waits.tick(run_key=registration.identity.key)
            async with asyncio.timeout(35):
                while not (directory / "ready.json").exists():
                    assert not dispatcher.child_waits._errors, tuple(
                        str(error) for error in dispatcher.child_waits._errors
                    )
                    await asyncio.sleep(0.01)
            await asyncio.Event().wait()
        assert saved is not None
        parent = services.invocations.get(saved["parent_id"])
        child = services.invocations.get(saved["child_id"])
        assert parent is not None and child is not None
        registration = services.child_waits.registration_for_key(saved["run_key"])
        assert registration is not None and registration.suspension.child_id == child.invocation_id
        timer = services.timers.registration_for_key(saved["timer_key"])
        assert timer.due_at == saved["due_at"] == 1060
        if deadline_boundary:
            assert registration.suspension.deadline_at == saved["deadline_at"] == 1030
            with closing(sqlite3.connect(directory / "external.sqlite3")) as external:
                assert external.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 0
            result = await engine.resume(parent, {"must": "not-replace"}, parent_plan)
            assert result.status == "faulted" and result.error["details"]["status"] == 408
            assert services.invocations.get(child.invocation_id).status == "cancelled"
            assert (
                services.child_waits.known_wakes(parent.invocation_id, parent_plan.fingerprint)[
                    registration.identity.key
                ]["reason"]
                == "deadline"
            )
            now = 1060
            assert await dispatcher.tick(now=now, invocation_id=child.invocation_id) == 0
            assert services.timers.registration_for_key(timer.timer_key).due_at == 1060
            assert (
                services.child_waits.registration(registration.identity).suspension.deadline_at
                == 1030
            )
        if boundary == "parent-waiting":
            early = await engine.resume(parent, {"must": "not-replace"}, parent_plan)
            assert early.status == "waiting"
            assert services.invocations.get(child.invocation_id).status == "waiting"
            with closing(sqlite3.connect(directory / "external.sqlite3")) as external:
                assert external.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 1
            now = 1060
            await dispatcher.tick(now=now, invocation_id=child.invocation_id)
            await drain(dispatcher)
        if boundary == "parent-cancel-control":
            async with asyncio.timeout(35):
                while services.invocations.get(child.invocation_id).status != "cancelled":
                    await dispatcher.child_waits.tick()
                    await drain(dispatcher.child_waits)
                    await asyncio.sleep(0.05)
            assert not services.timers.pending_for_invocation(
                child.invocation_id, child.workflow_fingerprint
            )
        async with asyncio.timeout(50):
            while True:
                parent = services.invocations.get(saved["parent_id"])
                if parent.status in {"completed", "faulted", "cancelled"}:
                    break
                try:
                    await engine.resume(parent, None, parent_plan)
                except InvocationStateConflict:
                    pass
                await asyncio.sleep(0.05)
        assert parent.status == (
            "cancelled"
            if boundary == "parent-cancel-control"
            else "faulted"
            if deadline_boundary
            else "completed"
        ), parent.error
        duplicate = await engine.resume(parent, {"duplicate": True}, parent_plan)
        write_json(
            directory / "result.json",
            {
                "pid": os.getpid(),
                "parent_id": parent.invocation_id,
                "child_id": child.invocation_id,
                "run_key": registration.identity.key,
                "timer_key": timer.timer_key,
                "due_at": timer.due_at,
                "deadline_at": registration.suspension.deadline_at,
                "child_status": services.invocations.get(child.invocation_id).status,
                "error": parent.error,
                "budget": services.children.connection.execute(
                    "SELECT max_depth,max_children,used,policy_digest "
                    "FROM child_workflow_budgets WHERE root_id=?",
                    (parent.invocation_id,),
                ).fetchone(),
                "status": parent.status,
                "output": parent.output,
                "original": services.invocations.read_input(parent),
                "bindings": services.children.connection.execute(
                    "SELECT COUNT(*) FROM child_workflow_bindings WHERE root_id=?",
                    (parent.invocation_id,),
                ).fetchone()[0],
                "duplicate": duplicate.as_dict(),
            },
        )
    finally:
        await engine.shutdown()
        services.close()
        patches.undo()
        write_json(directory / (mode + "-cleaned.json"), {"pid": os.getpid()})


if __name__ == "__main__":
    directory, mode = Path(sys.argv[1]), sys.argv[2]
    try:
        asyncio.run(run(directory, mode))
    except BaseException as error:
        write_json(
            directory / (mode + "-failure.json"),
            {
                "type": type(error).__name__,
                "message": str(error),
                "details": getattr(error, "details", None),
            },
        )
        raise
