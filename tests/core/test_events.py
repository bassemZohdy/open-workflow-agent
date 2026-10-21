from __future__ import annotations

import asyncio
from typing import Any

import pytest
from open_workflow_agent.errors import WorkflowSchemaError, WorkflowSemanticError
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


@pytest.mark.parametrize(
    "strategy",
    [
        {"one": {"with": {"type": "one"}}},
        {"all": [{"with": {"type": "one"}}, {"with": {"type": "two"}}]},
        {"any": [{"with": {"type": "one"}}]},
        {"any": []},
        {"any": [], "until": "${ .stop == true }"},
        {"any": [], "until": {"one": {"with": {"type": "stop"}}}},
    ],
)
def test_listen_admits_the_supported_durable_consumption_strategies(
    strategy: dict[str, Any],
) -> None:
    workflow = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "tests",
            "name": "durable-listen",
            "version": "1.0.0",
        },
        "do": [
            {
                "events": {
                    "listen": {
                        "to": strategy,
                    }
                }
            }
        ],
    }
    assert compile_workflow(workflow).uses_durable_events


@pytest.mark.parametrize(
    "strategy",
    [
        {"one": {"with": {}}},
        {"one": {"with": {"type": "one"}}, "any": []},
        {"any": [{"with": {"type": "one"}}], "until": 42},
    ],
)
def test_listen_rejects_invalid_strategy_definitions(strategy: dict[str, Any]) -> None:
    workflow = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "tests",
            "name": "invalid-listen",
            "version": "1.0.0",
        },
        "do": [{"events": {"listen": {"to": strategy}}}],
    }
    with pytest.raises((WorkflowSchemaError, WorkflowSemanticError)):
        compile_workflow(workflow)
