import respx
from httpx import Response

from openworkflow_adk import load, run_workflow


@respx.mock
async def test_http_handler_binds_state_and_returns_json() -> None:
    route = respx.post("https://example.test/echo").mock(
        return_value=Response(200, json={"ok": True})
    )
    document = load(
        {
            "document": {"dsl": "1.0.3", "namespace": "demo", "name": "http", "version": "1.0.0"},
            "do": [
                {
                    "send": {
                        "call": "http",
                        "with": {
                            "method": "post",
                            "endpoint": "https://example.test/echo",
                            "body": {"message": "${ .message }"},
                        },
                    }
                }
            ],
        }
    )

    events = await run_workflow(document, {"message": "hello"})

    assert route.called
    assert route.calls[0].request.content == b'{"message":"hello"}'
    assert any(event.output == {"ok": True} for event in events)
