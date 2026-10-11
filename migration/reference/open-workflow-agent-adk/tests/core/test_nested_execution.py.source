from openworkflow_adk import load, run_workflow


async def test_try_catch_runs_catch_tasks() -> None:
    document = load(
        {
            "document": {"dsl": "1.0.3", "namespace": "demo", "name": "try", "version": "1.0.0"},
            "do": [
                {
                    "guarded": {
                        "try": [
                            {
                                "fail": {
                                    "raise": {
                                        "error": {
                                            "type": "https://demo.test/errors/failure",
                                            "status": 500,
                                            "title": "Failure",
                                        }
                                    }
                                }
                            }
                        ],
                        "catch": {
                            "as": "failure",
                            "do": [{"recover": {"set": {"handled": '"yes"'}}}],
                        },
                    }
                }
            ],
        }
    )

    events = await run_workflow(document)

    assert any(
        event.actions and event.actions.state_delta.get("handled") == "yes" for event in events
    )


async def test_for_dispatches_nested_task_for_each_item() -> None:
    document = load(
        {
            "document": {"dsl": "1.0.3", "namespace": "demo", "name": "loop", "version": "1.0.0"},
            "do": [
                {
                    "eachItem": {
                        "for": {"each": "item", "in": ".items", "at": "index"},
                        "do": [{"save": {"set": {"last": "${ .item }"}}}],
                    }
                }
            ],
        }
    )

    events = await run_workflow(document, {"items": [1, 2, 3]})

    assert (
        sum(
            1
            for event in events
            if event.actions and event.actions.state_delta.get("last") is not None
        )
        == 3
    )
