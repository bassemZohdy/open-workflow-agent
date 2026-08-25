from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow
from open_workflow_agent_adk import AdkWorkflowEngine
from open_workflow_agent_langgraph import LangGraphWorkflowEngine

FEATURE_ROOT = Path(__file__).parent / "features"


def _load_scenarios() -> list[dict[str, Any]]:
    scenarios: list[dict[str, Any]] = []
    for path in sorted(FEATURE_ROOT.glob("*.feature")):
        lines = path.read_text(encoding="utf-8").splitlines()
        name = ""
        blocks: dict[str, Any] = {}
        current: str | None = None
        buffer: list[str] = []
        preceding = ""
        for line in lines:
            if line.startswith("  Scenario: "):
                if name and blocks.get("workflow") is not None:
                    scenarios.append({"name": name, **blocks})
                name = line.removeprefix("  Scenario: ")
                blocks = {}
                current = None
                buffer = []
            elif line.startswith('    """yaml'):
                if "workflow with definition" in preceding:
                    current = "workflow"
                elif "workflow input" in preceding:
                    current = "input"
                else:
                    current = "output"
                buffer = []
            elif line.startswith('    """') and current:
                blocks[current] = yaml.safe_load("\n".join(buffer))
                current = None
            elif current:
                buffer.append(line[4:] if line.startswith("    ") else line)
            else:
                match = re.match(r"    And (\S+) should run (first|last)", line)
                if match:
                    blocks.setdefault(match.group(2), []).append(match.group(1))
            preceding = line
        if name and blocks.get("workflow") is not None:
            scenarios.append({"name": name, **blocks})
    return scenarios


SCENARIOS = _load_scenarios()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_type", [AdkWorkflowEngine, LangGraphWorkflowEngine])
@pytest.mark.parametrize("scenario", [pytest.param(item, id=item["name"]) for item in SCENARIOS])
async def test_upstream_ctk_portable_profile_scenarios(engine_type, scenario, tmp_path):
    """Execute the selected upstream Gherkin scenarios through both adapters."""

    config = RuntimeConfig.model_validate({"model": {"provider": "fake"}})
    services = RuntimeServices(config, model=FakeModel(), database_root=tmp_path)
    engine = engine_type()
    await engine.initialize(services)
    workflow = scenario["workflow"]
    plan = compile_workflow(workflow)
    handle = services.invocations.create(
        engine=engine.engine_name,
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    result = await engine.invoke(plan, handle, scenario.get("input", {}))
    assert result.status == "completed"
    assert result.output == scenario["output"]
    task_names = [
        event.task_name for event in services.events.events if event.event_type == "TaskStarted"
    ]
    for expected in scenario.get("first", []):
        assert task_names[0] == expected
    for expected in scenario.get("last", []):
        assert task_names[-1] == expected
    services.close()
