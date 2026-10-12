"""Actual process-stop event worker; no paid providers or graceful crash cleanup."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from _process_recovery_worker import write_json
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.errors import InvocationStateConflict, InvocationStateError
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow


async def run(directory: Path, mode: str) -> None:
    scenario = json.loads((directory / "scenario.json").read_text(encoding="utf-8"))
    engine_name, boundary, subject = scenario["engine"], scenario["boundary"], scenario["subject"]
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if scenario["backend"] == "postgresql" else None
    config = (
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}})
        if datasource
        else RuntimeConfig()
    )
    counter = directory / "calls.json"
    if not counter.exists():
        write_json(counter, {"calls": 0})

    async def response(prompt: Any) -> Any:
        counts = json.loads(counter.read_text(encoding="utf-8"))
        counts["calls"] += 1
        write_json(counter, counts)
        if boundary == "foreach-outcome":
            assert isinstance(prompt, dict) and "prompt" in prompt
            return {"processed": prompt["prompt"]}
        return {"done": True}

    services = RuntimeServices(
        config, model=FakeModel(response), database_root=None if datasource else directory
    )
    store = getattr(services, "event_store", None)
    if boundary != "legacy-scalar":
        assert store is not None

    async def publish(kind: str, value: int) -> None:
        await services.event_bus.publish(
            {
                "id": f"{subject}-{value}",
                "source": "urn:contract:process-events",
                "type": kind,
                "subject": subject,
                "data": {"order": "chosen", "value": value},
            },
            default_source="urn:unused",
        )

    if mode == "publish":
        try:
            if boundary == "waiting":
                await publish("fixture.first", 1)
            elif boundary == "partial-all":
                await publish("fixture.second", 2)
            write_json(directory / "published.json", {"pid": os.getpid()})
        finally:
            services.close()
        return
    adapter_module, class_name = {
        "adk": ("open_workflow_agent_adk", "AdkWorkflowEngine"),
        "langgraph": ("open_workflow_agent_langgraph", "LangGraphWorkflowEngine"),
    }[engine_name]
    engine = getattr(importlib.import_module(adapter_module), class_name)()
    await engine.initialize(services)
    engine._execution_ownership_seconds = 5
    first = {
        "with": {"type": "fixture.first", "subject": subject},
        "correlate": {"order": {"from": "${ .data.order }"}},
    }
    if boundary == "legacy-scalar":
        first.pop("correlate")
    second = {
        "with": {"type": "fixture.second", "subject": subject},
        "correlate": {"order": {"from": "${ .data.order }"}},
    }
    listener: dict[str, Any] = {
        "listen": {
            "to": {"all": [first, second]}
            if boundary in {"partial-all", "foreach-outcome"}
            else {"one": first}
        }
    }
    if boundary == "legacy-scalar":
        tasks = [
            {"events": listener},
            {"pause": {"wait": {"seconds": 300}}},
            {"action": {"call": "llm:1.0.0@default", "with": {"prompt": "finish"}}},
        ]
    elif boundary == "foreach-outcome":
        listener["foreach"] = {
            "do": [
                {"action": {"call": "llm:1.0.0@default", "with": {"prompt": "${ $item.value }"}}}
            ]
        }
        tasks = [{"events": listener}, {"result": {"set": {"items": "${ . }"}}}]
    else:
        tasks = [
            {"events": listener},
            {"action": {"call": "llm:1.0.0@default", "with": {"prompt": "finish"}}},
        ]
    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "events",
                "name": "event-process",
                "version": "1.0.0",
            },
            "do": tasks,
        }
    )
    handle: Any = None
    legacy_output: Any = None

    def ready() -> None:
        core_source = sys.modules[RuntimeServices.__module__].__file__
        assert core_source is not None
        write_json(
            directory / "ready.json",
            {
                "pid": os.getpid(),
                "invocation_id": handle.invocation_id,
                "boundary": boundary,
                "legacy_output": legacy_output,
                "core_source": str(Path(core_source).resolve()),
            },
        )

    original_listen, original_call = engine.executor._run_listen, engine.executor._call

    async def after_selection(*args: Any, **kwargs: Any) -> Any:
        nonlocal legacy_output
        value = await original_listen(*args, **kwargs)
        if boundary == "legacy-scalar":
            legacy_output = value
        if mode == "start" and boundary == "selected":
            ready()
            await asyncio.Event().wait()
        return value

    async def after_action(*args: Any, **kwargs: Any) -> Any:
        value = await original_call(*args, **kwargs)
        if mode == "start" and boundary == "foreach-outcome":
            ready()
            await asyncio.Event().wait()
        return value

    engine.executor._run_listen, engine.executor._call = after_selection, after_action
    try:
        if mode in {"start", "legacy-start"}:
            handle = services.invocations.create(
                engine=engine_name,
                session_id=None,
                user_id=None,
                workflow_name=plan.name,
                workflow_version=plan.version,
                workflow_fingerprint=plan.fingerprint,
            )
            if boundary not in {"waiting", "legacy-scalar"}:
                await publish("fixture.first", 1)
            if boundary == "foreach-outcome":
                await publish("fixture.second", 2)
            task = asyncio.create_task(engine.invoke(plan, handle, {}))
            if boundary == "legacy-scalar":
                while handle.status != "waiting":
                    if task.done():
                        raise AssertionError(await task)
                    await asyncio.sleep(0.01)
                await publish("fixture.first", 1)
                while legacy_output is None or handle.status != "waiting":
                    if task.done():
                        raise AssertionError(await task)
                    await asyncio.sleep(0.01)
                ready()
            elif boundary in {"waiting", "partial-all"}:
                assert store is not None
                while True:
                    saved = services.invocations.get(handle.invocation_id)
                    selected = store.connection.execute(
                        "SELECT s.ordinal FROM event_selections s "
                        "JOIN event_subscriptions r ON r.identity = s.subscription "
                        "WHERE r.invocation_id = ?",
                        (handle.invocation_id,),
                    ).fetchall()
                    if (
                        saved is not None
                        and saved.status == "waiting"
                        and (boundary == "waiting" or selected)
                    ):
                        ready()
                        break
                    if task.done():
                        raise AssertionError(await task)
                    await asyncio.sleep(0.01)
            await task
        else:
            info = json.loads((directory / "ready.json").read_text(encoding="utf-8"))
            handle = services.invocations.get(info["invocation_id"])
            assert handle is not None
            original_parent = handle._persisted_payload
            async with asyncio.timeout(30):
                while True:
                    try:
                        result = await engine.resume(handle, None, plan)
                        break
                    except InvocationStateConflict:
                        await asyncio.sleep(0.05)
                    except InvocationStateError as error:
                        if boundary != "legacy-scalar":
                            raise
                        saved = services.invocations.get(handle.invocation_id)
                        assert saved is not None
                        write_json(
                            directory / "result.json",
                            {
                                "rejection": error.code,
                                "unchanged_parent": saved._persisted_payload == original_parent,
                                "counts": json.loads(counter.read_text(encoding="utf-8")),
                                "pid": os.getpid(),
                            },
                        )
                        return
            assert boundary != "legacy-scalar", (
                "predecessor scalar checkpoint was silently accepted"
            )
            write_json(
                directory / "result.json",
                {
                    "result": result.as_dict(),
                    "counts": json.loads(counter.read_text(encoding="utf-8")),
                    "pid": os.getpid(),
                },
            )
    finally:
        await engine.shutdown()
        services.close()
        write_json(directory / (mode + "-cleaned.json"), True)


if __name__ == "__main__":
    directory, mode = Path(sys.argv[1]), sys.argv[2]
    try:
        asyncio.run(run(directory, mode))
    except BaseException as exc:
        write_json(
            directory / (mode + "-failure.json"), {"type": type(exc).__name__, "message": str(exc)}
        )
        raise
