"""Portable durable timer decisions and adapter-owned nonresident recovery."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from engine_cases import engine_cases
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.errors import InvocationStateConflict, InvocationStateError
from open_workflow_agent.lifecycle import CancellationToken
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import WorkflowExecutor, compile_workflow


def _services(path: Path, backend: str, model: FakeModel) -> RuntimeServices:
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    config = (
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}})
        if datasource
        else RuntimeConfig()
    )
    return RuntimeServices(config, database_root=None if datasource else path, model=model)


def _workflow(tasks: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return {
        "document": {"dsl": "1.0.3", "namespace": "timers", "name": name, "version": "1.0.0"},
        "do": tasks,
    }


def _handle(services: RuntimeServices, engine_name: str, plan: Any) -> Any:
    return services.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
        owner_principal="timer-contract-owner",
    )


def _use_clock(monkeypatch: pytest.MonkeyPatch, clock: Callable[[], float]) -> None:
    """Bind the existing executor clock before an initializer starts polling."""
    original_initialize = WorkflowExecutor.__init__

    def initialize(executor: WorkflowExecutor, *args: Any, **kwargs: Any) -> None:
        original_initialize(executor, *args, **kwargs)
        executor._absolute_clock = clock

    monkeypatch.setattr(WorkflowExecutor, "__init__", initialize)


async def _terminal(services: RuntimeServices, invocation_id: str) -> Any:
    async with asyncio.timeout(30):
        while True:
            saved = services.invocations.get(invocation_id)
            assert saved is not None
            if saved.status in {"completed", "faulted", "cancelled"}:
                return saved
            await asyncio.sleep(0.01)


async def _idle_wait(engine: Any, services: RuntimeServices, invocation_id: str) -> None:
    """A saved arm can precede native acknowledgement and owner release."""
    async with asyncio.timeout(30):
        while True:
            saved = services.invocations.get(invocation_id)
            assert saved is not None
            assert saved.status not in {"faulted", "completed", "cancelled"}, saved.error
            if (
                saved.status == "waiting"
                and invocation_id not in engine._timer_dispatcher._workers
                and invocation_id not in engine._active
                and invocation_id not in engine._ownership_tasks
            ):
                return
            await asyncio.sleep(0.01)


def _retry_policy_records(services: RuntimeServices, invocation_id: str) -> list[tuple[str, str]]:
    rows = services.effect_journal.connection.execute(
        "SELECT i.identity,i.payload,e.payload FROM workflow_effect_invocations i "
        "JOIN workflow_effects e ON e.identity=i.identity WHERE i.invocation_id=? "
        "ORDER BY i.identity",
        (invocation_id,),
    ).fetchall()
    return [
        (row[0], row[2])
        for row in rows
        if json.loads(row[1])["operation"] in {"policy:retry-clock:v1", "policy:retry-schedule:v1"}
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("duration", [60, 601])
async def test_wait_commits_original_due_and_releases_resident_execution(
    tmp_path: Path,
    engine_name: str,
    engine_type: Any,
    backend: str,
    duration: int,
) -> None:
    model = FakeModel({"phase": "before"})
    services = _services(tmp_path, backend, model)
    engine = engine_type()
    running: asyncio.Task[Any] | None = None
    original_input = {"original": "retained", "duration": duration}
    plan = compile_workflow(
        _workflow(
            [
                {"before": {"call": "llm:1.0.0@default", "with": {"prompt": "before"}}},
                {"pause": {"wait": {"seconds": duration}}},
                {"after": {"call": "llm:1.0.0@default", "with": {"prompt": "after"}}},
            ],
            "nonresident-wait",
        )
    )
    try:
        await engine.initialize(services)
        dispatcher = getattr(engine, "_timer_dispatcher", None)
        if dispatcher is not None:
            await dispatcher.stop()
        engine.executor._absolute_clock = lambda: 1000.0
        handle = _handle(services, engine_name, plan)
        running = asyncio.create_task(engine.invoke(plan, handle, original_input))
        # This verifies release; a resident 60/601-second sleeper cannot satisfy it.
        result = await asyncio.wait_for(asyncio.shield(running), timeout=10)
        assert result.status == "waiting", result.error
        assert handle.status == "waiting"
        assert model.calls == [{"prompt": "before"}]
        assert handle.invocation_id not in engine._active
        assert handle.invocation_id not in engine._ownership_tasks
        assert handle._execution_lease is None
        assert services.invocations.read_input(handle) == original_input
        timers = getattr(services, "timers", None)
        assert timers is not None, "waiting must have committed common timer evidence"
        pending = timers.pending_for_invocation(handle.invocation_id, plan.fingerprint)
        assert len(pending) == 1
        registration = pending[0]
        assert registration.identity.operation == "wait:arm:v1"
        assert registration.identity.invocation_id == handle.invocation_id
        assert registration.identity.task_reference == "/do/1/pause"
        assert registration.armed_at == 1000.0
        assert registration.duration == duration
        assert registration.due_at == 1000.0 + duration
        assert registration.receipt.status == "completed"
        timers.verify_invocation(handle.invocation_id, plan.fingerprint)
    finally:
        if running is not None:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_cancelled_timer_cannot_dispatch_a_late_continuation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: Any,
    backend: str,
) -> None:
    now = 1000.0
    _use_clock(monkeypatch, lambda: now)
    model = FakeModel({"must": "not-dispatch"})
    services = _services(tmp_path, backend, model)
    engine = engine_type()
    plan = compile_workflow(
        _workflow(
            [
                {"pause": {"wait": {"seconds": 60}}},
                {"after": {"call": "llm:1.0.0@default", "with": {"prompt": "after"}}},
            ],
            "cancelled-wait",
        )
    )
    try:
        await engine.initialize(services)
        dispatcher = getattr(engine, "_timer_dispatcher", None)
        assert dispatcher is not None
        await dispatcher.stop()
        handle = _handle(services, engine_name, plan)
        waiting = await asyncio.wait_for(
            engine.invoke(plan, handle, {"original": True}), timeout=10
        )
        assert waiting.status == "waiting", waiting.error
        cancelled = await engine.cancel(handle, operation_id="timer-contract-cancel")
        assert cancelled.status == "cancelled"
        saved = services.invocations.get(handle.invocation_id)
        assert saved is not None
        before = saved._persisted_payload
        now = 1060.0
        await dispatcher.tick(now=now, invocation_id=handle.invocation_id)
        late = services.invocations.get(handle.invocation_id)
        assert late is not None and late.status == "cancelled"
        assert late._persisted_payload == before
        assert not model.calls
        timers = getattr(services, "timers", None)
        assert timers is not None
        assert not timers.pending_for_invocation(handle.invocation_id, plan.fingerprint)
        duplicate = await engine.resume(late, {"late": True}, plan)
        assert duplicate.status == "cancelled"
        assert not model.calls
        timers.verify_invocation(handle.invocation_id, plan.fingerprint)
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("scope", ["task", "workflow"])
async def test_original_deadline_wakes_a_suspended_timer_before_its_later_due(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: Any,
    backend: str,
    scope: str,
) -> None:
    now = 1000.0
    _use_clock(monkeypatch, lambda: now)
    model = FakeModel({"must": "not-dispatch"})
    services = _services(tmp_path, backend, model)
    engine = engine_type()
    pause: dict[str, Any] = {"wait": {"seconds": 60}}
    source = _workflow(
        [
            {"pause": pause},
            {"after": {"call": "llm:1.0.0@default", "with": {"prompt": "after"}}},
        ],
        "deadline-wait",
    )
    (pause if scope == "task" else source)["timeout"] = {"after": {"seconds": 30}}
    plan = compile_workflow(source)
    try:
        await engine.initialize(services)
        dispatcher = getattr(engine, "_timer_dispatcher", None)
        assert dispatcher is not None
        await dispatcher.stop()
        handle = _handle(services, engine_name, plan)
        waiting = await asyncio.wait_for(
            engine.invoke(plan, handle, {"original": True}), timeout=10
        )
        assert waiting.status == "waiting", waiting.error
        timers = getattr(services, "timers", None)
        assert timers is not None
        pending = timers.pending_for_invocation(handle.invocation_id, plan.fingerprint)
        assert len(pending) == 1
        assert pending[0].armed_at == 1000.0 and pending[0].due_at == 1060.0
        now = 1031.0
        await dispatcher.tick(now=now, invocation_id=handle.invocation_id)
        terminal = await _terminal(services, handle.invocation_id)
        assert terminal.status == "faulted", terminal.error
        assert terminal.error is not None
        assert terminal.error["code"] == "workflow_execution_error"
        assert terminal.error["details"]["type"] == (
            "https://serverlessworkflow.io/spec/1.0.0/errors/timeout"
        )
        assert terminal.error["details"]["status"] == 408
        assert not model.calls
        assert services.invocations.read_input(terminal) == {"original": True}
        now = 1060.0
        await dispatcher.tick(now=now, invocation_id=handle.invocation_id)
        assert not model.calls
        assert not timers.pending_for_invocation(handle.invocation_id, plan.fingerprint)
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("corruption", ["missing-arm", "changed-arm-digest"])
async def test_corrupt_timer_receipt_rejects_resume_before_parent_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: Any,
    backend: str,
    corruption: str,
) -> None:
    now = 1000.0
    _use_clock(monkeypatch, lambda: now)
    model = FakeModel({"must": "not-dispatch"})
    services = _services(tmp_path, backend, model)
    engine = engine_type()
    plan = compile_workflow(
        _workflow(
            [
                {"pause": {"wait": {"seconds": 60}}},
                {"after": {"call": "llm:1.0.0@default", "with": {"prompt": "after"}}},
            ],
            "corrupt-wait",
        )
    )
    try:
        await engine.initialize(services)
        dispatcher = getattr(engine, "_timer_dispatcher", None)
        assert dispatcher is not None
        await dispatcher.stop()
        handle = _handle(services, engine_name, plan)
        waiting = await asyncio.wait_for(
            engine.invoke(plan, handle, {"original": True}), timeout=10
        )
        assert waiting.status == "waiting", waiting.error
        timers = getattr(services, "timers", None)
        assert timers is not None
        registration = timers.pending_for_invocation(handle.invocation_id, plan.fingerprint)[0]
        connection = services.effect_journal.connection
        if corruption == "missing-arm":
            connection.execute(
                "DELETE FROM workflow_effects WHERE identity = ?", (registration.identity.key,)
            )
        else:
            row = connection.execute(
                "SELECT payload FROM workflow_effects WHERE identity = ?",
                (registration.identity.key,),
            ).fetchone()
            assert row is not None
            value = json.loads(row[0])
            value["request_digest"] = "0" * 64
            connection.execute(
                "UPDATE workflow_effects SET payload = ? WHERE identity = ?",
                (json.dumps(value), registration.identity.key),
            )
        connection.commit()
        saved = services.invocations.get(handle.invocation_id)
        assert saved is not None
        before = saved._persisted_payload
        now = 1020.0
        with pytest.raises(InvocationStateError):
            await engine.resume(saved, {"replacement": "must-not-bind"}, plan)
        unchanged = services.invocations.get(handle.invocation_id)
        assert unchanged is not None and unchanged._persisted_payload == before
        assert services.invocations.read_input(unchanged) == {"original": True}
        assert not model.calls
        wake_identity = replace(registration.identity, operation="wait:wake:v1")
        assert services.effect_journal.get(wake_identity) is None
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_reopened_timer_uses_remaining_delay_and_continues_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: Any,
    backend: str,
) -> None:
    now = 1000.0
    _use_clock(monkeypatch, lambda: now)
    before_model = FakeModel({"phase": "before"})
    services = _services(tmp_path, backend, before_model)
    engine = engine_type()
    plan = compile_workflow(
        _workflow(
            [
                {"before": {"call": "llm:1.0.0@default", "with": {"prompt": "before"}}},
                {
                    "pause": {
                        "wait": {"seconds": 60},
                        "output": {
                            "as": "${ {phase: .phase, original: $workflow.input.original} }"
                        },
                    }
                },
                {
                    "after": {
                        "call": "llm:1.0.0@default",
                        "with": {"prompt": "after", "data": "${ . }"},
                    }
                },
            ],
            "remaining-wait",
        )
    )
    original_input = {"original": "must-survive"}
    try:
        await engine.initialize(services)
        dispatcher = getattr(engine, "_timer_dispatcher", None)
        assert dispatcher is not None
        await dispatcher.stop()
        handle = _handle(services, engine_name, plan)
        result = await asyncio.wait_for(engine.invoke(plan, handle, original_input), timeout=10)
        assert result.status == "waiting", result.error
        timers = getattr(services, "timers", None)
        assert timers is not None
        first = timers.pending_for_invocation(handle.invocation_id, plan.fingerprint)
        assert len(first) == 1 and first[0].due_at == 1060.0
        original_identity = first[0].identity
        invocation_id = handle.invocation_id
        assert before_model.calls == [{"prompt": "before"}]
        await engine.shutdown()
        services.close()

        now = 1020.0
        after_model = FakeModel({"phase": "after"})
        services = _services(tmp_path, backend, after_model)
        engine = engine_type()
        await engine.initialize(services)
        dispatcher = engine._timer_dispatcher
        await dispatcher.stop()
        timers = getattr(services, "timers", None)
        assert timers is not None
        recovered = services.invocations.get(invocation_id)
        assert recovered is not None and recovered.status == "waiting"
        pending = timers.pending_for_invocation(invocation_id, plan.fingerprint)
        assert len(pending) == 1
        assert pending[0].identity == original_identity
        assert pending[0].armed_at == 1000.0
        assert pending[0].due_at == 1060.0
        assert pending[0].due_at - now == 40.0
        before = recovered._persisted_payload
        await dispatcher.tick(now=now, invocation_id=handle.invocation_id)
        unchanged = services.invocations.get(invocation_id)
        assert unchanged is not None and unchanged._persisted_payload == before
        assert not after_model.calls
        now = 1060.0
        await dispatcher.tick(now=now, invocation_id=handle.invocation_id)
        completed = await _terminal(services, invocation_id)
        assert completed.status == "completed", completed.error
        assert completed.output == {"phase": "after"}
        assert after_model.calls == [
            {"prompt": "after", "data": {"phase": "before", "original": "must-survive"}}
        ]
        assert services.invocations.read_input(completed) == original_input
        assert not timers.pending_for_invocation(invocation_id, plan.fingerprint)
        await dispatcher.tick(now=now + 100, invocation_id=handle.invocation_id)
        assert len(after_model.calls) == 1
        timers.verify_invocation(invocation_id, plan.fingerprint)
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_early_manual_resume_records_one_wake_and_blocks_late_due_delivery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: Any,
    backend: str,
) -> None:
    now = 1000.0
    _use_clock(monkeypatch, lambda: now)
    model = FakeModel(lambda payload: payload)
    services = _services(tmp_path, backend, model)
    engine = engine_type()
    original_input = {"source": "original"}
    plan = compile_workflow(
        _workflow(
            [
                {"pause": {"wait": {"seconds": 60}}},
                {
                    "after": {
                        "call": "llm:1.0.0@default",
                        "with": {
                            "prompt": "after",
                            "original": "${ $workflow.input }",
                            "resumed": "${ $resume_input }",
                        },
                    }
                },
            ],
            "early-manual-wake",
        )
    )
    try:
        await engine.initialize(services)
        dispatcher = getattr(engine, "_timer_dispatcher", None)
        assert dispatcher is not None
        await dispatcher.stop()
        handle = _handle(services, engine_name, plan)
        waiting = await asyncio.wait_for(engine.invoke(plan, handle, original_input), timeout=10)
        assert waiting.status == "waiting", waiting.error
        now = 1020.0
        resumed = await asyncio.wait_for(engine.resume(handle, {"manual": True}, plan), timeout=30)
        assert resumed.status == "completed", resumed.error
        expected = {"prompt": "after", "original": original_input, "resumed": {"manual": True}}
        assert resumed.output == expected
        assert model.calls == [expected]
        assert services.invocations.read_input(handle) == original_input
        now = 1060.0
        await dispatcher.tick(now=now, invocation_id=handle.invocation_id)
        duplicate = await engine.resume(handle, {"replacement": "must-not-replace"}, plan)
        assert duplicate.status == "completed" and duplicate.output == expected
        assert model.calls == [expected]
        timers = getattr(services, "timers", None)
        assert timers is not None
        assert not timers.pending_for_invocation(handle.invocation_id, plan.fingerprint)
        timers.verify_invocation(handle.invocation_id, plan.fingerprint)
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_manual_resume_racing_due_delivery_commits_one_immutable_wake(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: Any,
    backend: str,
) -> None:
    now = 1000.0
    _use_clock(monkeypatch, lambda: now)
    model = FakeModel(lambda payload: payload)
    services = _services(tmp_path, backend, model)
    engine = engine_type()
    plan = compile_workflow(
        _workflow(
            [
                {"pause": {"wait": {"seconds": 60}}},
                {
                    "after": {
                        "call": "llm:1.0.0@default",
                        "with": {"prompt": "after", "resumed": "${ $resume_input }"},
                    }
                },
            ],
            "racing-timer-wake",
        )
    )
    try:
        await engine.initialize(services)
        dispatcher = getattr(engine, "_timer_dispatcher", None)
        assert dispatcher is not None
        await dispatcher.stop()
        handle = _handle(services, engine_name, plan)
        waiting = await asyncio.wait_for(
            engine.invoke(plan, handle, {"original": True}), timeout=10
        )
        assert waiting.status == "waiting", waiting.error
        registration = services.timers.pending_for_invocation(
            handle.invocation_id, plan.fingerprint
        )[0]
        now = 1060.0
        outcomes = await asyncio.gather(
            engine.resume(handle, {"manual": True}, plan),
            dispatcher.tick(now=now, invocation_id=handle.invocation_id),
            return_exceptions=True,
        )
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                assert isinstance(outcome, InvocationStateConflict), outcome
        terminal = await _terminal(services, handle.invocation_id)
        assert terminal.status == "completed", terminal.error
        wake = services.timers.wake_for(registration)
        assert wake is not None and wake.reason in {"timer_due", "resume_requested"}
        assert wake.resume_input == (
            {"manual": True} if wake.reason == "resume_requested" else None
        )
        assert model.calls == [{"prompt": "after", "resumed": wake.resume_input}]
        assert services.invocations.read_input(terminal) == {"original": True}
        immutable = wake.receipt
        now = 1160.0
        await dispatcher.tick(now=now, invocation_id=handle.invocation_id)
        duplicate = await engine.resume(terminal, {"different": True}, plan)
        assert duplicate.status == "completed" and duplicate.output == terminal.output
        assert len(model.calls) == 1
        duplicate_wake = services.timers.wake_for(registration)
        assert duplicate_wake is not None and duplicate_wake.receipt == immutable
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_repeated_wait_task_arms_distinct_occurrences_and_resumes_fifo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: Any,
    backend: str,
) -> None:
    now = 1000.0
    _use_clock(monkeypatch, lambda: now)
    model = FakeModel(lambda payload: payload)
    services = _services(tmp_path, backend, model)
    engine = engine_type()
    source = _workflow(
        [
            {
                "loop": {
                    "for": {"each": "turn", "in": "${ [0,1] }"},
                    "do": [
                        {"pause": {"wait": {"seconds": 60}}},
                        {"action": {"call": "llm:1.0.0@default", "with": {"prompt": "${ $turn }"}}},
                    ],
                }
            }
        ],
        "repeated-wait",
    )
    plan = compile_workflow(source)
    try:
        await engine.initialize(services)
        dispatcher = getattr(engine, "_timer_dispatcher", None)
        assert dispatcher is not None
        await dispatcher.stop()
        handle = _handle(services, engine_name, plan)
        waiting = await asyncio.wait_for(
            engine.invoke(plan, handle, {"original": True}), timeout=10
        )
        assert waiting.status == "waiting", waiting.error
        first = services.timers.pending_for_invocation(handle.invocation_id, plan.fingerprint)[0]
        assert first.due_at == 1060
        now = 1060.0
        await dispatcher.tick(now=now, invocation_id=handle.invocation_id)
        async with asyncio.timeout(30):
            while len(model.calls) < 1:
                await asyncio.sleep(0.01)
        async with asyncio.timeout(30):
            while True:
                pending = services.timers.pending_for_invocation(
                    handle.invocation_id, plan.fingerprint
                )
                if pending and pending[0].identity.key != first.identity.key:
                    break
                await asyncio.sleep(0.01)
        assert len(pending) == 1
        second = pending[0]
        assert second.identity.task_reference == first.identity.task_reference
        assert second.identity.key != first.identity.key
        assert second.identity.occurrence != first.identity.occurrence
        assert second.armed_at == 1060 and second.due_at == 1120
        assert model.calls == [{"prompt": 0}]
        await _idle_wait(engine, services, handle.invocation_id)
        now = 1120.0
        await dispatcher.tick(now=now, invocation_id=handle.invocation_id)
        terminal = await _terminal(services, handle.invocation_id)
        assert terminal.status == "completed", terminal.error
        assert model.calls == [{"prompt": 0}, {"prompt": 1}]
        assert services.timers.wake_for(first) is not None
        assert services.timers.wake_for(second) is not None
        assert not services.timers.pending_for_invocation(handle.invocation_id, plan.fingerprint)
        services.timers.verify_invocation(handle.invocation_id, plan.fingerprint)
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_wait_inside_retry_preserves_attempt_clock_schedule_and_single_jitter_draw(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: Any,
    backend: str,
) -> None:
    now = 1000.0
    _use_clock(monkeypatch, lambda: now)
    model = FakeModel({"done": True}, failures=1)
    services = _services(tmp_path, backend, model)
    engine = engine_type()
    draws: list[tuple[float, float]] = []
    sleeps: list[float] = []

    def draw(lower: float, upper: float) -> float:
        draws.append((lower, upper))
        return 0.5

    async def sleep(token: CancellationToken, seconds: float) -> None:
        nonlocal now
        token.checkpoint()
        sleeps.append(seconds)
        now += seconds
        await asyncio.sleep(0)

    monkeypatch.setattr(CancellationToken, "sleep", sleep)
    plan = compile_workflow(
        _workflow(
            [
                {
                    "attempt": {
                        "try": [
                            {"pause": {"wait": {"seconds": 60}}},
                            {"action": {"call": "llm:1.0.0@default", "with": {"prompt": "finish"}}},
                        ],
                        "catch": {
                            "retry": {
                                "delay": {"seconds": 10},
                                "jitter": {
                                    "from": {"milliseconds": 250},
                                    "to": {"milliseconds": 750},
                                },
                                "limit": {"attempt": {"count": 1}, "duration": {"seconds": 180}},
                            }
                        },
                    }
                }
            ],
            "wait-with-retry",
        )
    )
    try:
        await engine.initialize(services)
        dispatcher = getattr(engine, "_timer_dispatcher", None)
        assert dispatcher is not None
        await dispatcher.stop()
        engine.executor._retry_random = draw
        handle = _handle(services, engine_name, plan)
        waiting = await asyncio.wait_for(
            engine.invoke(plan, handle, {"original": True}), timeout=10
        )
        assert waiting.status == "waiting", waiting.error
        first = services.timers.pending_for_invocation(handle.invocation_id, plan.fingerprint)[0]
        assert first.due_at == 1060
        now = 1060.0
        await dispatcher.tick(now=now, invocation_id=handle.invocation_id)
        async with asyncio.timeout(30):
            while True:
                pending = services.timers.pending_for_invocation(
                    handle.invocation_id, plan.fingerprint
                )
                if pending and pending[0].identity.key != first.identity.key:
                    break
                current = services.invocations.get(handle.invocation_id)
                assert current is not None
                assert current.status not in {"faulted", "completed"}, current.error
                await asyncio.sleep(0.01)
        second = pending[0]
        assert second.identity.key != first.identity.key
        assert second.armed_at == 1070.5 and second.due_at == 1130.5
        assert draws == [(0.25, 0.75)]
        assert [delay for delay in sleeps if delay > 0] == [10.5]
        assert model.calls == [{"prompt": "finish"}]
        await _idle_wait(engine, services, handle.invocation_id)
        policy = _retry_policy_records(services, handle.invocation_id)
        assert len(policy) == 2
        now = 1100.0
        await dispatcher.tick(now=now, invocation_id=handle.invocation_id)
        assert model.calls == [{"prompt": "finish"}]
        assert (
            services.timers.pending_for_invocation(handle.invocation_id, plan.fingerprint)[0].due_at
            == 1130.5
        )
        now = 1130.5
        await dispatcher.tick(now=now, invocation_id=handle.invocation_id)
        terminal = await _terminal(services, handle.invocation_id)
        assert terminal.status == "completed" and terminal.output == {"done": True}, terminal.error
        assert model.calls == [{"prompt": "finish"}, {"prompt": "finish"}]
        assert draws == [(0.25, 0.75)]
        assert [delay for delay in sleeps if delay > 0] == [10.5]
        after = _retry_policy_records(services, handle.invocation_id)
        assert after == policy
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_retry_attempt_original_deadline_wakes_wait_without_renewing_first_attempt_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: Any,
    backend: str,
) -> None:
    now = 1000.0
    _use_clock(monkeypatch, lambda: now)
    draws: list[tuple[float, float]] = []
    model = FakeModel({"must": "not-dispatch"}, failures=1)
    services = _services(tmp_path, backend, model)
    engine = engine_type()

    def draw(lower: float, upper: float) -> float:
        draws.append((lower, upper))
        return 0.5

    async def sleep(token: CancellationToken, seconds: float) -> None:
        nonlocal now
        token.checkpoint()
        now += seconds
        await asyncio.sleep(0)

    monkeypatch.setattr(CancellationToken, "sleep", sleep)
    plan = compile_workflow(
        _workflow(
            [
                {
                    "attempt": {
                        "try": [
                            {"pause": {"wait": {"seconds": 60}}},
                            {"action": {"call": "llm:1.0.0@default", "with": {"prompt": "finish"}}},
                        ],
                        "catch": {
                            "as": "failure",
                            "retry": {
                                "delay": {"seconds": 10},
                                "jitter": {
                                    "from": {"milliseconds": 250},
                                    "to": {"milliseconds": 750},
                                },
                                "limit": {
                                    "attempt": {"count": 1, "duration": {"seconds": 20}},
                                    "duration": {"seconds": 180},
                                },
                            },
                            "do": [
                                {
                                    "recover": {
                                        "set": {
                                            "type": "${ $failure.type }",
                                            "status": "${ $failure.status }",
                                        }
                                    }
                                }
                            ],
                        },
                    }
                }
            ],
            "suspended-retry-attempt-deadline",
        )
    )
    try:
        await engine.initialize(services)
        dispatcher = getattr(engine, "_timer_dispatcher", None)
        assert dispatcher is not None
        await dispatcher.stop()
        engine.executor._retry_random = draw
        handle = _handle(services, engine_name, plan)
        waiting = await asyncio.wait_for(
            engine.invoke(plan, handle, {"original": True}), timeout=10
        )
        assert waiting.status == "waiting", waiting.error
        first = services.timers.pending_for_invocation(handle.invocation_id, plan.fingerprint)[0]
        # The established retry-attempt budget applies to admitted retries.
        assert first.due_at == first.wake_at == 1060
        now = 1060.0
        await dispatcher.tick(now=now, invocation_id=handle.invocation_id)
        async with asyncio.timeout(30):
            while True:
                pending = services.timers.pending_for_invocation(
                    handle.invocation_id, plan.fingerprint
                )
                if pending and pending[0].timer_key != first.timer_key:
                    break
                await asyncio.sleep(0.01)
        second = pending[0]
        await _idle_wait(engine, services, handle.invocation_id)
        assert second.armed_at == 1070.5 and second.due_at == 1130.5
        assert second.wake_at == 1090.5
        assert model.calls == [{"prompt": "finish"}] and draws == [(0.25, 0.75)]
        policy = _retry_policy_records(services, handle.invocation_id)
        now = 1090.0
        await dispatcher.tick(now=now, invocation_id=handle.invocation_id)
        assert model.calls == [{"prompt": "finish"}]
        now = 1091.0
        await dispatcher.tick(now=now, invocation_id=handle.invocation_id)
        completed = await _terminal(services, handle.invocation_id)
        assert completed.status == "completed", completed.error
        assert completed.output == {
            "type": "https://serverlessworkflow.io/spec/1.0.0/errors/timeout",
            "status": 408,
        }
        assert model.calls == [{"prompt": "finish"}] and draws == [(0.25, 0.75)]
        assert _retry_policy_records(services, handle.invocation_id) == policy
        now = 1130.5
        await dispatcher.tick(now=now, invocation_id=handle.invocation_id)
        assert model.calls == [{"prompt": "finish"}]
    finally:
        await engine.shutdown()
        services.close()
