"""Test-only known child receipt recovery across host drain or actual OS stop."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import sqlite3
import sys
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx
from _process_recovery_worker import write_json
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.effects import EffectIdentity
from open_workflow_agent.errors import InvocationStateConflict
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.timer_dispatcher import TimerDispatcher
from open_workflow_agent.workflow import compile_workflow


async def run(directory: Path, mode: str) -> None:
    scenario = json.loads((directory / "scenario.json").read_text())
    saved = None if mode == "start" else json.loads((directory / "ready.json").read_text())
    engine_name = scenario["engine"]

    async def explicit_delivery(_dispatcher: TimerDispatcher) -> None:
        return None

    TimerDispatcher.start = explicit_delivery  # type: ignore[assignment]
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if scenario["backend"] == "postgresql" else None
    model = FakeModel()
    services = RuntimeServices(
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}}),
        model=model,
        database_root=None if datasource else directory,
    )
    module, class_name = {
        "adk": ("open_workflow_agent_adk", "AdkWorkflowEngine"),
        "langgraph": ("open_workflow_agent_langgraph", "LangGraphWorkflowEngine"),
        "agent-framework": ("open_workflow_agent_agent_framework", "AgentFrameworkWorkflowEngine"),
    }[engine_name]
    engine = getattr(importlib.import_module(module), class_name)()
    child_plan = services.workflow_catalog.register(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "host-drain",
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
    )
    parent_plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "host-drain",
                "name": "parent",
                "version": "1.0.0",
            },
            "do": [
                {
                    "child": {
                        "run": {
                            "workflow": {
                                "namespace": "host-drain",
                                "name": "child",
                                "version": "1.0.0",
                            }
                        }
                    }
                }
            ],
        }
    )
    with closing(sqlite3.connect(directory / "external.sqlite3")) as external:
        external.execute("CREATE TABLE IF NOT EXISTS calls(operation_id TEXT,body TEXT)")
        external.commit()

    async def action(request: httpx.Request) -> httpx.Response:
        with closing(sqlite3.connect(directory / "external.sqlite3")) as external:
            with external:
                external.execute(
                    "INSERT INTO calls(operation_id,body) VALUES (?,?)",
                    (request.headers["Idempotency-Key"], request.content.decode()),
                )
        return httpx.Response(200, json={"accepted": json.loads(request.content)})

    services.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(action)))
    services.tools.protocols = services.protocols
    invocation: asyncio.Task[Any] | None = None
    parent: Any = None
    child_id: str | None = None
    identity: EffectIdentity | None = None
    try:
        await engine.initialize(services)
        assert (
            engine.native.task_nodes_available
            if engine_name == "langgraph"
            else engine.native.available
        )
        engine._execution_ownership_seconds = 5
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
            original_notify = engine.executor._notify_receipt

            async def notify(state: Any, effect: EffectIdentity, receipt: Any) -> None:
                nonlocal child_id, identity
                await original_notify(state, effect, receipt)
                if effect.operation == "call:http":
                    assert receipt.status == "completed"
                    child_id, identity = effect.invocation_id, effect
                    write_json(
                        directory / "ready.json",
                        {
                            "pid": os.getpid(),
                            "native_available": True,
                            "parent_id": parent.invocation_id,
                            "child_id": child_id,
                            "fingerprint": parent_plan.fingerprint,
                            "original": scenario["original"],
                            "effect": asdict(effect),
                            "receipt": asdict(receipt),
                        },
                    )
                    await asyncio.Event().wait()

            engine.executor._notify_receipt = notify
            if engine_name == "langgraph":
                from open_workflow_agent_langgraph.graph import LangGraphScopeExecutor

                original_run_scope = LangGraphScopeExecutor.run_scope

                async def run_scope(scope_executor: Any, *args: Any, **kwargs: Any) -> None:
                    await original_run_scope(scope_executor, *args, **kwargs)
                    if (
                        scope_executor.plan.fingerprint != child_plan.fingerprint
                        or kwargs.get("prefix") != "/do"
                    ):
                        return
                    child_effect = next(
                        (
                            dependency.identity
                            for dependency in scope_executor.receipts.values()
                            if dependency.identity.operation == "call:http"
                            and dependency.identity.invocation_id == scope_executor.invocation_id
                        ),
                        None,
                    )
                    if child_effect is None:
                        return
                    child_receipt = services.effect_journal.get(child_effect)
                    assert child_receipt is not None and child_receipt.status == "completed"
                    child_id = scope_executor.invocation_id
                    write_json(
                        directory / "ready.json",
                        {
                            "pid": os.getpid(),
                            "native_available": True,
                            "parent_id": parent.invocation_id,
                            "child_id": child_id,
                            "fingerprint": parent_plan.fingerprint,
                            "original": scenario["original"],
                            "effect": asdict(child_effect),
                            "receipt": asdict(child_receipt),
                        },
                    )
                    await asyncio.Event().wait()

                LangGraphScopeExecutor.run_scope = run_scope  # type: ignore[method-assign]
            invocation = asyncio.create_task(
                engine.invoke(parent_plan, parent, scenario["original"])
            )
            async with asyncio.timeout(60):
                while not (directory / "ready.json").exists():
                    assert not invocation.done(), "producer ended before receipt boundary"
                    await asyncio.sleep(0.01)
            while not (directory / "drain.request").exists():
                await asyncio.sleep(0.02)
            async with asyncio.timeout(30):
                await engine.shutdown()
            outcome = await asyncio.gather(invocation, return_exceptions=True)
            assert isinstance(outcome[0], asyncio.CancelledError)
            assert (
                not engine._active and not engine._ownership_tasks and not engine._execution_leases
            )
            assert child_id is not None
            saved_parent = services.invocations.get(parent.invocation_id)
            saved_child = services.invocations.get(child_id)
            assert saved_parent is not None and saved_child is not None
            write_json(
                directory / "start-cleaned.json",
                {
                    "pid": os.getpid(),
                    "parent_status": saved_parent.status,
                    "child_status": saved_child.status,
                },
            )
            return
        assert saved is not None
        identity = EffectIdentity.from_value(saved["effect"])
        child_id = saved["child_id"]
        child = services.invocations.get(child_id)
        parent = services.invocations.get(saved["parent_id"])
        assert child is not None and parent is not None
        assert child.status == parent.status == "running"
        async with asyncio.timeout(45):
            child_result = None
            while child_result is None:
                try:
                    child_result = await engine.resume(child, {"replacement": True}, child_plan)
                except InvocationStateConflict:
                    await asyncio.sleep(0.05)
        assert child_result.status == "completed", child_result.error
        async with asyncio.timeout(45):
            parent_result = None
            while parent_result is None:
                try:
                    parent_result = await engine.resume(parent, {"replacement": True}, parent_plan)
                except InvocationStateConflict:
                    await asyncio.sleep(0.05)
        assert parent_result.status == "completed", parent_result.error
        receipt = services.effect_journal.get(identity)
        assert receipt is not None
        write_json(
            directory / "result.json",
            {
                "pid": os.getpid(),
                "parent_id": parent.invocation_id,
                "child_id": child.invocation_id,
                "parent_status": parent_result.status,
                "child_status": child_result.status,
                "output": parent_result.output,
                "fingerprint": parent.workflow_fingerprint,
                "original": services.invocations.read_input(parent),
                "child_input": services.invocations.read_input(child),
                "budget_used": services.children.connection.execute(
                    "SELECT used FROM child_workflow_budgets WHERE root_id=?",
                    (parent.invocation_id,),
                ).fetchone()[0],
                "model_calls": len(model.calls),
                "receipt_unchanged": asdict(receipt) == saved["receipt"],
            },
        )
    finally:
        await engine.shutdown()
        if invocation is not None and not invocation.done():
            invocation.cancel()
            await asyncio.gather(invocation, return_exceptions=True)
        services.close()


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
