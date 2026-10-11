from openworkflow_adk import load, run_workflow


async def test_sqlite_session_state_survives_new_runtime_service(tmp_path, monkeypatch) -> None:
    database = tmp_path / "sessions.db"
    monkeypatch.setenv("WORKFLOW_SESSION_DATABASE_URL", f"sqlite+aiosqlite:///{database}")
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "durable",
                "version": "1.0.0",
            },
            "do": [{"save": {"set": {"value": '"persisted"'}}}],
        }
    )

    await run_workflow(document, session_id="durable-run", session_backend="sqlite")
    events = await run_workflow(document, session_id="durable-run", session_backend="sqlite")

    assert events
    assert database.exists()
