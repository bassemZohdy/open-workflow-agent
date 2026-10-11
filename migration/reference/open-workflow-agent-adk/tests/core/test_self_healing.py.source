from openworkflow_adk import InMemoryRunHistory, load, run_workflow


async def test_self_healer_patches_state_and_retries_flaky_call() -> None:
    attempts = 0

    def flaky(value: int) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary failure")
        return str(value)

    async def diagnose(error: Exception, state: dict):
        assert error
        return {"retry": True, "state": {"value": 7}}

    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "heal",
                "version": "1.0.0",
            },
            "do": [
                {"prepare": {"set": {"value": ".value"}}},
                {
                    "guard": {
                        "try": [{"call_flaky": {"call": "flaky", "with": {"value": ".value"}}}],
                        "catch": {"do": []},
                        "metadata": {"adk": {"self_heal": {"max_attempts": 2}}},
                    }
                },
            ],
        }
    )

    history = InMemoryRunHistory()
    await run_workflow(
        document,
        {"value": 1},
        function_registry={"flaky": flaky},
        self_healer=diagnose,
        history=history,
        session_id="heal-run",
    )

    assert attempts == 2
    assert history.get("heal-run").status == "completed"
    assert history.get("heal-run").state["value"] == 7
