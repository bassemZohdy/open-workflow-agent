from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig, SandboxConfig
from open_workflow_agent.sandbox import compile_sandbox_workflow
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent_adk import AdkWorkflowEngine
from open_workflow_agent_langgraph import LangGraphWorkflowEngine


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("engine_name", "engine_type"),
    [("adk", AdkWorkflowEngine), ("langgraph", LangGraphWorkflowEngine)],
)
@pytest.mark.parametrize(
    ("fixture_name", "expected"),
    [
        (
            "run-script",
            {"exitCode": 0, "stdout": "script-ok\n", "stderr": ""},
        ),
        (
            "run-shell",
            {"exitCode": 0, "stdout": "shell-ok", "stderr": ""},
        ),
    ],
)
async def test_internal_sandbox_run_tasks_have_cross_engine_parity(
    tmp_path: Path,
    engine_name: str,
    engine_type: type[AdkWorkflowEngine] | type[LangGraphWorkflowEngine],
    fixture_name: str,
    expected: dict[str, object],
) -> None:
    fixture = Path(__file__).parent / "fixtures" / f"{fixture_name}.yaml"
    workflow = yaml.safe_load(fixture.read_text(encoding="utf-8"))
    sandbox = SandboxConfig(
        enabled=True,
        allow_shell=True,
        workspace_root=str(tmp_path / engine_name / "sandbox"),
        memory_bytes=None,
    )
    config = RuntimeConfig(sandbox=sandbox)
    services = RuntimeServices(
        config,
        model=FakeModel({"response": "ok"}),
        database_root=tmp_path / engine_name,
    )
    engine = engine_type()
    await engine.initialize(services)
    try:
        plan = compile_sandbox_workflow(workflow, sandbox=sandbox)
        handle = services.invocations.create(
            engine=engine_name,
            session_id=None,
            user_id=None,
            workflow_name=plan.name,
            workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
        )
        result = await engine.invoke(plan, handle, {})
        assert result.status == "completed"
        assert result.output == expected
        phases = [
            event.progress.get("phase")
            for event in services.events.events
            if event.event_type == "TaskProgress" and event.progress
        ]
        assert "sandbox_start" in phases
        assert "sandbox_finished" in phases
        assert all(
            event.task_reference
            for event in services.events.events
            if event.event_type in {"TaskStarted", "TaskCompleted", "TaskProgress"}
        )
    finally:
        await engine.shutdown()
        services.close()
