from pathlib import Path

import pytest
import respx
from httpx import Response

from openworkflow_adk import WorkflowRegistry, load, run_workflow
from openworkflow_adk.errors import OpenWorkflowError
from openworkflow_adk.internal import InMemoryBroker

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    "fixture_name,input_data",
    [
        ("switch.yaml", {"enabled": True}),
        ("fork.yaml", {}),
        ("try.yaml", {}),
        ("wait.yaml", {}),
        ("for.yaml", {"items": [1, 2, 3]}),
        ("run-script.yaml", {}),
    ],
)
async def test_deterministic_golden_fixtures_run(fixture_name, input_data) -> None:
    events = await run_workflow(load(FIXTURES / fixture_name), input_data)

    assert fixture_name == "wait.yaml" or events


async def test_emit_and_listen_golden_fixtures_run() -> None:
    broker = InMemoryBroker()
    await run_workflow(load(FIXTURES / "emit.yaml"), broker=broker)
    events = await run_workflow(load(FIXTURES / "listen.yaml"), broker=broker)

    assert events


async def test_subflow_golden_fixture_runs_with_registry() -> None:
    child = load(FIXTURES / "subflow-child.yaml")
    parent = load(FIXTURES / "subflow.yaml")
    events = await run_workflow(
        parent,
        {"value": "child output"},
        workflow_registry=WorkflowRegistry([child]),
    )

    assert events


@respx.mock
async def test_http_golden_fixture_runs_through_runtime(monkeypatch) -> None:
    monkeypatch.setenv("WORKFLOW_EGRESS_SKIP_DNS", "1")
    respx.get("https://example.test/echo").mock(return_value=Response(200, json={"ok": True}))

    events = await run_workflow(load(FIXTURES / "http.yaml"))

    assert any(event.output == {"ok": True} for event in events)


async def test_raise_golden_fixture_surfaces_structured_error() -> None:
    with pytest.raises(OpenWorkflowError) as raised:
        await run_workflow(load(FIXTURES / "raise.yaml"))

    assert raised.value.status == 500
