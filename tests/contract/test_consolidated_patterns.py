"""Selected predecessor fixtures and independently written portable patterns."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from engine_cases import engine_cases
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/contract/fixtures/migrated-adk"


@pytest.mark.asyncio
@pytest.mark.parametrize(("engine_name", "engine_type"), engine_cases())
@pytest.mark.parametrize(
    ("fixture", "input_data", "status", "output", "agent_calls"),
    [
        ("wait", {"question": "hello"}, "completed", {"question": "hello"}, 0),
        ("raise", {}, "faulted", None, 0),
        ("try", {}, "completed", {"recovered": "yes"}, 0),
        ("switch", {"enabled": True}, "completed", {"result": "enabled"}, 0),
        ("switch", {"enabled": False}, "completed", {"result": "disabled"}, 0),
        ("assistant", {"question": "hello"}, "completed", {"response": "ok"}, 1),
        ("pipeline", {"question": "hello"}, "completed", {"response": "ok"}, 2),
    ],
)
async def test_consolidated_fixture_outcome(
    tmp_path, engine_name, engine_type, fixture, input_data, status, output, agent_calls
):
    path = (
        ROOT / "resources/patterns" / f"{fixture}.yaml"
        if fixture in {"assistant", "pipeline"}
        else FIXTURES / f"{fixture}.yaml"
    )
    plan = compile_workflow(yaml.safe_load(path.read_text()))
    model = FakeModel({"response": "ok"})
    services = RuntimeServices(RuntimeConfig(), model=model, database_root=tmp_path / engine_name)
    engine = engine_type()
    await engine.initialize(services)
    handle = services.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    try:
        result = await engine.invoke(plan, handle, input_data)
        assert result.status == status
        assert result.output == output
        if status == "faulted":
            assert result.error and result.error["code"] == "workflow_execution_error"
        assert len(model.calls) == agent_calls
        if agent_calls:
            assert model.calls[0]["input"] == "hello"
        if fixture == "pipeline":
            assert model.calls[1]["input"] == {"response": "ok"}
        references = [
            event.task_reference
            for event in services.events.events
            if event.event_type == "TaskStarted"
        ]
        assert references and all(references)
        if fixture == "pipeline":
            assert references == ["/do/0/draft", "/do/1/refine"]
    finally:
        services.close()
