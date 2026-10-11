import respx
from httpx import Response

from openworkflow_adk import load, run_workflow


@respx.mock
async def test_named_bearer_auth_is_applied_to_http_call(monkeypatch) -> None:
    # respx intercepts the transport, so DNS never runs; allow unresolvable
    # test hosts through the egress guard.
    monkeypatch.setenv("WORKFLOW_EGRESS_SKIP_DNS", "1")
    route = respx.get("https://example.test/private").mock(return_value=Response(200, text="ok"))
    document = load(
        {
            "document": {"dsl": "1.0.3", "namespace": "demo", "name": "auth", "version": "1.0.0"},
            "use": {"authentications": {"service": {"bearer": {"token": "secret-token"}}}},
            "do": [
                {
                    "private": {
                        "call": "http",
                        "with": {
                            "method": "get",
                            "endpoint": {
                                "uri": "https://example.test/private",
                                "authentication": {"use": "service"},
                            },
                        },
                    }
                }
            ],
        }
    )

    await run_workflow(document)

    assert route.calls[0].request.headers["authorization"] == "Bearer secret-token"
