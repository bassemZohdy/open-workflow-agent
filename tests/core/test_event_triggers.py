"""Pre-invocation trigger cycles over real committed events and slot bindings."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from open_workflow_agent._event_triggers import EventTriggerBackpressure, EventTriggerStore
from open_workflow_agent.effects import EffectJournal
from open_workflow_agent.errors import EventValidationError, WorkflowExecutionError
from open_workflow_agent.event_store import EventStore, EventStoreLimits
from open_workflow_agent.persistence import InvocationStore
from open_workflow_agent.scheduling import ScheduleRecord, ScheduleStore
from open_workflow_agent.workflow import compile_workflow


@pytest.fixture
def stores(tmp_path: Path) -> Iterator[tuple[EventStore, ScheduleStore, EventTriggerStore]]:
    invocations = InvocationStore(tmp_path / "runtime.sqlite3")
    journal = EffectJournal(tmp_path / "runtime.sqlite3", invocations=invocations)
    schedules = ScheduleStore(invocations=invocations)
    events = EventStore(journal)
    triggers = EventTriggerStore(events, schedules, engine="test", clock=lambda: 1000.0)
    yield events, schedules, triggers
    schedules.close()
    journal.close()
    invocations.close()


def _schedule(
    schedules: ScheduleStore, strategy: dict[str, Any], read: str = "data"
) -> ScheduleRecord:
    scope = "trusted-" + uuid4().hex
    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "trigger",
                "name": "trigger-" + uuid4().hex,
                "version": "1.0.0",
            },
            "schedule": {"on": strategy, "read": read},
            "do": [{"finish": {"set": {"received": "${ . }"}}}],
        }
    )
    return schedules.create(
        plan, {"expected": "bound"}, now=1000, owner_principal=scope, event_scope=scope
    )


def _publish(events: EventStore, record: ScheduleRecord, event_type: str, data: Any) -> str:
    event_id = uuid4().hex
    events.ingest(
        {"id": event_id, "type": event_type, "data": data},
        default_source="urn:trigger-test",
        scope=record.event_scope,
    )
    return event_id


@pytest.mark.parametrize("read", ["data", "envelope", "raw"])
def test_one_projection_and_repeated_cycle_create_distinct_real_invocations(
    stores: Any, read: str
) -> None:
    events, schedules, triggers = stores
    record = _schedule(schedules, {"one": {"with": {"type": "^first$"}}}, read)
    _publish(events, record, "first", {"value": 1})
    registration = triggers.ensure_registered(record)
    assert registration.scope == record.event_scope
    first = triggers.poll(record.schedule_id)
    assert first is not None and first.identity.cycle_ordinal == 1
    expected = (
        [{"value": 1}]
        if read == "data"
        else [first.events[0].as_dict()]
        if read == "envelope"
        else [first.events[0].raw()]
    )
    assert first.input_data == expected
    handle = schedules.invocations.get(first.invocation_id)
    assert handle is not None and handle.owner_principal == record.owner_principal
    assert schedules.invocations.read_input(handle) == expected
    assert triggers.poll(record.schedule_id) is None
    _publish(events, record, "first", {"value": 2})
    second = triggers.poll(record.schedule_id)
    assert second is not None and second.identity.cycle_ordinal == 2
    assert second.invocation_id != first.invocation_id
    assert triggers.state(record.schedule_id).identity.cycle_ordinal == 3


def test_all_partial_correlation_reopens_and_reuses_recorded_decision(stores: Any) -> None:
    events, schedules, triggers = stores
    correlation = {"key": {"from": "${ .data.key }", "expect": "${ $workflow.input.expected }"}}
    record = _schedule(
        schedules,
        {
            "all": [
                {"with": {"type": "^first$"}, "correlate": correlation},
                {"with": {"type": "^second$"}, "correlate": correlation},
            ]
        },
    )
    triggers.ensure_registered(record)
    _publish(events, record, "first", {"key": "bound", "value": 1})
    assert triggers.poll(record.schedule_id) is None
    before = triggers.state(record.schedule_id)
    assert before.correlations == {"key": "bound"} and before.matched_filters == (0,)
    reopened = EventTriggerStore(events, schedules, engine="test")
    _publish(events, record, "second", {"key": "wrong", "value": 2})
    assert reopened.poll(record.schedule_id) is None
    _publish(events, record, "second", {"key": "bound", "value": 3})
    binding = reopened.poll(record.schedule_id)
    assert binding is not None
    assert binding.input_data == [{"key": "bound", "value": 1}, {"key": "bound", "value": 3}]


@pytest.mark.parametrize("until", ["${ .stop == true }", {"one": {"with": {"type": "^stop$"}}}])
def test_any_until_excludes_terminator(stores: Any, until: Any) -> None:
    events, schedules, triggers = stores
    record = _schedule(schedules, {"any": [], "until": until})
    triggers.ensure_registered(record)
    _publish(events, record, "item", {"value": 1})
    assert triggers.poll(record.schedule_id) is None
    _publish(events, record, "stop", {"stop": True})
    result = triggers.poll(record.schedule_id)
    assert result is not None and result.input_data == [{"value": 1}]


def test_empty_all_completes_one_bounded_cycle_per_poll(stores: Any) -> None:
    _, schedules, triggers = stores
    record = _schedule(schedules, {"all": []})
    first = triggers.poll(record.schedule_id)
    second = triggers.poll(record.schedule_id)
    assert first is not None and second is not None
    assert first.input_data == second.input_data == []
    assert first.invocation_id != second.invocation_id
    assert second.identity.cycle_ordinal == first.identity.cycle_ordinal + 1


def test_partial_and_completed_selected_arrivals_are_pinned_but_unmatched_can_prune(
    stores: Any,
) -> None:
    events, schedules, triggers = stores
    record = _schedule(
        schedules, {"all": [{"with": {"type": "^first$"}}, {"with": {"type": "^second$"}}]}
    )
    triggers.ensure_registered(record)
    selected = _publish(events, record, "first", 1)
    ignored = _publish(events, record, "ignored", 0)
    assert triggers.poll(record.schedule_id) is None
    assert triggers.poll(record.schedule_id) is None
    events.connection.execute(
        "UPDATE event_inbox SET created_at=0 WHERE scope=?", (record.event_scope,)
    )
    events.connection.commit()
    assert events.prune(now=100000) == 1
    assert events.connection.execute(
        "SELECT 1 FROM event_inbox WHERE event_id=?", (selected,)
    ).fetchone()
    assert (
        events.connection.execute(
            "SELECT 1 FROM event_inbox WHERE event_id=?", (ignored,)
        ).fetchone()
        is None
    )
    _publish(events, record, "second", 2)
    assert triggers.poll(record.schedule_id) is not None
    assert events.prune(now=100000) == 0


def test_unscanned_scope_arrivals_pinned_and_other_scope_does_not_authorize_trigger(
    stores: Any,
) -> None:
    events, schedules, triggers = stores
    record = _schedule(schedules, {"one": {"with": {"type": "^first$"}}})
    triggers.ensure_registered(record)
    events.ingest(
        {"type": "first", "data": {"principal": record.event_scope}},
        default_source="urn:test",
        scope="other",
    )
    assert triggers.poll(record.schedule_id) is None
    _publish(events, record, "first", 1)
    events.connection.execute("UPDATE event_inbox SET created_at=0")
    events.connection.commit()
    assert events.prune(now=100000) == 1
    assert triggers.poll(record.schedule_id) is not None


def test_trigger_binding_failure_rolls_back_decisions_cursor_invocation_and_input(
    stores: Any,
) -> None:
    events, schedules, triggers = stores
    record = _schedule(schedules, {"one": {"with": {"type": "^first$"}}})
    triggers.ensure_registered(record)
    _publish(events, record, "first", 1)

    def stop(name: str) -> None:
        if name == "trigger-binding-before-commit":
            raise WorkflowExecutionError("simulated trigger stop")

    triggers._failpoint = stop
    with pytest.raises(WorkflowExecutionError):
        triggers.poll(record.schedule_id)
    assert triggers.state(record.schedule_id).cursor == 0
    assert (
        schedules.invocations.connection.execute("SELECT COUNT(*) FROM invocations").fetchone()[0]
        == 0
    )
    triggers._failpoint = lambda _name: None
    assert triggers.poll(record.schedule_id) is not None


@pytest.mark.parametrize(
    "evidence",
    ["event_trigger_heads", "event_trigger_decisions", "event_trigger_cycles", "schedule_bindings"],
)
def test_missing_evidence_fails_before_another_invocation_or_cursor_mutation(
    stores: Any, evidence: str
) -> None:
    events, schedules, triggers = stores
    record = _schedule(schedules, {"one": {"with": {"type": "^first$"}}})
    _publish(events, record, "first", 1)
    result = triggers.poll(record.schedule_id)
    assert result is not None
    if evidence == "schedule_bindings":
        events.connection.execute(
            "DELETE FROM schedule_bindings WHERE slot_key="
            "(SELECT slot_key FROM schedule_slots WHERE invocation_id=?)",
            (result.invocation_id,),
        )
    else:
        events.connection.execute(
            f"DELETE FROM {evidence} WHERE schedule_id=?", (record.schedule_id,)
        )
    events.connection.commit()
    before = events.connection.execute("SELECT * FROM event_consumer_pins").fetchall()
    _publish(events, record, "first", 2)
    with pytest.raises(WorkflowExecutionError):
        triggers.poll(record.schedule_id)
    assert events.connection.execute("SELECT * FROM event_consumer_pins").fetchall() == before
    assert (
        schedules.invocations.connection.execute("SELECT COUNT(*) FROM invocations").fetchone()[0]
        == 1
    )


def test_trigger_and_listener_share_subscription_capacity(stores: Any) -> None:
    events, schedules, triggers = stores
    events.limits = EventStoreLimits(max_subscriptions=1)
    first = _schedule(schedules, {"one": {"with": {"type": "first"}}})
    triggers.ensure_registered(first)
    second = _schedule(schedules, {"one": {"with": {"type": "second"}}})
    with pytest.raises(EventValidationError):
        triggers.ensure_registered(second)


def test_vacuous_cycle_capacity_fails_without_losing_prior_binding(stores: Any) -> None:
    events, schedules, triggers = stores
    events.limits = EventStoreLimits(max_retained_events=1)
    record = _schedule(schedules, {"all": []})
    first = triggers.poll(record.schedule_id)
    assert first is not None
    with pytest.raises(EventTriggerBackpressure):
        triggers.poll(record.schedule_id)
    triggers.verify_invocation(first.invocation_id)
    assert schedules.invocations.get(first.invocation_id) is not None
    assert triggers.state(record.schedule_id).identity.cycle_ordinal == 2
