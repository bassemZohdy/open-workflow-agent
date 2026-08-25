from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow
from open_workflow_agent_adk import AdkWorkflowEngine
from open_workflow_agent_langgraph import LangGraphWorkflowEngine


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("engine_name", "engine_type"),
    [("adk", AdkWorkflowEngine), ("langgraph", LangGraphWorkflowEngine)],
)
@pytest.mark.parametrize("fixture_name", ["minimal-agent", "set", "switch", "for", "fork", "retry"])
async def test_portable_fixture_has_same_result_on_each_engine(
    tmp_path, engine_name, engine_type, fixture_name
):
    fixture = Path(__file__).parent / "fixtures" / f"{fixture_name}.yaml"
    workflow = yaml.safe_load(fixture.read_text(encoding="utf-8"))
    services = RuntimeServices(
        RuntimeConfig(), model=FakeModel({"response": "ok"}), database_root=tmp_path / engine_name
    )
    engine = engine_type()
    await engine.initialize(services)
    plan = compile_workflow(workflow)
    handle = services.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    result = await engine.invoke(
        plan, handle, {"question": "hello", "kind": "priority", "items": [1, 2]}
    )
    assert result.status == "completed"
    assert result.output is not None
    services.close()
