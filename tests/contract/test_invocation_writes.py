"""Common invocation state survives late writes from independently loaded handles."""

from __future__ import annotations

import asyncio
import json
import os

import pytest
from engine_cases import engine_cases
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.errors import InvocationStateError, ModelError
from open_workflow_agent.persistence import InvocationStore
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow


def _services(root, backend, model):
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    config = (
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}})
        if datasource
        else RuntimeConfig()
    )
    return RuntimeServices(config, model=model, database_root=None if datasource else root)


def _handle(services, engine_name, plan):
    return services.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )


def _workflow(tasks):
    return compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "tests",
                "name": "invocation-writes",
                "version": "1.0.0",
            },
            "do": tasks,
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("winner", ["value", "null", "fault", "cancelled"])
@pytest.mark.parametrize("late_outcome", ["value", "fault"])
async def test_other_workers_terminal_outcome_survives_late_native_execution(
    tmp_path, engine_name, engine_type, backend, winner, late_outcome
):
    started = asyncio.Event()
    release = asyncio.Event()

    async def late(_prompt):
        started.set()
        await release.wait()
        if late_outcome == "fault":
            raise ModelError("late model fault")
        return {"late": True}

    first = _services(tmp_path, backend, FakeModel(late))
    other = _services(tmp_path, backend, FakeModel({"forbidden": True}))
    engine = engine_type()
    operator = engine_type()
    await engine.initialize(first)
    await operator.initialize(other)
    plan = _workflow([{"action": {"call": "llm:1.0.0@default", "with": {"prompt": "once"}}}])
    handle = _handle(first, engine_name, plan)
    running = asyncio.create_task(engine.invoke(plan, handle, {"original": True}))
    try:
        await asyncio.wait_for(started.wait(), 10)
        fresh = other.invocations.get(handle.invocation_id)
        assert fresh is not None
        if winner == "cancelled":
            committed = await operator.cancel(fresh, operation_id="other-worker")
        else:
            committed = operator._record_result(
                fresh,
                status="faulted" if winner == "fault" else "completed",
                output={"retained": True} if winner == "value" else None,
                error={"code": "workflow_execution_error", "message": "retained", "details": {}}
                if winner == "fault"
                else None,
            )
        release.set()
        result = await asyncio.wait_for(running, 10)
        assert result.as_dict() == committed.as_dict()
        saved = other.invocations.get(handle.invocation_id)
        assert saved.status == committed.status
        assert saved.output == committed.output and saved.error == committed.error
        assert len(first.model.calls) == 1 and other.model.calls == []
        duplicate = await operator.resume(saved, {"different": True}, plan)
        assert duplicate.as_dict() == committed.as_dict() and other.model.calls == []
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()
        await operator.shutdown()
        first.close()
        other.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("action", ["resume", "cancel"])
async def test_operator_loaded_handle_controls_the_existing_active_invocation(
    tmp_path, engine_name, engine_type, backend, action
):
    services = _services(tmp_path, backend, FakeModel())
    engine = engine_type()
    await engine.initialize(services)
    plan = _workflow(
        [
            {"pause": {"wait": {"seconds": 300}}},
            {"report": {"set": {"resumed": "${ $resume_input }"}}},
        ]
    )
    original = _handle(services, engine_name, plan)
    running = asyncio.create_task(engine.invoke(plan, original, {}))
    try:
        async with asyncio.timeout(10):
            while original.status != "waiting":
                await asyncio.sleep(0.005)
        requested = services.invocations.get(original.invocation_id)
        assert requested is not original and requested.status == "waiting"
        result = await asyncio.wait_for(
            engine.resume(requested, None, plan)
            if action == "resume"
            else engine.cancel(requested, operation_id="loaded-handle"),
            10,
        )
        completed = await asyncio.wait_for(running, 10)
        assert completed.as_dict() == result.as_dict()
        assert original.status == result.status
        assert result.status == ("completed" if action == "resume" else "cancelled")
        if action == "resume":
            assert result.output == {"resumed": None}
        else:
            assert (
                sum(event.event_type == "WorkflowCancelled" for event in services.events.events)
                == 1
            )
        assert services.model.calls == []
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_stale_resume_handle_reads_committed_completion_before_native_dispatch(
    tmp_path, engine_name, engine_type, backend
):
    first = _services(tmp_path, backend, FakeModel({"forbidden": True}))
    other = _services(tmp_path, backend, FakeModel())
    engine = engine_type()
    await engine.initialize(first)
    plan = _workflow([{"action": {"call": "llm:1.0.0@default", "with": {"prompt": "forbidden"}}}])
    stale = _handle(first, engine_name, plan)
    try:
        fresh = other.invocations.get(stale.invocation_id)
        other.invocations.update(fresh, status="completed", output=None)
        result = await engine.resume(stale, {"different": True}, plan)
        assert result.status == "completed" and result.output is None and result.error is None
        assert first.model.calls == [] and stale.status == "completed"
    finally:
        await engine.shutdown()
        first.close()
        other.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("scope", ["leaf", "catch", "competitive"])
async def test_changed_active_state_stops_stale_execution_without_retry_or_terminal_write(
    tmp_path, engine_name, engine_type, backend, scope
):
    started = asyncio.Event()
    release = asyncio.Event()

    async def action(_prompt):
        started.set()
        await release.wait()
        return {"effect": True}

    body = [
        {"effect": {"call": "llm:1.0.0@default", "with": {"prompt": "once"}}},
        {"pause": {"wait": {"milliseconds": 0}}},
    ]
    if scope == "catch":
        body = [{"guard": {"try": body, "catch": {"retry": {"limit": {"attempt": {"count": 1}}}}}}]
    elif scope == "competitive":
        body = [
            {
                "race": {
                    "fork": {
                        "compete": True,
                        "branches": [
                            {"stale": {"do": body}},
                            {"loser": {"wait": {"seconds": 300}}},
                        ],
                    }
                }
            }
        ]
    first = _services(tmp_path, backend, FakeModel(action))
    other = _services(tmp_path, backend, FakeModel())
    engine = engine_type()
    await engine.initialize(first)
    plan = _workflow(body)
    original = _handle(first, engine_name, plan)
    running = asyncio.create_task(engine.invoke(plan, original, {}))
    try:
        await asyncio.wait_for(started.wait(), 10)
        fresh = other.invocations.get(original.invocation_id)
        other.invocations.update(fresh, status="running")
        # The status is unchanged; a distinct committed write still invalidates
        # this executor. A conflict must not enter catch retry or choose a winner.
        prior_decisions = {
            row[0]
            for row in first.fork_decisions.connection.execute(
                "SELECT identity FROM competitive_fork_decisions"
            ).fetchall()
        }
        release.set()
        with pytest.raises(InvocationStateError) as failure:
            await asyncio.wait_for(running, 10)
        assert failure.value.code == "invalid_invocation_state"
        saved = other.invocations.get(original.invocation_id)
        assert saved.status == "running" and saved.output is None and saved.error is None
        assert len(first.model.calls) == 1
        decisions = {
            row[0]
            for row in first.fork_decisions.connection.execute(
                "SELECT identity FROM competitive_fork_decisions"
            ).fetchall()
        }
        assert decisions == prior_decisions
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()
        first.close()
        other.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_dispatch_state_conflict_leaves_an_uncertain_action_intent_without_retry(
    tmp_path, engine_name, engine_type, backend
):
    async def action(_prompt):
        fresh = other.invocations.get(original.invocation_id)
        other.invocations.update(fresh, status="running")
        first.invocations.update(original, status="waiting")
        raise AssertionError("a stale writer must not regain authority")

    first = _services(tmp_path, backend, FakeModel(action))
    other = _services(tmp_path, backend, FakeModel())
    engine = engine_type()
    await engine.initialize(first)
    plan = _workflow(
        [
            {
                "guard": {
                    "try": [
                        {"effect": {"call": "llm:1.0.0@default", "with": {"prompt": "once"}}},
                    ],
                    "catch": {"retry": {"limit": {"attempt": {"count": 1}}}},
                }
            }
        ]
    )
    original = _handle(first, engine_name, plan)
    try:
        prior_effects = {
            row[0]
            for row in first.effect_journal.connection.execute(
                "SELECT identity FROM workflow_effects"
            ).fetchall()
        }
        with pytest.raises(InvocationStateError):
            await engine.invoke(plan, original, {})
        assert len(first.model.calls) == 1
        saved = other.invocations.get(original.invocation_id)
        assert saved.status == "running" and saved.error is None and saved.output is None
        rows = [
            row
            for row in first.effect_journal.connection.execute(
                "SELECT i.identity, e.status, i.payload FROM workflow_effect_invocations i "
                "JOIN workflow_effects e ON e.identity = i.identity "
                "WHERE i.invocation_id = ?",
                (original.invocation_id,),
            ).fetchall()
            if row[0] not in prior_effects
            and not json.loads(row[2])["operation"].startswith("policy:")
        ]
        assert len(rows) == 1 and rows[0][1] == "pending"
    finally:
        await engine.shutdown()
        first.close()
        other.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("action", ["resume", "cancel"])
async def test_terminal_commit_during_operator_refresh_wins_without_another_transition(
    tmp_path, monkeypatch, engine_name, engine_type, backend, action
):
    services = _services(tmp_path, backend, FakeModel())
    other = _services(tmp_path, backend, FakeModel())
    engine = engine_type()
    await engine.initialize(services)
    plan = _workflow([{"pause": {"wait": {"seconds": 300}}}])
    original = _handle(services, engine_name, plan)
    running = asyncio.create_task(engine.invoke(plan, original, {}))
    try:
        async with asyncio.timeout(10):
            while original.status != "waiting":
                await asyncio.sleep(0.005)
        requested = services.invocations.get(original.invocation_id)
        refresh = InvocationStore.refresh
        committed = {"code": "invocation_cancelled", "message": "other worker", "details": {}}
        transitions = []

        def racing_refresh(store, handle, **kwargs):
            if store is services.invocations and handle is original and not transitions:
                fresh = other.invocations.get(original.invocation_id)
                other.invocations.update(fresh, status="cancelled", error=committed)
                transitions.append("committed")
            return refresh(store, handle, **kwargs)

        monkeypatch.setattr(InvocationStore, "refresh", racing_refresh)
        result = await (
            engine.resume(requested, None, plan)
            if action == "resume"
            else engine.cancel(requested, operation_id="late operator")
        )
        assert transitions == ["committed"]
        assert result.status == "cancelled" and result.output is None and result.error == committed
        saved = other.invocations.get(original.invocation_id)
        assert saved.status == "cancelled" and saved.error == committed
        assert services.model.calls == []
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()
        services.close()
        other.close()
