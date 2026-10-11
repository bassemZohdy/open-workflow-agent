"""Replay and determinism utilities for persisted workflow event logs."""

from __future__ import annotations

from typing import Any

from openworkflow_adk.models import OpenWorkflowDocument
from openworkflow_adk.ops.history import InMemoryRunHistory

__all__ = ["replay_event_log", "replay_from_task", "verify_replay_determinism"]


def replay_event_log(
    event_log: list[dict[str, Any]], initial_state: dict[str, Any] | None = None
) -> tuple[dict[str, Any], Any]:
    """Reconstruct final state and output without invoking external handlers."""
    state = dict(initial_state or {})
    output: Any = None
    for event in event_log:
        delta = event.get("state_delta") or {}
        if not isinstance(delta, dict):
            raise ValueError("event log state_delta must be an object")
        state.update(delta)
        if event.get("output") is not None:
            output = event["output"]
        if event.get("error"):
            raise RuntimeError(f"cannot replay failed event: {event['error']}")
    return state, output


async def replay_from_task(
    document: OpenWorkflowDocument,
    task_name: str,
    checkpoint: dict[str, Any] | None = None,
    **kwargs: Any,
) -> list[Any]:
    """Resume a workflow at a named top-level task using checkpointed state."""
    for index, item in enumerate(document.do):
        if item.name == task_name:
            from openworkflow_adk.runtime import run_workflow

            resumed = document.model_copy(update={"do": document.do[index:]})
            return await run_workflow(resumed, checkpoint or {}, **kwargs)
    raise KeyError(f"unknown replay task: {task_name}")


async def verify_replay_determinism(
    document: OpenWorkflowDocument, input: dict[str, Any] | None = None
) -> bool:
    """Compare two deterministic runs through their persisted event logs."""
    from openworkflow_adk.runtime import run_workflow

    first = InMemoryRunHistory()
    second = InMemoryRunHistory()
    await run_workflow(document, input, session_id="replay-1", history=first)
    await run_workflow(document, input, session_id="replay-2", history=second)
    first_record = first.get("replay-1")
    second_record = second.get("replay-2")
    first_replayed = replay_event_log(first_record.event_log, input)
    second_replayed = replay_event_log(second_record.event_log, input)
    return (
        first_record.event_log == second_record.event_log
        and first_replayed == second_replayed
        and first_replayed == (first_record.state, first_record.output)
    )
