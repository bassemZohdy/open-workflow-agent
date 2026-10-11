from openworkflow_adk import load, run_workflow
from openworkflow_adk.resources.broker import InMemoryBroker


async def test_listen_read_raw_returns_cloud_event_data_before_data_projection() -> None:
    broker = InMemoryBroker()
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "raw-listen",
                "version": "1.0.0",
            },
            "do": [
                {
                    "readRaw": {
                        "listen": {
                            "to": {"one": {"with": {"type": "demo.ready"}}},
                            "read": "raw",
                        }
                    }
                }
            ],
        }
    )
    await broker.publish(
        {
            "specversion": "1.0",
            "id": "event-1",
            "source": "demo",
            "type": "demo.ready",
            "data": {"type": "demo.ready", "data": {"id": 9}},
        }
    )

    events = await run_workflow(document, broker=broker)

    assert any(event.output == {"type": "demo.ready", "data": {"id": 9}} for event in events)
