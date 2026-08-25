from __future__ import annotations

import pytest
from open_workflow_agent.workflow import WorkflowExecutor, compile_workflow


@pytest.mark.asyncio
async def test_competing_fork_returns_only_the_first_completed_branch(services):
    workflow = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "test",
            "name": "competing-fork",
            "version": "1.0.0",
        },
        "do": [
            {
                "race": {
                    "fork": {
                        "compete": True,
                        "branches": [
                            {"red": {"set": {"colors": ["red"]}}},
                            {"green": {"set": {"colors": ["green"]}}},
                        ],
                    }
                }
            }
        ],
    }

    result = await WorkflowExecutor(services.catalog, services=services).execute(
        compile_workflow(workflow), {}
    )

    assert result["colors"] in (["red"], ["green"])
