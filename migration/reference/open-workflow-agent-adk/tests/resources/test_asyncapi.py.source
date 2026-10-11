import respx

from openworkflow_adk import load, run_workflow
from openworkflow_adk.internal import InMemoryBroker


def _document(task):
    return load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "asyncapi",
                "version": "1.0.0",
            },
            "do": [{"message": task}],
        }
    )


@respx.mock
async def test_asyncapi_call_publishes_to_broker() -> None:
    respx.get("https://spec.test/asyncapi.json").respond(
        json={"asyncapi": "2.6.0", "channels": {"events": {}}}
    )
    document = _document(
        {
            "call": "asyncapi",
            "with": {
                "document": {"endpoint": {"uri": "https://spec.test/asyncapi.json"}},
                "channel": "events",
                "message": {"payload": {"value": "hello"}},
            },
        }
    )
    broker = InMemoryBroker()

    await run_workflow(document, broker=broker)

    assert await broker.consume() == {
        "channel": "events",
        "data": {"value": "hello"},
        "headers": {},
    }
