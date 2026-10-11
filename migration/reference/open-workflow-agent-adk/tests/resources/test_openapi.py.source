import respx
from httpx import Response

from openworkflow_adk import load, run_workflow


@respx.mock
async def test_openapi_operation_lookup_and_path_parameter_binding() -> None:
    respx.get("https://api.test/openapi.json").mock(
        return_value=Response(
            200,
            json={
                "openapi": "3.0.0",
                "servers": [{"url": "https://api.test"}],
                "paths": {
                    "/pets/{petId}": {
                        "get": {
                            "operationId": "getPet",
                            "parameters": [{"name": "petId", "in": "path", "required": True}],
                        }
                    }
                },
            },
        )
    )
    route = respx.get("https://api.test/pets/7").mock(return_value=Response(200, json={"id": 7}))
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "openapi",
                "version": "1.0.0",
            },
            "do": [
                {
                    "get": {
                        "call": "openapi",
                        "with": {
                            "document": {"endpoint": "https://api.test/openapi.json"},
                            "operationId": "getPet",
                            "parameters": {"petId": 7},
                        },
                    }
                }
            ],
        }
    )

    events = await run_workflow(document)

    assert route.called
    assert any(event.output == {"id": 7} for event in events)
