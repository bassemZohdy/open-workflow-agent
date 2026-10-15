"""Durable event bus polling and registration behavior."""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from open_workflow_agent.durable_events import DEFAULT_EVENT_SCOPE, DurableEventBus
from open_workflow_agent.effects import EffectIdentity, EffectJournal
from open_workflow_agent.event_store import EventStore
from open_workflow_agent.events import EventDecision


def _bus(tmp_path: Path) -> tuple[DurableEventBus, EffectJournal]:
    journal = EffectJournal(str(tmp_path / "durable-events.sqlite3"))
    return DurableEventBus(EventStore(journal), poll_interval_seconds=0), journal


def _identity(operation: str = "listen") -> EffectIdentity:
    reference = "/do/0/listen"
    return EffectIdentity(
        str(uuid4()),
        "durable-event-bus-test",
        reference,
        ((reference, 1),),
        operation,
    )


@pytest.mark.asyncio
async def test_direct_receive_reads_only_default_scope_and_uses_captured_strategy(
    tmp_path: Path,
) -> None:
    bus, journal = _bus(tmp_path)
    strategy = {"one": {"with": {"type": "fixture.ready"}}}
    receiving = bus.receive(strategy)
    strategy["one"] = {"with": {"type": "fixture.changed"}}
    try:
        await bus.publish_scoped(
            {"id": "private", "type": "fixture.ready"},
            default_source="urn:fixture",
            scope="tenant-a",
        )
        await bus.publish_scoped(
            {"id": "ignored", "type": "fixture.other"},
            default_source="urn:fixture",
            scope=DEFAULT_EVENT_SCOPE,
        )
        expected = await bus.publish(
            {"id": "public", "type": "fixture.ready"}, default_source="urn:fixture"
        )

        assert await receiving == expected
    finally:
        journal.close()


@pytest.mark.asyncio
async def test_registered_receive_returns_selected_receipt_and_forwards_lifecycle(
    tmp_path: Path,
) -> None:
    bus, journal = _bus(tmp_path)
    identity = _identity()
    strategy = {"one": {"with": {"type": "fixture.ready"}}}
    try:
        registration = bus.register_workflow_listener(
            identity, strategy, scope="tenant-a", execution_lease=None
        )
        await bus.publish_scoped(
            {"id": "ready", "type": "fixture.ready", "data": {"ok": True}},
            default_source="urn:fixture",
            scope="tenant-a",
        )

        receipt = await bus.receive_registered(
            registration,
            execution_lease=None,
            evaluate_candidate=lambda _event, _progress: EventDecision(terminal=True),
        )

        progress = bus.listener_state(registration)
        assert receipt == progress.receipt
        assert receipt is not None and receipt.output[0]["id"] == "ready"
        assert [event.id for event in progress.events] == ["ready"]
        assert bus.complete_workflow_listener(registration, execution_lease=None) == progress
        bus.verify_invocation(identity.invocation_id, identity.workflow_fingerprint)
    finally:
        journal.close()


@pytest.mark.asyncio
async def test_registered_receive_raises_cancellation_after_listener_is_abandoned(
    tmp_path: Path,
) -> None:
    bus, journal = _bus(tmp_path)
    identity = _identity()
    try:
        registration = bus.register_workflow_listener(
            identity,
            {"one": {"with": {"type": "fixture.never"}}},
            scope="tenant-a",
            execution_lease=None,
        )
        bus.abandon_workflow_listener(registration, execution_lease=None)

        with pytest.raises(asyncio.CancelledError):
            await bus.receive_registered(
                registration,
                execution_lease=None,
                evaluate_candidate=lambda _event, _progress: None,
            )

        assert bus.listener_state(registration).closed
    finally:
        journal.close()
