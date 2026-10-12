"""Original deadlines survive native waits and a fresh runtime process boundary."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from engine_cases import engine_cases
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.errors import InvocationStateError
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow


def _services(path: Path, backend: str, model: FakeModel) -> RuntimeServices:
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    config = (
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}})
        if datasource
        else RuntimeConfig()
    )
    return RuntimeServices(config, model=model, database_root=None if datasource else path)


def _policy_records(services: RuntimeServices, invocation_id: str) -> list[tuple[str, str]]:
    rows = services.effect_journal.connection.execute(
        "SELECT i.identity, i.payload, e.payload FROM workflow_effect_invocations i "
        "JOIN workflow_effects e ON e.identity = i.identity "
        "WHERE i.invocation_id = ? ORDER BY i.identity",
        (invocation_id,),
    ).fetchall()
    return [
        (row[0], row[2]) for row in rows if json.loads(row[1])["operation"].startswith("policy:")
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "engine_name,engine_type", [case for case in engine_cases() if case[0] in {"adk", "langgraph"}]
)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize(
    "boundary,expired,damage",
    [
        ("workflow", False, "clean"),
        ("workflow", True, "clean"),
        ("task", False, "clean"),
        ("task", True, "clean"),
        ("workflow", False, "root-missing"),
        ("workflow", False, "root-changed"),
        ("task", False, "root-missing"),
        ("task", False, "deadline-changed"),
    ],
)
async def test_original_deadline_is_reused_after_native_waiting_restart(
    tmp_path: Path,
    engine_name: str,
    engine_type: Any,
    backend: str,
    boundary: str,
    expired: bool,
    damage: str,
) -> None:
    model = FakeModel({"done": True})
    services = _services(tmp_path, backend, model)
    engine = engine_type()
    available = (
        engine.native.available if engine_name == "adk" else engine.native.task_nodes_available
    )
    if not available:
        services.close()
        pytest.skip("independent native engine environment is required")
    group: dict[str, Any] = {
        "do": [
            {"pause": {"wait": {"seconds": 10}}},
            {"result": {"call": "llm:1.0.0@default", "with": {"prompt": "finish"}}},
        ]
    }
    workflow: dict[str, Any] = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "test",
            "name": "deadline-recovery",
            "version": "1.0.0",
        },
        "do": [{"frame": group}],
    }
    if boundary == "workflow":
        workflow["timeout"] = {"after": {"seconds": 30}}
    else:
        group["timeout"] = {"after": {"seconds": 30}}
    plan = compile_workflow(workflow)
    await engine.initialize(services)
    engine.executor._absolute_clock = lambda: 1000.0
    handle = services.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    running = asyncio.create_task(engine.invoke(plan, handle, {"original": True}))
    before: list[tuple[str, str]] = []
    try:
        async with asyncio.timeout(10):
            while True:
                saved = services.invocations.get(handle.invocation_id)
                if saved is not None and saved.status == "waiting":
                    break
                if running.done():
                    raise AssertionError(await running)
                await asyncio.sleep(0.01)
        before = _policy_records(services, handle.invocation_id)
        assert len(model.calls) == 0
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(running, timeout=10)
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()
        services.close()

    restarted = _services(tmp_path, backend, model)
    resumed = engine_type()
    await resumed.initialize(restarted)
    # Leave room for real SDK/storage work on slow Windows runners while
    # checking the exact original remaining budget rather than a fresh one.
    resumed.executor._absolute_clock = lambda: 1031.0 if expired else 1010.0
    remaining_budgets: list[float] = []
    original_timed = resumed.executor._run_timed

    async def record_timed(
        operation: Callable[[], Awaitable[Any]],
        seconds: float | None,
        message: str,
        details: dict[str, Any],
    ) -> Any:
        if seconds is not None:
            remaining_budgets.append(seconds)
        return await original_timed(operation, seconds, message, details)

    resumed.executor._run_timed = record_timed
    persisted = restarted.invocations.get(handle.invocation_id)
    assert persisted is not None
    try:
        original_parent = persisted._persisted_payload
        if damage != "clean":
            target = next(
                (key, value)
                for key, value in before
                if (
                    json.loads(value)["output"]["profile"]
                    == ("owa/deadline/v1" if damage == "deadline-changed" else "owa/timing/v1")
                )
            )
            if damage == "root-missing":
                restarted.effect_journal.connection.execute(
                    "DELETE FROM workflow_effects WHERE identity = ?", (target[0],)
                )
            else:
                payload = json.loads(target[1])
                payload["output"]["started_at"] += 10000
                restarted.effect_journal.connection.execute(
                    "UPDATE workflow_effects SET payload = ? WHERE identity = ?",
                    (json.dumps(payload), target[0]),
                )
            restarted.effect_journal.connection.commit()
            with pytest.raises(InvocationStateError):
                await resumed.resume(persisted, {"continue": True}, plan)
            assert len(model.calls) == 0
            saved = restarted.invocations.get(handle.invocation_id)
            assert saved is not None and saved._persisted_payload == original_parent
            return
        result = await asyncio.wait_for(
            resumed.resume(persisted, {"continue": True}, plan), timeout=10
        )
        assert remaining_budgets and remaining_budgets[0] == (0.0 if expired else 20.0)
        if expired:
            assert result.status == "faulted", result.output
            assert result.error and "timed out" in result.error["message"]
            assert len(model.calls) == 0
        else:
            assert result.status == "completed", result.error
            assert result.output == {"done": True}
            assert len(model.calls) == 1
        assert len(before) == (1 if boundary == "workflow" else 2)
        assert _policy_records(restarted, handle.invocation_id) == before
    finally:
        await resumed.shutdown()
        restarted.close()
