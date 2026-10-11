from openworkflow_adk import load, run_workflow


async def test_named_function_from_use_functions_runs_as_nested_task() -> None:
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "named-function",
                "version": "1.0.0",
            },
            "use": {"functions": {"makeGreeting": {"set": {"greeting": '"hello"'}}}},
            "do": [{"greet": {"call": "makeGreeting"}}],
        }
    )

    events = await run_workflow(document)

    assert any(
        event.actions and event.actions.state_delta.get("greeting") == "hello" for event in events
    )
