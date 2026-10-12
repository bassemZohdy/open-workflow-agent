"""Deterministic timing worker killed by its parent before cleanup."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

from _process_recovery_worker import write_json
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.errors import InvocationStateConflict, ModelError
from open_workflow_agent.lifecycle import CancellationToken
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow


async def run(directory: Path, mode: str) -> None:
    scenario = json.loads((directory / "scenario.json").read_text())
    backend, boundary, engine_name = scenario["backend"], scenario["boundary"], scenario["engine"]
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    config = (
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}})
        if datasource
        else RuntimeConfig()
    )
    counter = directory / "calls.json"
    if not counter.exists():
        write_json(counter, {"calls": 0, "draws": 0})

    async def response(_prompt: Any) -> Any:
        counts = json.loads(counter.read_text())
        counts["calls"] += 1
        write_json(counter, counts)
        if boundary == "retry-delay" and counts["calls"] == 1:
            raise ModelError("first attempt fails")
        return {"done": True}

    services = RuntimeServices(
        config, model=FakeModel(response), database_root=None if datasource else directory
    )
    module, class_name = {
        "adk": ("open_workflow_agent_adk", "AdkWorkflowEngine"),
        "langgraph": ("open_workflow_agent_langgraph", "LangGraphWorkflowEngine"),
    }[engine_name]
    engine = getattr(importlib.import_module(module), class_name)()

    async def no_background_timer_delivery(_dispatcher: Any) -> None:
        # Original-deadline recovery is delivered explicitly below. Shared
        # PostgreSQL fixtures must not run unrelated timers through this model.
        return None

    with patch(
        "open_workflow_agent.timer_dispatcher.TimerDispatcher.start",
        new=no_background_timer_delivery,
    ):
        await engine.initialize(services)
    engine._execution_ownership_seconds = 5
    action = {"action": {"call": "llm:1.0.0@default", "with": {"prompt": "value"}}}
    workflow: dict[str, Any] = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "test",
            "name": "timing-process",
            "version": "1.0.0",
        },
        "do": [],
    }
    if boundary == "retry-delay":
        workflow["do"] = [
            {
                "attempt": {
                    "try": [action],
                    "catch": {
                        "retry": {
                            "delay": {"seconds": 6},
                            "jitter": {"from": {"milliseconds": 250}, "to": {"milliseconds": 750}},
                            "limit": {"attempt": {"count": 1}, "duration": {"seconds": 60}},
                        }
                    },
                }
            }
        ]
    else:
        group: dict[str, Any] = {"do": [{"pause": {"wait": {"seconds": 300}}}, action]}
        workflow["do"] = [{"frame": group}]
        target = workflow if boundary == "workflow-deadline" else group
        target["timeout"] = {"after": {"seconds": 4}}
    plan = compile_workflow(workflow)
    handle: Any = None
    deadline_evidence: dict[str, Any] = {}

    def ready() -> None:
        write_json(
            directory / "ready.json",
            {
                "pid": os.getpid(),
                "invocation_id": handle.invocation_id,
                "boundary": boundary,
                **deadline_evidence,
            },
        )

    def draw(lower: float, upper: float) -> float:
        if mode == "recover":
            raise AssertionError("retry jitter redrawn during process recovery")
        counts = json.loads(counter.read_text())
        counts["draws"] += 1
        write_json(counter, counts)
        assert lower == 0.25 and upper == 0.75
        return 0.5

    engine.executor._retry_random = draw
    original_sleep = CancellationToken.sleep

    async def mark_sleep(token: CancellationToken, seconds: float) -> None:
        if mode == "start" and boundary == "retry-delay":
            ready()
        await original_sleep(token, seconds)

    method_name = "sleep"
    setattr(CancellationToken, method_name, mark_sleep)
    try:
        if mode == "start":
            handle = services.invocations.create(
                engine=engine_name,
                session_id=None,
                user_id=None,
                workflow_name=plan.name,
                workflow_version=plan.version,
                workflow_fingerprint=plan.fingerprint,
            )
            task = asyncio.create_task(engine.invoke(plan, handle, {"original": True}))
            if boundary != "retry-delay":
                result = await asyncio.wait_for(task, timeout=30)
                saved = services.invocations.get(handle.invocation_id)
                if result.status != "waiting" or saved is None or saved.status != "waiting":
                    raise AssertionError(result)
                if (
                    handle.invocation_id in engine._active
                    or handle.invocation_id in engine._ownership_tasks
                    or handle._execution_lease is not None
                ):
                    raise AssertionError("waiting execution ownership was not released")
                registrations = services.timers.resumable_for_invocation(
                    handle.invocation_id, plan.fingerprint
                )
                assert len(registrations) == 1
                registration = registrations[0]
                assert registration.deadline_at is not None
                assert registration.duration == 300
                deadline_evidence.update(
                    timer_key=registration.timer_key,
                    deadline_at=registration.deadline_at,
                    due_at=registration.due_at,
                )
                ready()
                # Stay outside the acknowledged runner until the parent kills
                # this PID; normal cancellation still reaches the cleanup marker.
                await asyncio.Event().wait()
            else:
                await task
        else:
            info = json.loads((directory / "ready.json").read_text())
            handle = services.invocations.get(info["invocation_id"])
            async with asyncio.timeout(20):
                if boundary != "retry-delay":
                    services.timers.verify_invocation(handle.invocation_id, plan.fingerprint)
                    registration = services.timers.registration_for_key(info["timer_key"])
                    assert registration.identity.invocation_id == handle.invocation_id
                    assert registration.identity.workflow_fingerprint == plan.fingerprint
                    assert registration.deadline_at == info["deadline_at"]
                    assert registration.due_at == info["due_at"]
                    assert registration.deadline_at is not None
                    while engine.executor._absolute_clock() < registration.deadline_at:
                        await asyncio.sleep(0.01)
                    observed = engine.executor._absolute_clock()
                    assert observed >= registration.deadline_at
                    assert max(0, registration.deadline_at - observed) == 0
                    assert services.timers.registration_for_key(info["timer_key"]) == registration
                    deadline_evidence.update(
                        deadline_at=registration.deadline_at, remaining=0, observed_at=observed
                    )
                    # The autonomous poller may already have committed the fault.
                    handle = services.invocations.get(info["invocation_id"])
                    assert handle.status not in {"completed", "cancelled"}
                while True:
                    try:
                        result = await engine.resume(handle, {"continue": True}, plan)
                        break
                    except InvocationStateConflict:
                        await asyncio.sleep(0.05)
            counts = json.loads(counter.read_text())
            write_json(
                directory / "result.json",
                {"result": result.as_dict(), "counts": counts, "deadline": deadline_evidence},
            )
    finally:
        setattr(CancellationToken, method_name, original_sleep)
        await engine.shutdown()
        services.close()
        write_json(directory / (mode + "-cleaned.json"), True)


if __name__ == "__main__":
    directory = Path(sys.argv[1])
    mode = sys.argv[2]
    try:
        asyncio.run(run(directory, mode))
    except BaseException as exc:
        write_json(
            directory / (mode + "-failure.json"), {"type": type(exc).__name__, "message": str(exc)}
        )
        raise
