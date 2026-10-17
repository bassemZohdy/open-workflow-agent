"""Recorded clocks and random draws prove runtime retry and timeout semantics."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest
from engine_cases import engine_cases
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.errors import ModelError
from open_workflow_agent.lifecycle import CancellationToken
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow

pytestmark = pytest.mark.usefixtures("explicit_workflow_delivery")


def _services(tmp_path: Path, backend: str) -> RuntimeServices:
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    config = (
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}})
        if datasource
        else RuntimeConfig()
    )
    return RuntimeServices(
        config, model=FakeModel(), database_root=None if datasource else tmp_path
    )


def _workflow(retry: dict[str, Any]) -> dict[str, Any]:
    return {
        "document": {
            "dsl": "1.0.3",
            "namespace": "test",
            "name": "retry-timing",
            "version": "1.0.0",
        },
        "do": [
            {
                "attempt": {
                    "try": [{"action": {"call": "llm:1.0.0@default", "with": {"prompt": "value"}}}],
                    "catch": {
                        "as": "failure",
                        "retry": retry,
                        "do": [{"recover": {"set": {"code": "${ $failure.code }"}}}],
                    },
                }
            }
        ],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize(
    "case",
    [
        "constant",
        "linear",
        "exponential",
        "jitter-only",
        "unlimited-count",
        "duration",
        "zero-duration",
        "zero-attempt",
    ],
)
async def test_retry_clock_and_rng_determine_actual_attempts(
    tmp_path: Path, monkeypatch: Any, engine_name: str, engine_type: Any, case: str, backend: str
) -> None:
    services = _services(tmp_path, backend)
    now = 100.0
    sleeps: list[float] = []
    draws: list[tuple[float, float]] = []
    retry: dict[str, Any] = {"limit": {"attempt": {"count": 3}}}
    failures = 3
    expected_calls = 4
    expected_sleeps: list[float] = []
    expected_output: Any = {"success": True}
    if case in {"constant", "linear", "exponential"}:
        retry.update(
            {
                "delay": {"seconds": 2},
                "backoff": {case: {}},
                "jitter": {"from": {"milliseconds": 250}, "to": {"milliseconds": 750}},
            }
        )
        expected_sleeps = {
            "constant": [2.5, 2.5, 2.5],
            "linear": [2.5, 4.5, 6.5],
            "exponential": [2.5, 4.5, 8.5],
        }[case]
    elif case == "jitter-only":
        retry["jitter"] = {"from": {"milliseconds": 250}, "to": {"milliseconds": 750}}
        expected_sleeps = [0.5, 0.5, 0.5]
    elif case == "unlimited-count":
        retry = {}
        expected_sleeps = [0, 0, 0]
    elif case == "duration":
        retry = {"limit": {"duration": {"seconds": 3}}, "delay": {"seconds": 1}}
        failures = 20
        expected_calls = 2
        expected_sleeps = [1]
        expected_output = {"code": "model_error"}
    elif case == "zero-duration":
        retry = {"limit": {"duration": {"seconds": 0}}}
        expected_calls = 1
        expected_output = {"code": "model_error"}
    else:
        retry = {"limit": {"attempt": {"count": 1, "duration": {"milliseconds": 0}}}}
        expected_calls = 1
        expected_sleeps = [0]
        expected_output = {"code": "workflow_execution_error"}

    def response(_prompt: Any) -> Any:
        nonlocal failures, now
        if case == "duration":
            now += 1
        if failures:
            failures -= 1
            raise ModelError("controlled transient failure")
        return {"success": True}

    async def sleep(token: CancellationToken, seconds: float) -> None:
        nonlocal now
        token.checkpoint()
        sleeps.append(seconds)
        now += seconds
        await asyncio.sleep(0)
        token.checkpoint()

    def draw(lower: float, upper: float) -> float:
        draws.append((lower, upper))
        return (lower + upper) / 2

    monkeypatch.setattr(CancellationToken, "sleep", sleep)
    model = FakeModel(response)
    services.model = model
    plan = compile_workflow(_workflow(retry))
    engine = engine_type()
    await engine.initialize(services)
    engine.executor._policy_clock = lambda: now
    engine.executor._absolute_clock = lambda: now
    engine.executor._retry_random = draw
    handle = services.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    try:
        result = await asyncio.wait_for(engine.invoke(plan, handle, {"original": True}), timeout=15)
        assert result.status == "completed", result.error
        assert result.output == expected_output, {
            "output": result.output,
            "error": result.error,
            "model_calls": len(model.calls),
            "sleeps": sleeps,
            "draws": draws,
            "clock": now,
        }
        assert len(model.calls) == expected_calls
        assert sleeps == expected_sleeps
        assert draws == ([(0.25, 0.75)] * 3 if "jitter" in retry else [])
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("boundary", ["attempt", "task", "workflow"])
async def test_timeout_drains_an_uncertain_action_without_catch_retry(
    tmp_path: Path, engine_name: str, engine_type: Any, boundary: str, backend: str
) -> None:
    services = _services(tmp_path, backend)
    attempts = 0
    active = False
    released = False

    async def response(_prompt: Any) -> Any:
        nonlocal attempts, active, released
        attempts += 1
        if boundary == "attempt" and attempts == 1:
            raise ModelError("first attempt fails")
        active = True
        try:
            await asyncio.Event().wait()
        finally:
            active = False
            released = True

    retry: dict[str, Any] = {"limit": {"attempt": {"count": 3}}}
    if boundary == "attempt":
        retry["limit"]["attempt"]["duration"] = {"seconds": 10}
    workflow = _workflow(retry)
    if boundary == "task":
        workflow["do"][0]["attempt"]["timeout"] = {"after": {"seconds": 10}}
    elif boundary == "workflow":
        workflow["timeout"] = {"after": {"seconds": 10}}
    model = FakeModel(response)
    services.model = model
    plan = compile_workflow(workflow)
    engine = engine_type()
    await engine.initialize(services)
    handle = services.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    try:
        # The workflow keeps its 10-second deadline; this is only a generous
        # outer harness ceiling for busy Windows runners.
        result = await asyncio.wait_for(engine.invoke(plan, handle, {}), timeout=45)
        assert result.status == "faulted", result.output
        assert result.error and "unresolved execution intent" in result.error["message"]
        assert len(model.calls) == (2 if boundary == "attempt" else 1)
        assert released and not active
    finally:
        await engine.shutdown()
        services.close()
