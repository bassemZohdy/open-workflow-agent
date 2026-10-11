"""Listen consume policies: `until`, `foreach`, and `correlate`."""

from openworkflow_adk import load, run_workflow
from openworkflow_adk.resources.broker import InMemoryBroker


def _document(tasks: list[dict]):
    return load(
        {
            "document": {"dsl": "1.0.3", "namespace": "demo", "name": "listen", "version": "1.0.0"},
            "do": tasks,
        }
    )


def _state_deltas(events: list) -> dict:
    deltas: dict = {}
    for event in events:
        if event.actions and event.actions.state_delta:
            deltas.update(event.actions.state_delta)
    return deltas


async def test_listen_until_stops_on_condition() -> None:
    broker = InMemoryBroker()
    document = _document(
        [
            {
                "collect": {
                    "listen": {
                        "to": {"any": [], "until": "${ ( . | length ) > 2 }"},
                    }
                }
            }
        ]
    )

    # Seed the broker before running; the listener drains three events.
    for index in range(5):
        await broker.publish({"type": "demo.event", "data": {"n": index}})

    events = await run_workflow(document, broker=broker)

    outputs = [event.output for event in events if event.output is not None]
    collected = next(value for value in outputs if isinstance(value, list))
    assert len(collected) == 2


async def test_listen_foreach_runs_tasks_per_event() -> None:
    broker = InMemoryBroker()
    document = _document(
        [
            {
                "watch": {
                    "listen": {
                        "to": {"any": [], "until": "${ ( . | length ) > 1 }"},
                    },
                    "foreach": {
                        "item": "event",
                        "at": "i",
                        "do": [{"save": {"set": {"last_n": "${ .event.n }", "last_i": "${ .i }"}}}],
                    },
                }
            }
        ]
    )

    for index in range(2):
        await broker.publish({"type": "demo.event", "data": {"n": index}})

    events = await run_workflow(document, broker=broker)

    deltas = _state_deltas(events)
    assert deltas.get("last_n") == 0
    assert deltas.get("last_i") == 0


async def test_listen_correlate_first_value_defines_match() -> None:
    broker = InMemoryBroker()
    document = _document(
        [
            {
                "watch": {
                    "listen": {
                        "to": {
                            "one": {
                                "with": {"type": "demo.order"},
                                "correlate": {"customer": {"from": "${ .data.customer }"}},
                            }
                        }
                    }
                }
            }
        ]
    )

    await broker.publish({"type": "demo.order", "data": {"customer": "alice", "n": 1}})
    await broker.publish({"type": "demo.order", "data": {"customer": "bob", "n": 2}})
    await broker.publish({"type": "demo.order", "data": {"customer": "alice", "n": 3}})

    events = await run_workflow(document, broker=broker)

    outputs = [event.output for event in events if event.output is not None]
    assert {"customer": "alice", "n": 1} in outputs
    assert {"customer": "bob", "n": 2} not in outputs


async def test_listen_correlate_expect_expression() -> None:
    broker = InMemoryBroker()
    document = _document(
        [
            {"seed": {"set": {"wanted": '"carol"'}}},
            {
                "watch": {
                    "listen": {
                        "to": {
                            "one": {
                                "with": {"type": "demo.order"},
                                "correlate": {
                                    "customer": {
                                        "from": "${ .data.customer }",
                                        "expect": "${ $context.wanted }",
                                    }
                                },
                            }
                        }
                    }
                }
            },
        ]
    )

    await broker.publish({"type": "demo.order", "data": {"customer": "bob", "n": 1}})
    await broker.publish({"type": "demo.order", "data": {"customer": "carol", "n": 2}})

    events = await run_workflow(document, broker=broker)

    outputs = [event.output for event in events if event.output is not None]
    assert {"customer": "carol", "n": 2} in outputs


async def test_listen_nested_until_consumes_terminator_without_returning_it() -> None:
    broker = InMemoryBroker()
    document = _document(
        [
            {
                "collect": {
                    "listen": {
                        "to": {
                            "any": [{"with": {"type": "demo.item"}}],
                            "until": {"one": {"with": {"type": "demo.stop"}}},
                        }
                    }
                }
            }
        ]
    )

    await broker.publish({"type": "demo.item", "data": {"n": 1}})
    await broker.publish({"type": "demo.stop", "data": {"n": 2}})
    await broker.publish({"type": "demo.item", "data": {"n": 3}})

    events = await run_workflow(document, broker=broker)

    outputs = [event.output for event in events if event.output is not None]
    assert [{"n": 1}] in outputs
    assert {"n": 2} not in outputs


async def test_listen_nested_until_all_filters() -> None:
    broker = InMemoryBroker()
    document = _document(
        [
            {
                "collect": {
                    "listen": {
                        "to": {
                            "any": [{"with": {"type": "demo.item"}}],
                            "until": {
                                "all": [
                                    {"with": {"type": "demo.stop.first"}},
                                    {"with": {"type": "demo.stop.second"}},
                                ]
                            },
                        }
                    }
                }
            }
        ]
    )

    await broker.publish({"type": "demo.item", "data": {"n": 1}})
    await broker.publish({"type": "demo.stop.first", "data": {}})
    await broker.publish({"type": "demo.stop.second", "data": {}})

    events = await run_workflow(document, broker=broker)

    outputs = [event.output for event in events if event.output is not None]
    assert [{"n": 1}] in outputs
