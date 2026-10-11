import json

from openworkflow_adk import InMemoryRunHistory, generate_workflow, run_workflow


async def test_prompt_generates_valid_runnable_workflow() -> None:
    async def generator(request: str) -> str:
        assert "User request:" in request
        return json.dumps(
            {
                "document": {
                    "dsl": "1.0.3",
                    "namespace": "generated",
                    "name": "hello",
                    "version": "1.0.0",
                },
                "do": [{"finish": {"set": {"message": '"hello"'}}}],
            }
        )

    document = await generate_workflow("Say hello", generator=generator)
    history = InMemoryRunHistory()
    await run_workflow(document, history=history, session_id="generated")

    assert document.document.name == "hello"
    assert history.get("generated").state["message"] == "hello"
