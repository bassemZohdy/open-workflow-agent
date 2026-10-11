import sys

from openworkflow_adk import ResultMemoization, load, run_workflow


async def test_registered_call_result_is_reused_across_runs() -> None:
    calls = 0

    def expensive(value: int) -> int:
        nonlocal calls
        calls += 1
        return value * 2

    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "memo",
                "version": "1.0.0",
            },
            "do": [{"calculate": {"call": "expensive", "with": {"value": ".value"}}}],
        }
    )
    cache = ResultMemoization()
    kwargs = {"function_registry": {"expensive": expensive}, "memoization": cache}

    await run_workflow(document, {"value": 3}, session_id="memo-1", **kwargs)
    await run_workflow(document, {"value": 3}, session_id="memo-2", **kwargs)

    assert calls == 1


async def test_shell_run_result_is_reused_across_runs() -> None:
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "memo-run",
                "version": "1.0.0",
            },
            "do": [
                {
                    "command": {
                        "run": {
                            "shell": {
                                "command": sys.executable,
                                "arguments": ["-c", "import sys; sys.stdout.write('ok')"],
                            }
                        }
                    }
                }
            ],
        }
    )
    cache = ResultMemoization()
    kwargs = {"memoization": cache}
    first = await run_workflow(document, session_id="run-1", **kwargs)
    second = await run_workflow(document, session_id="run-2", **kwargs)

    assert any(event.output == "ok" for event in first)
    assert any(event.output == "ok" for event in second)
    assert len(cache.values) == 1
