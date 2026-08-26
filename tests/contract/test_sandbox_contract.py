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
        sandbox_events = [
            event
            for event in services.events.events
            if event.event_type.startswith("SandboxExecution")
        ]
        assert [event.event_type for event in sandbox_events] == [
            "SandboxExecutionStarted",
            "SandboxExecutionCompleted",
        ]
        assert sandbox_events[0].execution_id
        assert sandbox_events[0].execution_id == sandbox_events[1].execution_id
        assert sandbox_events[0].invocation_id == handle.invocation_id
        assert sandbox_events[1].duration is not None
        assert sandbox_events[1].duration >= 0
        assert sandbox_events[1].status == "completed"
        cloud_events = [
            event.data
            for event in services.lifecycle_events.events
            if event.data.get("event_type", "").startswith("SandboxExecution")
        ]
        assert cloud_events[-1]["execution_id"] == sandbox_events[1].execution_id
        assert cloud_events[-1]["status"] == "completed"
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("engine_name", "engine_type"),
    [("adk", AdkWorkflowEngine), ("langgraph", LangGraphWorkflowEngine)],
)
async def test_sandbox_retry_reexecutes_with_new_identity_and_at_least_once_side_effects(
    tmp_path: Path,
    engine_name: str,
    engine_type: type[AdkWorkflowEngine] | type[LangGraphWorkflowEngine],
) -> None:
    marker = tmp_path / engine_name / "retry-side-effect.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    workflow = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "contract",
            "name": "sandbox-retry",
            "version": "1.0.0",
        },
        "do": [
            {
                "retry": {
                    "try": [
                        {
                            "execute": {
                                "run": {
                                    "script": {
                                        "language": "python",
                                        "arguments": [str(marker)],
                                        "code": (
                                            "from pathlib import Path\n"
                                            "import sys\n"
                                            "marker = Path(sys.argv[1])\n"
                                            "if not marker.exists():\n"
                                            "    marker.write_text('first-attempt')\n"
                                            "    raise SystemExit(7)\n"
                                            "print('retry-ok')\n"
                                        ),
                                    }
                                }
                            }
                        }
                    ],
                    "catch": {"retry": {"limit": {"attempt": {"count": 1}}}},
                }
            }
        ],
    }
    sandbox = SandboxConfig(
        enabled=True,
        workspace_root=str(tmp_path / engine_name / "sandbox"),
        memory_bytes=None,
    )
    services = RuntimeServices(
        RuntimeConfig(sandbox=sandbox),
        model=FakeModel({"response": "ok"}),
        database_root=tmp_path / engine_name / "data",
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
        assert result.output == {"exitCode": 0, "stdout": "retry-ok\n", "stderr": ""}
        assert marker.read_text() == "first-attempt"

        sandbox_events = [
            event
            for event in services.events.events
            if event.event_type.startswith("SandboxExecution")
        ]
        assert [event.event_type for event in sandbox_events] == [
            "SandboxExecutionStarted",
            "SandboxExecutionFailed",
            "SandboxExecutionStarted",
            "SandboxExecutionCompleted",
        ]
        first_execution = sandbox_events[0].execution_id
        second_execution = sandbox_events[2].execution_id
        assert first_execution
        assert second_execution
        assert first_execution != second_execution
        assert sandbox_events[1].error == {"code": "sandbox_process_error"}
        assert any(event.event_type == "TaskRetried" for event in services.events.events)
    finally:
        await engine.shutdown()
        services.close()
