from openworkflow_adk import WorkflowRegistry, load, run_workflow


async def test_run_workflow_executes_registered_subflow() -> None:
    child = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "child",
                "version": "1.0.0",
            },
            "do": [{"setChild": {"set": {"child_value": "${ .value }"}}}],
        }
    )
    parent = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "parent",
                "version": "1.0.0",
            },
            "do": [
                {
                    "invoke": {
                        "run": {
                            "workflow": {
                                "namespace": "demo",
                                "name": "child",
                                "version": "1.0.0",
                                "input": {"value": "${ .input_value }"},
                            }
                        }
                    }
                }
            ],
        }
    )

    events = await run_workflow(
        parent,
        {"input_value": "hello"},
        workflow_registry=WorkflowRegistry([child]),
    )

    assert any(
        event.actions and event.actions.state_delta.get("child_value") == "hello"
        for event in events
    )
