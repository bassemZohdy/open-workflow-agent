from openworkflow_adk import InMemoryRunHistory, WorkflowManager, WorkflowRegistry, load


def _document():
    return load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "managed",
                "version": "1.0.0",
                "summary": "Managed workflow",
            },
            "do": [{"done": {"set": {"ok": '"yes"'}}}],
        }
    )


async def test_management_lists_runs_plans_and_inspects() -> None:
    history = InMemoryRunHistory()
    manager = WorkflowManager(WorkflowRegistry([_document()]), history=history)

    assert manager.list_workflows()[0]["name"] == "managed"
    assert manager.plan("demo", "managed")["workflow"] == "managed"
    await manager.run("demo", "managed", run_id="managed-1")

    assert manager.inspect_run("managed-1").status == "completed"


async def test_management_streams_live_run_events() -> None:
    manager = WorkflowManager(WorkflowRegistry([_document()]), history=InMemoryRunHistory())
    events = [event async for event in manager.stream_run("demo", "managed", run_id="stream-1")]

    assert events
    assert manager.inspect_run("stream-1").status == "completed"
