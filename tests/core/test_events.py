from __future__ import annotations

import asyncio

import pytest
from open_workflow_agent.errors import UnsupportedWorkflowFeature
from open_workflow_agent.events import InMemoryEventBus
from open_workflow_agent.workflow import compile_workflow


@pytest.mark.asyncio
async def test_event_bus_delivers_only_matching_one_strategy() -> None:
    bus = InMemoryEventBus()
    receiving = asyncio.create_task(
        bus.receive({"one": {"with": {"type": "approval.granted", "subject": "case-1"}}})
    )
    await bus.publish(
        {"id": "ignored", "type": "approval.denied", "subject": "case-1", "data": {}},
        default_source="urn:test",
    )
    assert not receiving.done()
    await bus.publish(
        {
            "id": "approved-1",
            "type": "approval.granted",
            "subject": "case-1",
            "data": {"approved": True},
        },
        default_source="urn:test",
    )
    event = await receiving
    assert event.as_dict() == {
        "id": "approved-1",
        "source": "urn:test",
        "type": "approval.granted",
        "time": event.time,
        "subject": "case-1",
        "data": {"approved": True},
    }


@pytest.mark.asyncio
async def test_event_bus_subscription_is_active_when_receive_returns() -> None:
    bus = InMemoryEventBus()
    receiving = bus.receive({"one": {"with": {"type": "case.ready"}}})

    await bus.publish(
        {"id": "ready-1", "type": "case.ready", "data": {"ready": True}},
        default_source="urn:test",
    )

    event = await asyncio.wait_for(receiving, timeout=0.5)
    assert event.id == "ready-1"
    assert event.data == {"ready": True}


def test_listen_rejects_unimplemented_strategies() -> None:
    workflow = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "tests",
            "name": "unsupported-listen",
            "version": "1.0.0",
        },
        "do": [
            {
                "events": {
                    "listen": {
                        "to": {"any": [{"with": {"type": "one"}}]},
                    }
                }
            }
        ],
    }
    with pytest.raises(UnsupportedWorkflowFeature):
        compile_workflow(workflow)
