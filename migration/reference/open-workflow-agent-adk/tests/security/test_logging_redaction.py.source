import io

from openworkflow_adk import load, run_workflow
from openworkflow_adk.internal import JsonRunLogger
from openworkflow_adk.security.security import redact


async def test_json_run_logger_records_lifecycle_and_redacts_secret(monkeypatch) -> None:
    monkeypatch.setenv("WORKFLOW_SECRET__token", "super-secret")
    stream = io.StringIO()
    records = []
    logger = JsonRunLogger(stream)
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "logging",
                "version": "1.0.0",
            },
            "use": {"secrets": ["token"]},
            "do": [{"save": {"set": {"value": '"super-secret"'}}}],
        }
    )

    await run_workflow(document, run_logger=records.append)
    logger({"value": redact("super-secret", ["super-secret"])})

    assert records[0]["event"] == "run.started"
    assert records[-1]["event"] == "run.completed"
    task_exit = next(record for record in records if record["event"] == "task.exit")
    assert task_exit["state_delta"]["value"] == "[REDACTED]"
    assert task_exit["duration_ms"] >= 0
    assert "super-secret" not in stream.getvalue()
