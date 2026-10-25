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
from sandbox_fixture_policy import supported_process_limits
from test_native_checkpoint_fencing import _snapshot as native_snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize("return_mode", [None, "stdout", "stderr", "code", "all", "none"])
@pytest.mark.parametrize("reusable", [False, True])
@pytest.mark.parametrize(
    ("engine_name", "engine_type"),
    [("adk", AdkWorkflowEngine), ("langgraph", LangGraphWorkflowEngine)],
)
@pytest.mark.parametrize(
    ("fixture_name", "expected"),
    [
        (
            "run-script",
            {"code": 0, "stdout": "script-ok\n", "stderr": ""},
        ),
        (
            "run-shell",
            {"code": 0, "stdout": "shell-ok", "stderr": ""},
        ),
    ],
)
async def test_internal_sandbox_run_tasks_have_cross_engine_parity(
    tmp_path: Path,
    engine_name: str,
    engine_type: type[AdkWorkflowEngine] | type[LangGraphWorkflowEngine],
    fixture_name: str,
    expected: dict[str, object],
    reusable: bool,
    return_mode: str | None,
) -> None:
    fixture = Path(__file__).parent / "fixtures" / f"{fixture_name}.yaml"
    workflow = yaml.safe_load(fixture.read_text(encoding="utf-8"))
    definition = next(iter(workflow["do"][0].values()))
    expected = {**expected, "stderr": "diagnostic"}
    if fixture_name == "run-script":
        definition["run"]["script"]["code"] += "import sys\nsys.stderr.write('diagnostic')\n"
    else:
        definition["run"]["shell"]["arguments"][-1] += (
            "; import sys; sys.stderr.write('diagnostic')"
        )
    if return_mode is not None:
        definition["run"]["return"] = return_mode
    if reusable:
        definition = next(iter(workflow["do"][0].values()))
        workflow["use"] = {"functions": {"execute": definition}}
        workflow["do"] = [{"runReusable": {"call": "execute"}}]
    sandbox = SandboxConfig(
        **supported_process_limits(),
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
        if not (
            engine.native.available if engine_name == "adk" else engine.native.task_nodes_available
        ):
            pytest.skip("actual engine SDK is unavailable in this independent environment")
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
        projection = (
            expected
            if return_mode == "all"
            else None
            if return_mode == "none"
            else expected[return_mode or "stdout"]
        )
        assert result.output == projection
        assert type(result.output) is type(projection)
        assert services.invocations.connection.kind == "sqlite"
        assert any(native_snapshot(services, handle, "sqlite").values())
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
@pytest.mark.parametrize("return_mode", ["code", "all", "none"])
@pytest.mark.parametrize("reusable", [False, True])
@pytest.mark.parametrize(
    ("engine_name", "engine_type"),
    [("adk", AdkWorkflowEngine), ("langgraph", LangGraphWorkflowEngine)],
)
async def test_nonzero_internal_script_exit_stays_faulted_for_every_return_projection(
    tmp_path: Path,
    engine_name: str,
    engine_type: type[AdkWorkflowEngine] | type[LangGraphWorkflowEngine],
    reusable: bool,
    return_mode: str,
) -> None:
    task = {
        "run": {
            "script": {
                "language": "python",
                "code": (
                    "import sys\n"
                    "sys.stdout.write('must-not-be-projected')\n"
                    "sys.stderr.write('diagnostic')\n"
                    "raise SystemExit(7)\n"
                ),
            },
            "return": return_mode,
        }
    }
    workflow = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "contract",
            "name": "sandbox-nonzero-return-projection",
            "version": "1.0.0",
        },
        "do": [{"execute": task}],
    }
    if reusable:
        workflow["use"] = {"functions": {"execute": task}}
        workflow["do"] = [{"executeReusable": {"call": "execute"}}]

    sandbox = SandboxConfig(
        **supported_process_limits(),
        enabled=True,
        workspace_root=str(tmp_path / engine_name / "sandbox"),
        memory_bytes=None,
    )
    services = RuntimeServices(
        RuntimeConfig(sandbox=sandbox),
        model=FakeModel({"response": "ok"}),
        database_root=tmp_path / engine_name,
    )
    engine = engine_type()
    await engine.initialize(services)
    try:
        if not (
            engine.native.available if engine_name == "adk" else engine.native.task_nodes_available
        ):
            pytest.skip("actual engine SDK is unavailable in this independent environment")
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

        assert result.status == "faulted"
        assert result.output is None
        assert result.error is not None
        assert result.error["code"] == "sandbox_process_error"
        assert result.error["details"]["exit_code"] == 7
        sandbox_events = [
            event
            for event in services.events.events
            if event.event_type.startswith("SandboxExecution")
        ]
        assert [event.event_type for event in sandbox_events] == [
            "SandboxExecutionStarted",
            "SandboxExecutionFailed",
        ]
        assert sandbox_events[1].error == {"code": "sandbox_process_error"}
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
        **supported_process_limits(),
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
        assert result.output == "retry-ok\n"
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
