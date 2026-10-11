from openworkflow_adk import SQLiteRunHistory, load, run_workflow


async def test_checkpoint_interval_persists_intermediate_state(tmp_path) -> None:
    history = SQLiteRunHistory(str(tmp_path / "checkpoint.db"))
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "checkpoint",
                "version": "1.0.0",
            },
            "do": [
                {"one": {"set": {"one": '"ok"'}}},
                {"two": {"set": {"two": '"ok"'}}},
            ],
        }
    )

    await run_workflow(document, session_id="run-1", history=history, checkpoint_interval=1)

    record = history.get("run-1")
    assert record.checkpoint_index >= 1
    assert record.state["two"] == "ok"
    assert record.checkpoint_task == "two"
    history.close()
