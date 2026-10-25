"""Suspended fork branches retain timers without inventing completion or a winner."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from engine_cases import engine_cases
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.forks import ForkIdentity
from open_workflow_agent.workflow import compile_workflow
from test_durable_timers import _handle, _idle_wait, _services, _terminal, _use_clock, _workflow


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("compete", [False, True])
async def test_owned_workflow_end_after_sibling_wait_uses_existing_running_transition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: Any,
    backend: str,
    compete: bool,
) -> None:
    model = FakeModel()
    services = _services(tmp_path, backend, model)
    engine = engine_type()
    waiting = asyncio.Event()
    transitions: list[str] = []
    await engine.initialize(services)
    await engine._timer_dispatcher.stop()
    original_update = services.invocations.update
    original_task = engine.executor._run_task

    def update(handle: Any, **changes: Any) -> Any:
        result = original_update(handle, **changes)
        if "status" in changes:
            transitions.append(changes["status"])
        if changes.get("status") == "waiting":
            waiting.set()
        return result

    async def run_task(name: str, definition: Any, state: Any) -> Any:
        if name == "winner":
            await waiting.wait()
        return await original_task(name, definition, state)

    monkeypatch.setattr(services.invocations, "update", update)
    monkeypatch.setattr(engine.executor, "_run_task", run_task)
    plan = compile_workflow(
        _workflow(
            [
                {
                    "parallel": {
                        "fork": {
                            "compete": compete,
                            "branches": [
                                {"winner": {"set": {"ended": True}, "then": "end"}},
                                {"paused": {"wait": {"seconds": 300}}},
                            ],
                        }
                    }
                },
                {"never": {"call": "llm:1.0.0@default"}},
            ],
            "end-after-sibling-wait",
        )
    )
    handle = _handle(services, engine_name, plan)
    try:
        await services.recovery_definitions.prepare_profile(engine_name)
        result = await asyncio.wait_for(engine.invoke(plan, handle, {}), timeout=20)
        assert result.status == "completed" and result.output == {"ended": True}, result.error
        assert waiting.is_set() and "waiting" in transitions
        assert transitions[-1] == "completed"
        last_wait = len(transitions) - 1 - transitions[::-1].index("waiting")
        assert "running" in transitions[last_wait + 1 :]
        assert services.invocations.get(handle.invocation_id).status == "completed"
        again = await engine.resume(handle, {"ignored": True}, plan)
        assert again.status == "completed" and again.output == {"ended": True}
        assert model.calls == []
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("compete", [False, True])
async def test_all_waiting_fork_branches_release_execution_and_keep_original_due_times(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: Any,
    backend: str,
    compete: bool,
) -> None:
    now = 1000.0
    _use_clock(monkeypatch, lambda: now)
    model = FakeModel(lambda payload: payload)
    services = _services(tmp_path, backend, model)
    engine = engine_type()
    plan = compile_workflow(
        _workflow(
            [
                {
                    "parallel": {
                        "fork": {
                            "compete": compete,
                            "branches": [
                                {"first": {"wait": {"seconds": 60}}},
                                {"second": {"wait": {"seconds": 120}}},
                            ],
                        }
                    }
                },
                {
                    "finish": {
                        "call": "llm:1.0.0@default",
                        "with": {"prompt": "finish", "branches": "${ . }"},
                    }
                },
            ],
            "durable-fork-timers",
        )
    )
    original = {"original": True}
    try:
        await engine.initialize(services)
        dispatcher = engine._timer_dispatcher
        assert dispatcher is not None
        await dispatcher.stop()
        handle = _handle(services, engine_name, plan)
        waiting = await asyncio.wait_for(engine.invoke(plan, handle, original), timeout=10)
        assert waiting.status == "waiting", waiting.error
        await _idle_wait(engine, services, handle.invocation_id)
        timers = services.timers.pending_for_invocation(handle.invocation_id, plan.fingerprint)
        assert sorted(value.due_at for value in timers) == [1060, 1120]
        identity = ForkIdentity(
            handle.invocation_id,
            plan.fingerprint,
            "/do/0/parallel",
            (("/do/0/parallel", 1), ("/do/0/parallel/@attempt", 1)),
        )
        assert services.fork_decisions.get(identity) is None and model.calls == []
        now = 1060.0
        admitted = await dispatcher.tick(now=now, invocation_id=handle.invocation_id)
        assert admitted == 1, [(type(error).__name__, str(error)) for error in dispatcher._errors]
        if not compete:
            await _idle_wait(engine, services, handle.invocation_id)
            assert not dispatcher._errors, [
                (type(error).__name__, str(error)) for error in dispatcher._errors
            ]
            pending = services.timers.pending_for_invocation(handle.invocation_id, plan.fingerprint)
            assert len(pending) == 1 and pending[0].due_at == 1120
            assert model.calls == [] and services.fork_decisions.get(identity) is None
            now = 1120.0
            await dispatcher.tick(now=now, invocation_id=handle.invocation_id)
        result = await _terminal(services, handle.invocation_id)
        assert result.status == "completed", result.error
        assert result.output == {
            "prompt": "finish",
            "branches": original if compete else [original, original],
        }
        assert model.calls == [result.output]
        decision = services.fork_decisions.get(identity)
        if compete:
            assert decision is not None and decision.winner == 0 and decision.drained
            assert decision.cancellation_intents == (1,)
        else:
            assert decision is None
        assert services.invocations.read_input(handle) == original
        services.timers.verify_invocation(handle.invocation_id, plan.fingerprint)
    finally:
        await engine.shutdown()
        services.close()
