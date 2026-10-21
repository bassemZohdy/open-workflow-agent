"""Identical competitive fork decisions, cancellation and replay across engines."""

from __future__ import annotations

import asyncio
import json
import os

import pytest
from engine_cases import engine_cases
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.errors import ExpressionError, WorkflowExecutionError
from open_workflow_agent.recovery_definitions import admission_identity
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow


def _plan(tasks, functions=None):
    return compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "test",
                "name": "competitive-forks",
                "version": "1.0.0",
            },
            "do": tasks,
            **({"use": {"functions": functions}} if functions else {}),
        }
    )


def _call(branch):
    return {
        "call": "http",
        "with": {
            "method": "POST",
            "endpoint": "https://unused.test/action",
            "body": {"branch": branch},
        },
    }


def _race():
    return {
        "race": {
            "fork": {
                "compete": True,
                "branches": [{"slow": _call("slow")}, {"fast": _call("fast")}],
            }
        }
    }


def _configuration(tmp_path, backend):
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    return (
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}})
        if datasource
        else RuntimeConfig(),
        None if datasource else tmp_path,
    )


def _existing_keys(services):
    return {
        row[0]
        for row in services.fork_decisions.connection.execute(
            "SELECT identity FROM competitive_fork_decisions"
        ).fetchall()
    }


async def _engine(services, name, engine_type, plan):
    engine = engine_type()
    await engine.initialize(services)
    handle = services.invocations.create(
        engine=name,
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    return engine, handle


def _decisions(services, ignored_keys=()):
    return [
        (json.loads(row[1]), row[2])
        for row in services.fork_decisions.connection.execute(
            "SELECT identity, payload, drained FROM competitive_fork_decisions ORDER BY identity"
        ).fetchall()
        if row[0] not in ignored_keys
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("winner_outcome", ["value", "null", "fault"])
async def test_later_declared_first_completion_is_recorded_and_loser_cleanup_is_drained(
    services, engine_name, engine_type, winner_outcome
):
    slow_started = asyncio.Event()
    slow_drained = asyncio.Event()
    loser_effects = []

    async def call(protocol, payload):
        assert protocol == "http"
        if payload["body"]["branch"] == "slow":
            slow_started.set()
            try:
                await asyncio.Event().wait()
                loser_effects.append("committed")
            finally:
                # Cleanup may itself yield. A second cancellation must not
                # interrupt it after the winner has already cancelled losers.
                await asyncio.sleep(0)
                slow_drained.set()
        await slow_started.wait()
        if winner_outcome == "fault":
            raise WorkflowExecutionError("winning branch failed", details={"type": "urn:race"})
        return None if winner_outcome == "null" else {"winner": "fast"}

    services.call_protocol = call
    plan = _plan([_race()])
    engine, handle = await _engine(services, engine_name, engine_type, plan)
    try:
        result = await asyncio.wait_for(engine.invoke(plan, handle, {}), timeout=20)
        assert slow_drained.is_set()
        assert loser_effects == []
        decisions = _decisions(services)
        assert len(decisions) == 1
        decision, drained = decisions[0]
        assert decision["winner"] == 1
        assert decision["cancellation_intents"] == [0] and drained == 1
        if winner_outcome == "fault":
            assert result.status == "faulted"
            assert decision["status"] == "faulted" and decision["error"] == result.error
            assert result.error["details"]["instance"] == "/do/0/race/fork/branches/1/fast"
        else:
            expected = None if winner_outcome == "null" else {"winner": "fast"}
            assert result.status == "completed", result.error
            assert result.output == expected == decision["output"]
            assert decision["status"] == "completed" and decision["error"] is None
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("call_style", ["inline", "reusable", "nested"])
async def test_repeated_loop_races_have_distinct_decisions(
    services, engine_name, engine_type, call_style
):
    slow_started = [asyncio.Event(), asyncio.Event()]
    slow_drained = []
    fast_calls = []

    async def call(protocol, payload):
        index = payload["body"]["iteration"]
        if payload["body"]["branch"] == "slow":
            slow_started[index].set()
            try:
                await asyncio.Event().wait()
            finally:
                slow_drained.append(index)
        await slow_started[index].wait()
        fast_calls.append(index)
        return {"winner": index}

    services.call_protocol = call
    race = _race()
    for branch in race["race"]["fork"]["branches"]:
        next(iter(branch.values()))["with"]["body"]["iteration"] = (
            "${ .iteration }" if call_style == "reusable" else "${ $item }"
        )
    functions = None
    body = [race]
    if call_style == "reusable":
        functions = {"contest": race["race"]}
        body = [{"invoke": {"call": "contest", "with": {"iteration": "${ $item }"}}}]
    elif call_style == "nested":
        body = [{"outer": {"fork": {"compete": True, "branches": [{"inner": {"do": body}}]}}}]
    plan = _plan([{"loop": {"for": {"in": "${ [0, 1] }"}, "do": body}}], functions)
    engine, handle = await _engine(services, engine_name, engine_type, plan)
    try:
        result = await asyncio.wait_for(engine.invoke(plan, handle, {}), timeout=20)
        assert result.status == "completed", result.error
        assert result.output == {"winner": 1}
        assert fast_calls == slow_drained == [0, 1]
        decisions = _decisions(services)
        copies = 2 if call_style == "nested" else 1
        assert len(decisions) == 2 * copies and all(drained == 1 for _, drained in decisions)
        assert sorted(d["output"]["winner"] for d, _ in decisions) == [0] * copies + [1] * copies
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
async def test_repeated_transition_runs_a_new_race(services, engine_name, engine_type):
    started = [asyncio.Event(), asyncio.Event()]
    completed = []

    async def call(protocol, payload):
        step = payload["body"]["step"]
        if payload["body"]["branch"] == "slow":
            started[step].set()
            await asyncio.Event().wait()
        await started[step].wait()
        completed.append(step)
        return {"step": step + 1}

    services.call_protocol = call
    race = _race()
    for branch in race["race"]["fork"]["branches"]:
        next(iter(branch.values()))["with"]["body"]["step"] = "${ .step }"
    plan = _plan(
        [
            race,
            {
                "repeat": {
                    "switch": [
                        {"again": {"when": "${ .step < 2 }", "then": "race"}},
                        {"done": {"then": "end"}},
                    ]
                }
            },
        ]
    )
    engine, handle = await _engine(services, engine_name, engine_type, plan)
    try:
        result = await asyncio.wait_for(engine.invoke(plan, handle, {"step": 0}), timeout=20)
        assert result.status == "completed", result.error
        assert result.output == {"step": 2} and completed == [0, 1]
        decisions = _decisions(services)
        assert len(decisions) == 2 and all(receipt == 1 for _, receipt in decisions)
        assert sorted(d["output"]["step"] for d, _ in decisions) == [1, 2]
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
async def test_restart_without_a_decision_can_choose_a_new_winner(
    tmp_path, engine_name, engine_type
):
    services = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
    started = set()
    ready = asyncio.Event()

    async def interrupted_call(protocol, payload, state):
        started.add(payload["body"]["branch"])
        if len(started) == 2:
            ready.set()
        await asyncio.Event().wait()

    plan = _plan([_race()])
    engine, handle = await _engine(services, engine_name, engine_type, plan)
    # Stop before either action has a dispatch intent. Once an external action
    # begins, absence of a fork decision cannot justify dispatching it again.
    engine.executor._call = interrupted_call
    running = asyncio.create_task(engine.invoke(plan, handle, {}))
    try:
        await asyncio.wait_for(ready.wait(), timeout=20)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(running, timeout=20)
        assert _decisions(services) == []
        # Definition admission precedes execution. This boundary requires no
        # branch business intent, while retaining that independent admission.
        indexed = services.effect_journal.connection.execute(
            "SELECT payload FROM workflow_effect_invocations WHERE invocation_id=?",
            (handle.invocation_id,),
        ).fetchall()
        assert not any(json.loads(row[0])["operation"] == "call:http" for row in indexed)
        admission_before = services.effect_journal.get(admission_identity(handle))
        assert admission_before is not None and admission_before.status == "completed"
        assert services.recovery_definitions.resolve(handle).fingerprint == plan.fingerprint
        assert services.invocations.read_input(handle) == {}
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()
        services.close()

    restarted = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
    resumed_engine = engine_type()
    await resumed_engine.initialize(restarted)
    persisted = restarted.invocations.get(handle.invocation_id)
    assert persisted is not None and persisted.status == "running"
    assert restarted.effect_journal.get(admission_identity(persisted)) == admission_before
    slow_started = asyncio.Event()
    calls = []

    async def resumed_call(protocol, payload):
        branch = payload["body"]["branch"]
        calls.append(branch)
        if branch == "slow":
            slow_started.set()
            await asyncio.Event().wait()
        await slow_started.wait()
        return {"winner": "fast"}

    restarted.call_protocol = resumed_call
    try:
        result = await asyncio.wait_for(resumed_engine.resume(persisted, {}, plan), timeout=20)
        assert result.status == "completed", result.error
        assert result.output == {"winner": "fast"}
        assert sorted(calls) == ["fast", "slow"]
        decision, receipt = _decisions(restarted)[0]
        assert decision["winner"] == 1 and receipt == 1
        assert restarted.effect_journal.get(admission_identity(persisted)) == admission_before
    finally:
        await resumed_engine.shutdown()
        restarted.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("outer_competes", [False, True])
async def test_end_outside_a_competitive_scope_drains_it_without_choosing_its_winner(
    services, engine_name, engine_type, outer_competes
):
    started = set()
    drained = set()
    ready = asyncio.Event()

    async def call(protocol, payload):
        branch = payload["body"]["branch"]
        if branch == "finish":
            await ready.wait()
            return {"ended": True}
        started.add(branch)
        if len(started) == 2:
            ready.set()
        try:
            await asyncio.Event().wait()
        finally:
            drained.add(branch)

    services.call_protocol = call
    finish = _call("finish")
    finish["then"] = "end"
    plan = _plan(
        [
            {
                "outer": {
                    "fork": {
                        "compete": outer_competes,
                        "branches": [{"nested": {"do": [_race()]}}, {"finish": finish}],
                    }
                }
            },
            {"never": _call("never")},
        ]
    )
    engine, handle = await _engine(services, engine_name, engine_type, plan)
    try:
        result = await asyncio.wait_for(engine.invoke(plan, handle, {}), timeout=20)
        assert result.status == "completed", result.error
        assert result.output == {"ended": True}
        assert drained == {"slow", "fast"}
        decisions = _decisions(services)
        assert len(decisions) == int(outer_competes)
        if outer_competes:
            decision, receipt = decisions[0]
            assert decision["status"] == "ended" and decision["winner"] == 1 and receipt == 1
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("style", ["inline", "reusable", "nested", "transition", "catch_retry"])
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_repeated_race_restart_keeps_occurrences_and_never_repeats_committed_branches(
    tmp_path, engine_name, engine_type, style, backend, monkeypatch
):
    config, database_root = _configuration(tmp_path, backend)
    services = RuntimeServices(config, model=FakeModel(), database_root=database_root)
    existing_keys = _existing_keys(services)
    started = [asyncio.Event(), asyncio.Event()]
    calls = []
    original_mark = services.fork_decisions.mark_drained

    async def call(protocol, payload):
        index = len(calls)
        if payload["body"]["branch"] == "slow":
            started[index].set()
            await asyncio.Event().wait()
        await started[index].wait()
        calls.append(index)
        if style == "catch_retry" and index == 0:
            raise WorkflowExecutionError("first attempt failed")
        return {"iteration": index + 1}

    def interrupt_second_occurrence(identity, **authority):
        decision = services.fork_decisions.get(identity)
        if decision.output == {"iteration": 2}:
            running.cancel()
            raise asyncio.CancelledError
        original_mark(identity, **authority)

    race = _race()
    functions = None
    if style == "transition":
        tasks = [
            race,
            {
                "repeat": {
                    "switch": [
                        {"again": {"when": "${ .iteration < 2 }", "then": "race"}},
                        {"done": {"then": "end"}},
                    ]
                }
            },
        ]
    elif style == "catch_retry":
        tasks = [
            {"recover": {"try": [race], "catch": {"retry": {"limit": {"attempt": {"count": 1}}}}}}
        ]
    else:
        body = [race]
        if style == "reusable":
            functions = {"contest": race["race"]}
            body = [{"invoke": {"call": "contest"}}]
        elif style == "nested":
            body = [{"outer": {"fork": {"compete": True, "branches": [{"inner": {"do": body}}]}}}]
        tasks = [{"loop": {"for": {"in": [0, 1]}, "do": body}}]
    plan = _plan(tasks, functions)
    services.call_protocol = call
    monkeypatch.setattr(services.fork_decisions, "mark_drained", interrupt_second_occurrence)
    engine, handle = await _engine(services, engine_name, engine_type, plan)
    running = asyncio.create_task(engine.invoke(plan, handle, {}))
    try:
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(running, timeout=20)
        assert calls == [0, 1]
        recorded = _decisions(services, existing_keys)
        expected_count = 3 if style == "nested" else 2
        assert len(recorded) == expected_count
        assert any(receipt == 1 for _, receipt in recorded)
        assert any(receipt == 0 for _, receipt in recorded)
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()
        services.close()

    restarted = RuntimeServices(config, model=FakeModel(), database_root=database_root)
    resumed_engine = engine_type()
    await resumed_engine.initialize(restarted)
    persisted = restarted.invocations.get(handle.invocation_id)
    assert persisted is not None and persisted.status == "running"

    async def forbidden_call(protocol, payload):
        pytest.fail("repeated-race restart must preserve every committed occurrence")

    restarted.call_protocol = forbidden_call
    try:
        result = await asyncio.wait_for(resumed_engine.resume(persisted, {}, plan), timeout=20)
        assert result.status == "completed", result.error
        assert result.output == {"iteration": 2}
        after = _decisions(restarted, existing_keys)
        assert all(receipt == 1 for _, receipt in after)
        assert len(after) == (4 if style == "nested" else 2)
        for decision, _ in recorded:
            assert decision in [d for d, _ in after]
    finally:
        await resumed_engine.shutdown()
        restarted.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
async def test_storage_failure_faults_and_drains_losers_without_a_winner(
    services, engine_name, engine_type, monkeypatch
):
    started = asyncio.Event()
    drained = asyncio.Event()

    async def call(protocol, payload):
        if payload["body"]["branch"] == "slow":
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                drained.set()
        await started.wait()
        return {"winner": "fast"}

    def fail_claim(*args, **kwargs):
        raise WorkflowExecutionError("competitive fork decision could not be stored")

    services.call_protocol = call
    monkeypatch.setattr(services.fork_decisions, "claim", fail_claim)
    plan = _plan([_race()])
    engine, handle = await _engine(services, engine_name, engine_type, plan)
    try:
        result = await asyncio.wait_for(engine.invoke(plan, handle, {}), timeout=20)
        assert result.status == "faulted" and result.output is None
        assert result.error["message"] == "competitive fork decision could not be stored"
        assert drained.is_set() and _decisions(services) == []
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
async def test_cancellation_before_completion_drains_both_branches_without_a_decision(
    services, engine_name, engine_type
):
    started = set()
    drained = set()
    both_started = asyncio.Event()

    async def call(protocol, payload):
        branch = payload["body"]["branch"]
        started.add(branch)
        if len(started) == 2:
            both_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            drained.add(branch)

    services.call_protocol = call
    plan = _plan([_race()])
    engine, handle = await _engine(services, engine_name, engine_type, plan)
    running = asyncio.create_task(engine.invoke(plan, handle, {}))
    try:
        await asyncio.wait_for(both_started.wait(), timeout=20)
        cancelled = await engine.cancel(handle, operation_id="cancel-before-winner")
        assert cancelled.status == "cancelled"
        assert (await asyncio.wait_for(running, timeout=20)).status == "cancelled"
        assert drained == {"slow", "fast"} and _decisions(services) == []
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
async def test_a_losing_wait_does_not_leave_the_invocation_waiting(
    services, engine_name, engine_type
):
    waiting = asyncio.Event()

    async def call(protocol, payload):
        while handle.status != "waiting":
            await asyncio.sleep(0)
        waiting.set()
        return {"winner": "fast"}

    services.call_protocol = call
    race = _race()
    race["race"]["fork"]["branches"][0] = {"slow": {"wait": {"seconds": 300}}}
    plan = _plan([race])
    engine, handle = await _engine(services, engine_name, engine_type, plan)
    try:
        result = await asyncio.wait_for(engine.invoke(plan, handle, {}), timeout=20)
        assert waiting.is_set()
        assert result.status == handle.status == "completed", result.error
        assert result.output == {"winner": "fast"}
        assert _decisions(services)[0][1] == 1
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
async def test_catch_retry_starts_a_distinct_race_after_a_faulted_winner(
    services, engine_name, engine_type
):
    slow_started = [asyncio.Event(), asyncio.Event()]
    attempts = []
    drained = []

    async def call(protocol, payload):
        attempt = len(attempts)
        if payload["body"]["branch"] == "slow":
            slow_started[attempt].set()
            try:
                await asyncio.Event().wait()
            finally:
                drained.append(attempt)
        await slow_started[attempt].wait()
        attempts.append(attempt)
        if attempt == 0:
            raise WorkflowExecutionError("controlled first attempt failure")
        return {"attempt": attempt}

    services.call_protocol = call
    plan = _plan(
        [{"recover": {"try": [_race()], "catch": {"retry": {"limit": {"attempt": {"count": 1}}}}}}]
    )
    engine, handle = await _engine(services, engine_name, engine_type, plan)
    try:
        result = await asyncio.wait_for(engine.invoke(plan, handle, {}), timeout=20)
        assert result.status == "completed", result.error
        assert result.output == {"attempt": 1}
        assert attempts == drained == [0, 1]
        decisions = _decisions(services)
        assert len(decisions) == 2 and all(receipt == 1 for _, receipt in decisions)
        assert sorted(d["status"] for d, _ in decisions) == ["completed", "faulted"]
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
async def test_winning_error_retains_its_declared_ows_status_for_catch_filtering(
    services, engine_name, engine_type
):
    started = asyncio.Event()

    async def call(protocol, payload):
        if payload["body"]["branch"] == "slow":
            started.set()
            await asyncio.Event().wait()
        await started.wait()
        raise ExpressionError("controlled expression error", details={"status": 400})

    services.call_protocol = call
    plan = _plan(
        [
            {
                "recover": {
                    "try": [_race()],
                    "catch": {
                        "errors": {"with": {"status": 400}},
                        "do": [{"handled": {"set": {"handled": True}}}],
                    },
                }
            }
        ]
    )
    engine, handle = await _engine(services, engine_name, engine_type, plan)
    try:
        result = await asyncio.wait_for(engine.invoke(plan, handle, {}), timeout=20)
        assert result.status == "completed", result.error
        assert result.output == {"handled": True}
        decision, receipt = _decisions(services)[0]
        assert decision["error"]["code"] == "expression_error"
        assert decision["error_status_code"] == 400 and receipt == 1
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("winner_outcome", ["value", "null", "fault", "end"])
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("interruption", ["before_receipt", "during_cleanup"])
async def test_restart_after_winner_commit_replays_decision_without_starting_either_branch(
    tmp_path, engine_name, engine_type, winner_outcome, backend, interruption, monkeypatch
):
    config, database_root = _configuration(tmp_path, backend)
    services = RuntimeServices(config, model=FakeModel(), database_root=database_root)
    # Configured PostgreSQL tests share a database with other integration cases.
    # Compare only decisions created by this test; never erase another record.
    existing_keys = _existing_keys(services)
    slow_started = asyncio.Event()
    cleanup_started = asyncio.Event()
    slow_drained = asyncio.Event()
    fast_calls = []

    async def call(protocol, payload):
        if payload["body"]["branch"] == "slow":
            slow_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleanup_started.set()
                if interruption == "during_cleanup":
                    await asyncio.Event().wait()
                slow_drained.set()
        await slow_started.wait()
        fast_calls.append(payload["body"]["branch"])
        if winner_outcome == "fault":
            raise WorkflowExecutionError("winning branch failed", details={"type": "urn:race"})
        return None if winner_outcome == "null" else {"winner": "fast"}

    def interrupt_before_cleanup_receipt(identity, **authority):
        # The atomic winner and cancellation intents exist. Neither the cleanup
        # receipt nor the parent task's native completed checkpoint exists yet.
        assert services.fork_decisions.get(identity).drained is False
        running.cancel()
        raise asyncio.CancelledError

    services.call_protocol = call
    if interruption == "before_receipt":
        monkeypatch.setattr(
            services.fork_decisions, "mark_drained", interrupt_before_cleanup_receipt
        )
    race = _race()
    tasks = [race]
    if winner_outcome == "end":
        race["race"]["fork"]["branches"][1]["fast"]["then"] = "end"
        tasks.append({"never": _call("never")})
    plan = _plan(tasks)
    engine, handle = await _engine(services, engine_name, engine_type, plan)
    running = asyncio.create_task(engine.invoke(plan, handle, {}))
    try:
        if interruption == "during_cleanup":
            await asyncio.wait_for(cleanup_started.wait(), timeout=20)
            assert fast_calls == ["fast"] and not slow_drained.is_set()
            running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(running, timeout=20)
        assert slow_drained.is_set() == (interruption == "before_receipt")
        assert fast_calls == ["fast"]
        recorded = _decisions(services, existing_keys)
        assert len(recorded) == 1 and recorded[0][1] == 0
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()
        services.close()

    restarted = RuntimeServices(config, model=FakeModel(), database_root=database_root)
    resumed_engine = engine_type()
    await resumed_engine.initialize(restarted)
    persisted = restarted.invocations.get(handle.invocation_id)
    assert persisted is not None and persisted.status == "running"

    async def forbidden_call(protocol, payload):
        pytest.fail("a committed competitive decision must not execute either branch on replay")

    restarted.call_protocol = forbidden_call
    try:
        result = await asyncio.wait_for(resumed_engine.resume(persisted, {}, plan), timeout=20)
        if winner_outcome == "fault":
            assert result.status == "faulted"
            assert result.error == recorded[0][0]["error"]
        else:
            expected = None if winner_outcome == "null" else {"winner": "fast"}
            assert result.status == "completed", result.error
            assert result.output == expected
        assert _decisions(restarted, existing_keys)[0][1] == 1
        assert _decisions(restarted, existing_keys)[0][0] == recorded[0][0]
    finally:
        await resumed_engine.shutdown()
        restarted.close()
