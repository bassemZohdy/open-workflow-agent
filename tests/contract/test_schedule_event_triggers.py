"""Portable pre-invocation trigger cycles and actual scheduled invocation inputs."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from engine_cases import engine_cases
from open_workflow_agent._event_triggers import EventTriggerStore, TriggerCycleBinding
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.errors import (
    EventValidationError,
    InvocationStateError,
    WorkflowExecutionError,
)
from open_workflow_agent.scheduling import WorkflowScheduler
from open_workflow_agent.workflow import compile_workflow
from test_durable_scheduling import _claim
from test_durable_timers import _services, _use_clock, _workflow


def _create(
    services: Any, strategy: dict[str, Any], scope: str, read: str = "data"
) -> tuple[Any, Any]:
    source = _workflow(
        [{"report": {"call": "llm:1.0.0@default", "with": {"prompt": "${ $workflow.input }"}}}],
        "event-trigger",
    )
    source["schedule"] = {"on": strategy, "read": read}
    plan = compile_workflow(source)
    record = services.schedules.create(
        plan, {"expected": "chosen"}, now=1000, owner_principal=scope, event_scope=scope
    )
    return plan, record


def _triggers(services: Any, engine: str) -> EventTriggerStore:
    assert services.event_store is not None
    return EventTriggerStore(
        services.event_store, services.schedules, engine=engine, clock=lambda: 1000
    )


async def _publish(
    services: Any, scope: str, subject: str, kind: str, data: Any, *, event_id: str | None = None
) -> Any:
    return await services.event_bus.publish_scoped(
        {
            "id": event_id or str(uuid4()),
            "source": "urn:contract:trigger",
            "type": kind,
            "subject": subject,
            "time": "2026-10-12T00:00:00Z",
            "data": data,
        },
        default_source="urn:unused",
        scope=scope,
    )


def _cycle(store: EventTriggerStore, schedule_id: str) -> TriggerCycleBinding:
    for _ in range(20):
        binding = store.poll(schedule_id)
        if binding is not None:
            return binding
    raise AssertionError("retained matching arrivals did not complete a trigger cycle")


async def _dispatch(services: Any, engine: Any, record: Any, binding: TriggerCycleBinding) -> None:
    handle = services.invocations.get(binding.invocation_id)
    assert handle is not None and services.invocations.read_input(handle) == binding.input_data
    assert handle.owner_principal == record.owner_principal
    scheduler = WorkflowScheduler(services, engine, clock=lambda: 1000)
    selected = _claim(services, record.schedule_id, 1000, "trigger-dispatch-owner")
    assert (
        services.schedules.bind_invocation(
            selected, engine=engine.engine_name, now=1000
        ).invocation_id
        == binding.invocation_id
    )
    await scheduler._execute(selected)
    completed = services.invocations.get(binding.invocation_id)
    assert completed is not None and completed.status == "completed", completed
    assert completed.workflow_fingerprint == record.workflow_fingerprint
    assert completed.output == {"prompt": binding.input_data}


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("read", ["data", "envelope", "raw"])
async def test_trigger_repeated_cycles_create_distinct_real_invocations_with_declared_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: Any,
    backend: str,
    read: str,
) -> None:
    _use_clock(monkeypatch, lambda: 1000)
    scope, subject = str(uuid4()), str(uuid4())
    model = FakeModel(lambda payload: payload)
    services = _services(tmp_path, backend, model)
    engine = engine_type()
    _, record = _create(
        services, {"one": {"with": {"type": "fixture.ready", "subject": subject}}}, scope, read
    )
    store = _triggers(services, engine_name)
    first_event = await _publish(services, scope, subject, "fixture.ready", None)
    store.ensure_registered(record)
    try:
        await engine.initialize(services)
        await engine._timer_dispatcher.stop()
        first = _cycle(store, record.schedule_id)
        projection = (
            first_event.as_dict()
            if read == "envelope"
            else first_event.raw()
            if read == "raw"
            else None
        )
        assert first.input_data == [projection]
        assert first.identity.cycle_ordinal == 1 and first.events == (first_event,)
        await _dispatch(services, engine, record, first)
        assert store.poll(record.schedule_id) is None
        second_event = await _publish(services, scope, subject, "fixture.ready", {"value": 2})
        second = _cycle(store, record.schedule_id)
        assert second.identity.cycle_ordinal == 2 and second.invocation_id != first.invocation_id
        assert second.cursor > first.cursor
        projection = (
            second_event.as_dict()
            if read == "envelope"
            else second_event.raw()
            if read == "raw"
            else {"value": 2}
        )
        assert second.input_data == [projection]
        await _dispatch(services, engine, record, second)
        assert model.calls == [{"prompt": first.input_data}, {"prompt": second.input_data}]
    finally:
        store.close(record.schedule_id)
        services.schedules.cancel(record.schedule_id)
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize(
    "case", ["all-correlated", "all-null", "any", "until-expression", "until-filter"]
)
async def test_trigger_strategy_preserves_order_correlation_and_excludes_terminators(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: Any,
    backend: str,
    case: str,
) -> None:
    _use_clock(monkeypatch, lambda: 1000)
    scope, subject = str(uuid4()), str(uuid4())
    model = FakeModel(lambda payload: payload)
    services = _services(tmp_path, backend, model)
    engine = engine_type()
    first: dict[str, Any] = {"with": {"type": "fixture.first", "subject": subject}}
    second: dict[str, Any] = {"with": {"type": "fixture.second", "subject": subject}}
    strategy: dict[str, Any]
    expected: list[Any]
    if case.startswith("all-"):
        for selected in (first, second):
            selected["correlate"] = {"order": {"from": "${ .data.order }"}}
        order = None if case == "all-null" else "chosen"
        strategy = {"all": [first, second]}
        events = [
            ("fixture.first", {"order": order, "value": 1}),
            ("fixture.second", {"order": False, "value": "wrong"}),
            ("fixture.second", {"order": order, "value": 2}),
        ]
        expected = [events[0][1], events[2][1]]
    elif case == "any":
        strategy = {"any": [first, second]}
        events = [("fixture.unrelated", 0), ("fixture.second", {"value": 2})]
        expected = [events[1][1]]
    else:
        strategy = {"any": [first]}
        if case == "until-expression":
            strategy["until"] = "${ .stop == true }"
            events = [
                ("fixture.first", {"value": 1}),
                ("fixture.first", {"value": 2}),
                ("fixture.first", {"stop": True}),
            ]
        else:
            strategy["until"] = {"one": {"with": {"type": "fixture.stop", "subject": subject}}}
            events = [
                ("fixture.first", {"value": 1}),
                ("fixture.first", {"value": 2}),
                ("fixture.stop", {"stop": True}),
            ]
        expected = [events[0][1], events[1][1]]
    _, record = _create(services, strategy, scope)
    store = _triggers(services, engine_name)
    store.ensure_registered(record)
    try:
        await engine.initialize(services)
        await engine._timer_dispatcher.stop()
        for kind, data in events:
            await _publish(services, scope, subject, kind, data)
        binding = _cycle(store, record.schedule_id)
        assert binding.input_data == expected
        assert [event.data for event in binding.events] == expected
        await _dispatch(services, engine, record, binding)
        assert model.calls == [{"prompt": expected}]
        assert store.state(record.schedule_id).identity.cycle_ordinal == 2
    finally:
        store.close(record.schedule_id)
        services.schedules.cancel(record.schedule_id)
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_partial_correlated_trigger_survives_reopen_and_ignores_conflicting_duplicate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: Any,
    backend: str,
) -> None:
    _use_clock(monkeypatch, lambda: 1000)
    scope, subject = str(uuid4()), str(uuid4())
    model = FakeModel(lambda payload: payload)
    services = _services(tmp_path, backend, model)
    engine = engine_type()
    filters = [
        {
            "with": {"type": kind, "subject": subject},
            "correlate": {"order": {"from": "${ .data.order }", "expect": "${ .expected }"}},
        }
        for kind in ("fixture.first", "fixture.second")
    ]
    _, record = _create(services, {"all": filters}, scope)
    store = _triggers(services, engine_name)
    store.ensure_registered(record)
    original_event = await _publish(
        services, scope, subject, "fixture.first", {"order": "chosen", "value": 1}, event_id="first"
    )
    assert store.poll(record.schedule_id) is None
    partial = store.state(record.schedule_id)
    assert partial.correlations == {"order": "chosen"} and partial.events == (original_event,)
    services.close()
    services = _services(tmp_path, backend, model)
    store = _triggers(services, engine_name)
    try:
        assert store.state(record.schedule_id) == partial
        duplicate = await _publish(
            services, scope, subject, "fixture.first", original_event.data, event_id="first"
        )
        assert duplicate == original_event
        with pytest.raises(EventValidationError):
            await _publish(
                services, scope, subject, "fixture.first", {"order": "different"}, event_id="first"
            )
        assert store.poll(record.schedule_id) is None
        assert store.state(record.schedule_id) == partial
        await _publish(services, scope, subject, "fixture.second", {"order": "chosen", "value": 2})
        binding = _cycle(store, record.schedule_id)
        assert binding.input_data == [
            {"order": "chosen", "value": 1},
            {"order": "chosen", "value": 2},
        ]
        await engine.initialize(services)
        await engine._timer_dispatcher.stop()
        await _dispatch(services, engine, record, binding)
        assert len(model.calls) == 1
    finally:
        store.close(record.schedule_id)
        services.schedules.cancel(record.schedule_id)
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_trigger_trusted_scope_cannot_be_forged_and_workflow_listener_retains_broadcast(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: Any,
    backend: str,
) -> None:
    _use_clock(monkeypatch, lambda: 1000)
    scope, subject = str(uuid4()), str(uuid4())
    model = FakeModel(lambda payload: payload)
    services = _services(tmp_path, backend, model)
    engine = engine_type()
    strategy = {"one": {"with": {"type": "fixture.ready", "subject": subject}}}
    _, record = _create(services, strategy, scope)
    store = _triggers(services, engine_name)
    store.ensure_registered(record)
    listener_plan = compile_workflow(
        _workflow(
            [{"consume": {"listen": {"to": strategy}}}], "independent-trigger-broadcast-listener"
        )
    )
    listener_handle = services.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id=None,
        workflow_name=listener_plan.name,
        workflow_version=listener_plan.version,
        workflow_fingerprint=listener_plan.fingerprint,
        owner_principal=scope,
    )
    running: asyncio.Task[Any] | None = None
    try:
        await engine.initialize(services)
        await engine._timer_dispatcher.stop()
        running = asyncio.create_task(engine.invoke(listener_plan, listener_handle, {}))
        async with asyncio.timeout(30):
            while listener_handle.status != "waiting":
                if running.done():
                    raise AssertionError(await running)
                await asyncio.sleep(0.01)
        await _publish(
            services,
            "untrusted-" + scope,
            subject,
            "fixture.ready",
            {"principal": scope, "user_id": scope, "value": "forged"},
        )
        assert store.poll(record.schedule_id) is None
        assert not store.state(record.schedule_id).events
        assert not running.done()
        accepted = await _publish(services, scope, subject, "fixture.ready", {"value": "accepted"})
        binding = _cycle(store, record.schedule_id)
        listener_result = await asyncio.wait_for(running, timeout=30)
        assert listener_result.status == "completed", listener_result.error
        assert listener_result.output == [accepted.data]
        assert binding.events == (accepted,)
        await _dispatch(services, engine, record, binding)
        assert model.calls == [{"prompt": [{"value": "accepted"}]}]
    finally:
        if running is not None:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
        store.close(record.schedule_id)
        services.schedules.cancel(record.schedule_id)
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize(
    "failure", ["trigger-selection-before-commit", "trigger-binding-before-commit"]
)
async def test_trigger_selection_and_real_invocation_binding_roll_back_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: Any,
    backend: str,
    failure: str,
) -> None:
    _use_clock(monkeypatch, lambda: 1000)
    scope, subject = str(uuid4()), str(uuid4())
    model = FakeModel(lambda payload: payload)
    services = _services(tmp_path, backend, model)
    engine = engine_type()
    _, record = _create(
        services, {"one": {"with": {"type": "fixture.ready", "subject": subject}}}, scope
    )
    store = _triggers(services, engine_name)
    store.ensure_registered(record)
    before = store.state(record.schedule_id)

    def inject(name: str) -> None:
        if name == failure:
            raise RuntimeError("controlled trigger transaction failure")

    try:
        await engine.initialize(services)
        await engine._timer_dispatcher.stop()
        event = await _publish(services, scope, subject, "fixture.ready", {"value": 1})
        store._failpoint = inject
        with pytest.raises(WorkflowExecutionError):
            store.poll(record.schedule_id)
        assert store.state(record.schedule_id) == before
        assert not model.calls
        assert not services.schedules.active_invocations(record.schedule_id)
        assert not services.schedules.connection.execute(
            "SELECT invocation_id FROM schedule_slots WHERE schedule_id=?", (record.schedule_id,)
        ).fetchall()
        own = [
            json.loads(row[0])
            for row in services.invocations.connection.execute(
                "SELECT payload FROM invocations"
            ).fetchall()
            if json.loads(row[0]).get("owner_principal") == scope
        ]
        assert not own, "a rolled-back trigger must not leave an orphan invocation"
        store._failpoint = lambda _name: None
        binding = _cycle(store, record.schedule_id)
        assert binding.identity == before.identity and binding.events == (event,)
        await _dispatch(services, engine, record, binding)
        assert model.calls == [{"prompt": [{"value": 1}]}]
    finally:
        store._failpoint = lambda _name: None
        store.close(record.schedule_id)
        services.schedules.cancel(record.schedule_id)
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("corruption", ["missing-cycle", "changed-cycle-digest"])
async def test_corrupt_completed_trigger_cycle_rejects_native_dispatch_before_parent_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: Any,
    backend: str,
    corruption: str,
) -> None:
    _use_clock(monkeypatch, lambda: 1000)
    scope, subject = str(uuid4()), str(uuid4())
    model = FakeModel(lambda payload: payload)
    services = _services(tmp_path, backend, model)
    engine = engine_type()
    plan, record = _create(
        services, {"one": {"with": {"type": "fixture.ready", "subject": subject}}}, scope
    )
    store = _triggers(services, engine_name)
    store.ensure_registered(record)
    try:
        await engine.initialize(services)
        await engine._timer_dispatcher.stop()
        await _publish(services, scope, subject, "fixture.ready", {"value": 1})
        binding = _cycle(store, record.schedule_id)
        handle = services.invocations.get(binding.invocation_id)
        assert handle is not None
        parent = handle._persisted_payload
        native_before = await engine._scheduled_native_state_exists(handle, plan)
        assert not native_before
        if corruption == "missing-cycle":
            store.connection.execute(
                "DELETE FROM event_trigger_cycles WHERE schedule_id=? AND ordinal=1",
                (record.schedule_id,),
            )
        else:
            store.connection.execute(
                "UPDATE event_trigger_cycles SET digest=? WHERE schedule_id=? AND ordinal=1",
                ("0" * 64, record.schedule_id),
            )
        store.connection.commit()
        with pytest.raises((WorkflowExecutionError, InvocationStateError)):
            await engine.dispatch_scheduled(handle, plan, binding.input_data)
        saved = services.invocations.get(handle.invocation_id)
        assert saved is not None and saved._persisted_payload == parent
        assert services.invocations.read_input(saved) == binding.input_data
        assert not model.calls
        assert not await engine._scheduled_native_state_exists(saved, plan)
    finally:
        # The negative evidence stays corrupt; cancel only this owned schedule.
        services.schedules.cancel(record.schedule_id)
        await engine.shutdown()
        services.close()
