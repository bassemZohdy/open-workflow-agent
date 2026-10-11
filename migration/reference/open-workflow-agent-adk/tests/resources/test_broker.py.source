from openworkflow_adk import load, run_workflow
from openworkflow_adk.internal import InMemoryBroker


async def test_emit_publishes_expression_bound_event() -> None:
    broker = InMemoryBroker()
    document = load(
        {
            "document": {"dsl": "1.0.3", "namespace": "demo", "name": "event", "version": "1.0.0"},
            "do": [
                {
                    "notify": {
                        "emit": {
                            "event": {
                                "with": {
                                    "source": "https://demo.test",
                                    "type": "demo.ready",
                                    "data": {"id": "${ .id }"},
                                }
                            }
                        }
                    }
                }
            ],
        }
    )

    await run_workflow(document, {"id": 7}, broker=broker)

    assert broker.events == [
        {"source": "https://demo.test", "type": "demo.ready", "data": {"id": 7}}
    ]
