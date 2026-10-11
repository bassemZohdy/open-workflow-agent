from openworkflow_adk import SQLiteRunHistory, load, run_workflow


async def test_sqlite_history_records_completed_run(tmp_path) -> None:
    history = SQLiteRunHistory(str(tmp_path / "runs.db"))
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "history",
                "version": "1.0.0",
            },
            "do": [{"save": {"set": {"value": '"ok"'}}}],
        }
    )

    await run_workflow(document, session_id="run-1", history=history)

    record = history.get("run-1")
    assert record.status == "completed"
    assert record.state["value"] == "ok"
    history.close()
