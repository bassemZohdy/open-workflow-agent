"""`use.extensions` before/after task injection."""

from openworkflow_adk import load, run_workflow


def _document(tasks: list[dict], extensions: list[dict]):
    return load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "extensions",
                "version": "1.0.0",
            },
            "use": {"extensions": extensions},
            "do": tasks,
        }
    )


def _state_deltas(events: list) -> dict:
    deltas: dict = {}
    for event in events:
        if event.actions and event.actions.state_delta:
            deltas.update(event.actions.state_delta)
    return deltas


async def test_extension_wraps_matching_task_kind() -> None:
    document = _document(
        [
            {"produce": {"set": {"value": '"target"'}}},
            {"pause": {"wait": {"seconds": 0}}},
        ],
        extensions=[
            {
                "instrumentSet": {
                    "extend": "set",
                    "before": [{"logBefore": {"set": {"before_ran": '"yes"'}}}],
                    "after": [{"logAfter": {"set": {"after_ran": '"yes"'}}}],
                }
            }
        ],
    )

    events = await run_workflow(document)

    deltas = _state_deltas(events)
    assert deltas.get("before_ran") == "yes"
    assert deltas.get("after_ran") == "yes"
    assert deltas.get("value") == "target"


async def test_extension_skips_non_matching_kind() -> None:
    document = _document(
        [{"pause": {"wait": {"seconds": 0}}}],
        extensions=[
            {
                "instrumentSet": {
                    "extend": "set",
                    "after": [{"logAfter": {"set": {"after_ran": '"yes"'}}}],
                }
            }
        ],
    )

    events = await run_workflow(document)

    assert "after_ran" not in _state_deltas(events)


async def test_extension_all_matches_every_kind() -> None:
    document = _document(
        [{"produce": {"set": {"value": '"x"'}}}],
        extensions=[
            {
                "auditAll": {
                    "extend": "all",
                    "after": [{"count": {"set": {"audited": '"yes"'}}}],
                }
            }
        ],
    )

    events = await run_workflow(document)

    assert _state_deltas(events).get("audited") == "yes"


async def test_extension_when_condition_gates_injection() -> None:
    document = _document(
        [{"produce": {"set": {"value": '"x"'}}}],
        extensions=[
            {
                "conditional": {
                    "extend": "set",
                    "when": "${ .enabled == true }",
                    "after": [{"count": {"set": {"injected": '"yes"'}}}],
                }
            }
        ],
    )

    events_off = await run_workflow(document, {"enabled": False})
    assert "injected" not in _state_deltas(events_off)

    events_on = await run_workflow(document, {"enabled": True})
    assert _state_deltas(events_on).get("injected") == "yes"
