from openworkflow_adk import load, run_scheduled


async def test_after_schedule_runs_once() -> None:
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "scheduled",
                "version": "1.0.0",
            },
            "schedule": {"after": "PT0S"},
            "do": [{"done": {"set": {"ok": "true"}}}],
        }
    )

    assert len(await run_scheduled(document, max_runs=1)) == 1
