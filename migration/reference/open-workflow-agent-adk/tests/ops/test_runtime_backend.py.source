import pytest

from openworkflow_adk import InMemoryRunHistory, load, run_workflow


def _document():
    return load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "backend",
                "version": "1.0.0",
            },
            "do": [{"done": {"set": {"ok": '"yes"'}}}],
        }
    )


async def test_unknown_session_backend_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported session backend"):
        await run_workflow(_document(), session_backend="unknown")


async def test_negative_token_budget_is_rejected() -> None:
    with pytest.raises(ValueError, match="token_budget"):
        await run_workflow(_document(), token_budget=-1)


async def test_resume_rejects_region_change() -> None:
    history = InMemoryRunHistory()
    await run_workflow(_document(), history=history, session_id="region-run", region="eu-west")

    with pytest.raises(RuntimeError, match="does not match"):
        await run_workflow(
            _document(), history=history, session_id="region-run", region="us-east", resume=True
        )
