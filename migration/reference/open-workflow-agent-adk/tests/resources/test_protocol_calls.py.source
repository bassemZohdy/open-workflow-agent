import httpx
import respx

from openworkflow_adk import load, run_workflow


@respx.mock
async def test_a2a_call_posts_json_rpc_request() -> None:
    route = respx.post("https://agent.test/rpc").respond(
        json={"jsonrpc": "2.0", "id": "ask", "result": {"taskId": "t1"}}
    )
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "a2a",
                "version": "1.0.0",
            },
            "do": [
                {
                    "ask": {
                        "call": "a2a",
                        "with": {
                            "server": {"uri": "https://agent.test/rpc"},
                            "method": "message/send",
                            "parameters": {"message": "hello"},
                        },
                    }
                }
            ],
        }
    )

    events = await run_workflow(document)

    assert route.called
    assert any(event.output == {"taskId": "t1"} for event in events)


@respx.mock
async def test_mcp_http_call_initializes_then_dispatches_method() -> None:
    responses = iter(
        [
            {"jsonrpc": "2.0", "id": "initialize", "result": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "id": 1, "result": {"tools": []}},
        ]
    )

    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=next(responses))

    route = respx.post("https://mcp.test/rpc").mock(side_effect=respond)
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "mcp",
                "version": "1.0.0",
            },
            "do": [
                {
                    "list": {
                        "call": "mcp",
                        "with": {
                            "method": "tools/list",
                            "transport": {"http": {"endpoint": {"uri": "https://mcp.test/rpc"}}},
                        },
                    }
                }
            ],
        }
    )

    events = await run_workflow(document)

    assert route.call_count == 2
    assert any(event.output == {"tools": []} for event in events)
