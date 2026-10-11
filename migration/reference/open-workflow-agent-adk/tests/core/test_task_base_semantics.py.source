"""Spec taskBase semantics: `if`, `input`, `output`, `export`, `timeout`."""

import pytest

from openworkflow_adk import load, run_workflow
from openworkflow_adk.models import OpenWorkflowDocument


def _document(tasks: list[dict], **extra: object) -> OpenWorkflowDocument:
    base: dict = {
        "document": {"dsl": "1.0.3", "namespace": "demo", "name": "base", "version": "1.0.0"},
        "do": tasks,
    }
    base.update(extra)
    return load(base)


def _state_deltas(events: list) -> dict:
    deltas: dict = {}
    for event in events:
        if event.actions and event.actions.state_delta:
            deltas.update(event.actions.state_delta)
    return deltas


async def test_if_false_skips_task() -> None:
    document = _document(
        [
            {"maybe": {"if": "${ .run_flag == true }", "set": {"ran": '"yes"'}}},
            {"always": {"set": {"finished": '"done"'}}},
        ]
    )

    events = await run_workflow(document, {"run_flag": False})

    deltas = _state_deltas(events)
    assert "ran" not in deltas
    assert deltas.get("finished") == "done"


async def test_if_true_runs_task() -> None:
    document = _document(
        [
            {"maybe": {"if": "${ .run_flag == true }", "set": {"ran": '"yes"'}}},
        ]
    )

    events = await run_workflow(document, {"run_flag": True})

    assert _state_deltas(events).get("ran") == "yes"


async def test_output_as_transforms_task_output() -> None:
    document = _document(
        [
            {
                "shaped": {
                    "set": {"raw": '"value"'},
                    "output": {"as": "${ { 'wrapped': raw } }"},
                }
            },
        ]
    )

    events = await run_workflow(document)

    assert any(event.output == {"wrapped": "value"} for event in events)


async def test_export_as_updates_workflow_context() -> None:
    document = _document(
        [
            {
                "exporter": {
                    "set": {"local": '"inner"'},
                    "export": {"as": "${ { 'shared': local } }"},
                }
            },
            {"reader": {"set": {"seen": "${ .shared }"}}},
        ]
    )

    events = await run_workflow(document)

    deltas = _state_deltas(events)
    assert deltas.get("shared") == "inner"
    assert deltas.get("seen") == "inner"


async def test_input_from_filters_task_input() -> None:
    document = _document(
        [
            {
                "filtered": {
                    "input": {"from": "${ .payload }"},
                    "set": {"copied": '"kept"'},
                }
            },
        ]
    )

    events = await run_workflow(document, {"payload": {"copied": "kept"}, "other": "dropped"})

    assert _state_deltas(events).get("copied") == "kept"


async def test_task_timeout_fails_slow_task() -> None:
    document = _document(
        [
            {"slow": {"wait": {"seconds": 5}, "timeout": {"after": {"milliseconds": 50}}}},
        ]
    )

    with pytest.raises(TimeoutError):
        await run_workflow(document)


async def test_task_timeout_reference_from_use_timeouts() -> None:
    document = _document(
        [
            {"slow": {"wait": {"seconds": 5}, "timeout": "short"}},
        ],
        use={"timeouts": {"short": {"after": {"milliseconds": 50}}}},
    )

    with pytest.raises(TimeoutError):
        await run_workflow(document)


async def test_timeout_allows_fast_task() -> None:
    document = _document(
        [
            {"quick": {"wait": {"seconds": 0}, "timeout": {"after": {"seconds": 30}}}},
            {"done": {"set": {"finished": '"yes"'}}},
        ]
    )

    events = await run_workflow(document)

    assert _state_deltas(events).get("finished") == "yes"
