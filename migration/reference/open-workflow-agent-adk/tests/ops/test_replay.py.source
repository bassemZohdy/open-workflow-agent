from openworkflow_adk import load, replay_from_task


async def test_replay_from_task_skips_upstream_tasks() -> None:
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "replay",
                "version": "1.0.0",
            },
            "do": [
                {"upstream": {"set": {"value": '"should-not-run"'}}},
                {"resume": {"set": {"value": "$.checkpoint"}}},
            ],
        }
    )

    events = await replay_from_task(document, "resume", {"checkpoint": "replayed"})

    assert [event.author for event in events] == ["replay"]
    assert events[-1].actions.state_delta["value"] == "replayed"
