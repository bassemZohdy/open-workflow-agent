from openworkflow_adk import load, run_workflow
from openworkflow_adk.internal import InMemoryBroker


async def test_listen_reads_matching_event_data() -> None:
    broker = InMemoryBroker()
    await broker.publish({"type": "demo.ready", "data": {"id": 9}})
    document = load(
        {
            "document": {"dsl": "1.0.3", "namespace": "demo", "name": "listen", "version": "1.0.0"},
            "do": [
                {
                    "waitForReady": {
                        "listen": {
                            "to": {"one": {"with": {"type": "demo.ready"}}},
                            "read": "data",
                        }
                    }
                }
            ],
        }
    )

    events = await run_workflow(document, broker=broker)

    assert any(event.output == {"id": 9} for event in events)
