"""Retry clocks, attempt admission and jitter survive fresh native recovery."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest
from engine_cases import engine_cases
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.errors import InvocationStateError, ModelError
from open_workflow_agent.lifecycle import CancellationToken
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "engine_name,engine_type", [case for case in engine_cases() if case[0] in {"adk", "langgraph"}]
)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize(
    "boundary", ["delay", "expired-delay", "outcome", "uncertain", "changed-schedule"]
)
async def test_retry_schedule_and_known_outcome_survive_restart(
    tmp_path: Path,
    monkeypatch: Any,
    engine_name: str,
    engine_type: Any,
    backend: str,
    boundary: str,
    explicit_workflow_delivery: None,
) -> None:
    now = 1000.0
    reached = asyncio.Event()
    model_calls = 0
    rng_calls: list[tuple[float, float]] = []
    sleeps: list[float] = []

    async def response(_prompt: Any) -> Any:
        nonlocal model_calls
        model_calls += 1
        if model_calls == 1:
            raise ModelError("transient failure")
        if boundary == "uncertain":
            reached.set()
            await asyncio.Event().wait()
        return {"done": True}

    model = FakeModel(response)
    services = _services(tmp_path, backend, model)
    engine = engine_type()
    available = (
        engine.native.available if engine_name == "adk" else engine.native.task_nodes_available
    )
    if not available:
        services.close()
        pytest.skip("independent native engine environment is required")
    workflow = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "test",
            "name": "retry-recovery",
            "version": "1.0.0",
        },
        "do": [
            {
                "attempt": {
                    "try": [{"action": {"call": "llm:1.0.0@default", "with": {"prompt": "value"}}}],
                    "catch": {
                        "retry": {
                            "delay": {"seconds": 10},
                            "jitter": {"from": {"milliseconds": 250}, "to": {"milliseconds": 750}},
                            "limit": {"attempt": {"count": 1}, "duration": {"seconds": 60}},
                        },
                        "do": [{"fallback": {"set": {"fallback": True}}}],
                    },
                }
            }
        ],
    }
    plan = compile_workflow(workflow)
    await engine.initialize(services)
    engine.executor._absolute_clock = lambda: now

    def draw(lower: float, upper: float) -> float:
        rng_calls.append((lower, upper))
        return 0.5

    engine.executor._retry_random = draw

    async def initial_sleep(token: CancellationToken, seconds: float) -> None:
        nonlocal now
        token.checkpoint()
        if boundary in {"delay", "expired-delay", "changed-schedule"}:
            reached.set()
            await asyncio.Event().wait()
        now += seconds
        await asyncio.sleep(0)

    monkeypatch.setattr(CancellationToken, "sleep", initial_sleep)
    original_call = engine.executor._call

    async def stop_after_outcome(call: str, payload: Any, state: Any) -> Any:
        value = await original_call(call, payload, state)
        if boundary == "outcome":
            reached.set()
            await asyncio.Event().wait()
        return value

    engine.executor._call = stop_after_outcome
    handle = services.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    running = asyncio.create_task(engine.invoke(plan, handle, {"original": True}))
    original_schedule: tuple[str, str] | None = None
    try:
        await asyncio.wait_for(reached.wait(), timeout=10)
        rows = services.effect_journal.connection.execute(
            "SELECT i.identity, i.payload, e.payload FROM workflow_effect_invocations i "
            "JOIN workflow_effects e ON e.identity = i.identity WHERE i.invocation_id = ?",
            (handle.invocation_id,),
        ).fetchall()
        original_schedule = next(
            (row[0], row[2])
            for row in rows
            if json.loads(row[1])["operation"] == "policy:retry-schedule:v1"
        )
        assert rng_calls == [(0.25, 0.75)]
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(running, timeout=10)
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()
        services.close()

    now = 1070.0 if boundary in {"expired-delay", "outcome", "uncertain"} else 1005.0
    restarted = _services(tmp_path, backend, model)
    resumed = engine_type()
    await resumed.initialize(restarted)
    resumed.executor._absolute_clock = lambda: now

    def no_redraw(_lower: float, _upper: float) -> float:
        raise AssertionError("recorded jitter must not be sampled on recovery")

    resumed.executor._retry_random = no_redraw

    async def recovered_sleep(token: CancellationToken, seconds: float) -> None:
        nonlocal now
        token.checkpoint()
        sleeps.append(seconds)
        now += seconds
        await asyncio.sleep(0)

    monkeypatch.setattr(CancellationToken, "sleep", recovered_sleep)
    persisted = restarted.invocations.get(handle.invocation_id)
    assert persisted is not None and original_schedule is not None
    original_parent = persisted._persisted_payload
    try:
        if boundary == "changed-schedule":
            raw = json.loads(original_schedule[1])
            raw["output"]["due_at"] += 10
            restarted.effect_journal.connection.execute(
                "UPDATE workflow_effects SET payload = ? WHERE identity = ?",
                (json.dumps(raw), original_schedule[0]),
            )
            restarted.effect_journal.connection.commit()
            with pytest.raises(InvocationStateError):
                await resumed.resume(persisted, None, plan)
            saved = restarted.invocations.get(handle.invocation_id)
            assert saved is not None and saved._persisted_payload == original_parent
            assert model_calls == 1
            return
        result = await asyncio.wait_for(resumed.resume(persisted, None, plan), timeout=10)
        if boundary == "uncertain":
            assert result.status == "faulted" and result.error
            assert "unresolved execution intent" in result.error["message"]
            assert model_calls == 2
        else:
            assert result.status == "completed", result.error
            assert result.output == (
                {"fallback": True} if boundary == "expired-delay" else {"done": True}
            )
            assert model_calls == (1 if boundary == "expired-delay" else 2)
        if boundary == "delay":
            assert sleeps == [5.5]
        assert rng_calls == [(0.25, 0.75)]
        recorded = restarted.effect_journal.connection.execute(
            "SELECT payload FROM workflow_effects WHERE identity = ?", (original_schedule[0],)
        ).fetchone()
        assert recorded is not None and recorded[0] == original_schedule[1]
    finally:
        await resumed.shutdown()
        restarted.close()
