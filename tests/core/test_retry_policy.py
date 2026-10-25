from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any

import pytest
from open_workflow_agent.errors import WorkflowExecutionError, WorkflowSemanticError
from open_workflow_agent.retry import RetryPolicy
from open_workflow_agent.workflow import WorkflowExecutor, _duration_seconds, compile_workflow


@pytest.mark.parametrize(
    "policy",
    [
        {"limit": {"attempt": {"count": -1}}},
        {"limit": {"attempt": {"count": True}}},
        {"limit": {"duration": -1}},
        {"limit": {"duration": float("inf")}},
        {"limit": {"attempt": {"duration": float("nan")}}},
        {"jitter": {"from": {"seconds": 2}, "to": {"seconds": 1}}},
    ],
)
def test_invalid_retry_budgets_and_jitter_are_rejected(policy: Any) -> None:
    with pytest.raises(WorkflowSemanticError):
        RetryPolicy.from_value(policy, duration=_duration_seconds)


def test_reversed_jitter_is_rejected_at_admission() -> None:
    workflow = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "test",
            "name": "invalid-jitter",
            "version": "1.0.0",
        },
        "do": [
            {
                "attempt": {
                    "try": [{"result": {"set": {"value": True}}}],
                    "catch": {"retry": {"jitter": {"from": {"seconds": 2}, "to": {"seconds": 1}}}},
                }
            }
        ],
    }
    with pytest.raises(WorkflowSemanticError, match="jitter lower bound"):
        compile_workflow(workflow)


def test_policy_is_immutable_and_enforces_count_and_draw_bounds() -> None:
    policy = RetryPolicy.from_value(
        {"limit": {"attempt": {"count": 2}}, "jitter": {"from": 1, "to": 2}},
        duration=_duration_seconds,
    )
    assert policy.allows_retry(0) and policy.allows_retry(1)
    assert not policy.allows_retry(2)
    with pytest.raises(FrozenInstanceError):
        policy.max_retries = 3  # type: ignore[misc]
    with pytest.raises(WorkflowExecutionError, match="declared bounds"):
        policy.delay_for(0, lambda _lower, _upper: 3)
    with pytest.raises(WorkflowExecutionError, match="supported duration"):
        RetryPolicy(max_retries=None, delay=1, backoff="exponential").delay_for(
            10000, lambda _lower, _upper: 0
        )


@pytest.mark.asyncio
async def test_foreign_timeout_is_not_attributed_to_the_runtime_deadline(services: Any) -> None:
    executor = WorkflowExecutor(services.catalog, services=services)

    async def operation() -> Any:
        raise TimeoutError("provider timeout")

    with pytest.raises(TimeoutError, match="provider timeout"):
        await executor._run_timed(operation, 10, "runtime deadline", {})


@pytest.mark.asyncio
async def test_elapsed_synchronous_work_cannot_escape_timeout_accounting(services: Any) -> None:
    executor = WorkflowExecutor(services.catalog, services=services)
    now = 100.0
    executor._policy_clock = lambda: now

    async def operation() -> Any:
        nonlocal now
        now += 2
        return {"completed": True}

    with pytest.raises(WorkflowExecutionError, match="runtime deadline"):
        await executor._run_timed(operation, 1, "runtime deadline", {})
