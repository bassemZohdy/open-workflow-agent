"""Test-only worker interrupted by its owning parent at durable boundaries."""

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
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.effects import EffectIdentity
from open_workflow_agent.errors import OwaError
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


async def run(directory: Path, mode: str) -> None:
    scenario = json.loads((directory / "scenario.json").read_text(encoding="utf-8"))
    engine_name = scenario["engine"]
    boundary = scenario["boundary"]
    outcome = scenario["outcome"]
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
        "agent-framework": ("open_workflow_agent_agent_framework", "AgentFrameworkWorkflowEngine"),
    }[engine_name]
    engine = getattr(importlib.import_module(module), class_name)()
    await engine.initialize(services)
    available = (
        engine.native.task_nodes_available
        if engine_name == "langgraph"
        else engine.native.available
    )
    if not available:
        raise RuntimeError("native dependency environment is required")
    tasks = (
        [
            {"pause": {"wait": {"seconds": 300}}},
            {
                "report": {
                    "set": {"original": "${ $workflow.input }", "resumed": "${ $resume_input }"}
                }
            },
        ]
        if boundary == "wait"
        else [
            {
                "action": {
                    "call": "http",
                    "with": {
                        "method": "POST",
                        "endpoint": "https://service.test/action",
                        "body": "${ . }",
                    },
                }
            }
        ]
    )
    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "test",
                "name": "process-crash",
                "version": "1.0.0",
            },
            "do": tasks,
        }
    )
    original = {"request": "original"}
    identity: EffectIdentity | None = None
    if mode == "start":
        handle = services.invocations.create(
            engine=engine_name,
            session_id=None,
            user_id=None,
            workflow_name=plan.name,
            workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
        )
    else:
        saved_marker = json.loads((directory / "ready.json").read_text(encoding="utf-8"))
        recovered_handle = services.invocations.get(saved_marker["invocation_id"])
        if recovered_handle is None:
            raise RuntimeError("saved invocation is required")
        handle = recovered_handle

    def ready(error: Any = None) -> None:
        write_json(
            directory / "ready.json",
            {
                "pid": os.getpid(),
                "invocation_id": handle.invocation_id,
                "status": handle.status,
                "effect_key": identity.key if identity else None,
                "error": error,
                "native_available": available,
                "boundary": boundary,
            },
        )

    async def handler(request: httpx.Request) -> httpx.Response:
        with closing(sqlite3.connect(directory / "external.sqlite3")) as external:
            with external:
                external.execute(
                    "INSERT INTO calls(operation_id, body) VALUES (?, ?)",
                    (request.headers["Idempotency-Key"], request.content.decode()),
                )
        if mode == "start" and boundary == "pending":
            ready()
            await asyncio.Event().wait()
        return httpx.Response(
            503 if outcome == "fault" else 200,
            content=b"null" if outcome == "null" else b'{"accepted":true}',
            headers={"Content-Type": "application/json"},
        )

    services.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(handler)))
    services.tools.protocols = services.protocols
    real_call = engine.executor._call

    async def controlled_call(call: str, payload: Any, state: Any) -> Any:
        nonlocal identity
        identity = EffectIdentity(
            handle.invocation_id,
            plan.fingerprint,
            str(state.variables.get("_execution_reference", state.variables["_task_reference"])),
            state.execution_path,
            "call:" + call,
        )
        try:
            value = await real_call(call, payload, state)
        except OwaError as error:
            if mode == "start" and boundary == "receipt":
                ready(error.as_dict())
                await asyncio.Event().wait()
            raise
        if mode == "start" and boundary == "receipt":
            ready()
            await asyncio.Event().wait()
        return value

    engine.executor._call = controlled_call
    real_execute = engine.executor.execute

    async def controlled_execute(*args: Any, **kwargs: Any) -> Any:
        try:
            value = await real_execute(*args, **kwargs)
        except OwaError as error:
            if mode == "start" and boundary == "terminal":
                ready(error.as_dict())
                await asyncio.Event().wait()
            raise
        if mode == "start" and boundary == "terminal":
            ready()
            await asyncio.Event().wait()
        return value

    engine.executor.execute = controlled_execute
    try:
        if mode == "start":
            running = asyncio.create_task(engine.invoke(plan, handle, original))
            if boundary == "wait":
                while handle.status != "waiting":
                    if running.done():
                        await running
                        raise RuntimeError("worker did not reach waiting")
                    await asyncio.sleep(0.01)
                ready()
            await running
        else:
            result = await engine.resume(
                handle, None if boundary == "wait" else {"resume": "different"}, plan
            )
            duplicate = await engine.resume(handle, {"duplicate": True}, plan)
            saved_handle = services.invocations.get(handle.invocation_id)
            if saved_handle is None:
                raise RuntimeError("completed invocation is required")
            write_json(
                directory / "result.json",
                {
                    "pid": os.getpid(),
                    "result": result.as_dict(),
                    "duplicate": duplicate.as_dict(),
                    "original": services.invocations.read_input(handle),
                    "saved": saved_handle.status,
                },
            )
    finally:
        await engine.shutdown()
        services.close()
        write_json(directory / (mode + "-cleaned.json"), {"pid": os.getpid()})


if __name__ == "__main__":
    directory = Path(sys.argv[1])
    mode = sys.argv[2]
    try:
        asyncio.run(run(directory, mode))
    except Exception as error:
        write_json(
            directory / (mode + "-failure.json"),
            {
                "exception": type(error).__name__,
                "error": error.as_dict() if isinstance(error, OwaError) else None,
            },
        )
        raise SystemExit(1) from None
