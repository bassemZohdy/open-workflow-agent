from __future__ import annotations

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow
from open_workflow_agent_adk import AdkWorkflowEngine
from open_workflow_agent_langgraph import LangGraphWorkflowEngine

ENGINE_CASES = [("adk", AdkWorkflowEngine), ("langgraph", LangGraphWorkflowEngine)]


def _child_workflow():
    return {
        "document": {
            "dsl": "1.0.3",
            "namespace": "subworkflow-contracts",
            "name": "child",
            "version": "1.0.0",
        },
        "do": [{"make_child": {"set": {"child": "${ .value }"}}}],
    }


def _parent_workflow():
    return {
        "document": {
            "dsl": "1.0.3",
            "namespace": "subworkflow-contracts",
            "name": "parent",
            "version": "1.0.0",
        },
        "do": [
            {
                "child": {
                    "run": {
                        "workflow": {
                            "namespace": "subworkflow-contracts",
                            "name": "child",
                            "version": "1.0.0",
                            "input": {"value": "${ .value }"},
                        }
                    }
                }
            },
            {"finish": {"set": {"result": "${ .child }"}}},
        ],
    }


def _handle(services, engine_name, plan):
    return services.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id="subworkflow-user",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("engine_name", "engine_type"), ENGINE_CASES)
async def test_run_creates_separate_child_invocation_with_common_parent_identity(
    tmp_path, engine_name, engine_type
):
    services = RuntimeServices(
        RuntimeConfig(), model=FakeModel(), database_root=tmp_path / engine_name
    )
    engine = engine_type()
    await engine.initialize(services)
    child = services.workflow_catalog.register(_child_workflow())
    parent = compile_workflow(_parent_workflow())
    parent_handle = _handle(services, engine_name, parent)

    result = await engine.invoke(parent, parent_handle, {"value": "from-parent"})

    assert result.status == "completed"
    assert result.output == {"result": "from-parent"}
    child_started = next(
        event
        for event in services.events.events
        if event.event_type == "WorkflowStarted" and event.workflow_name == child.name
    )
    assert child_started.parent_invocation_id == parent_handle.invocation_id
    assert child_started.parent_task_reference == "/do/0/child"
    assert child_started.invocation_id != parent_handle.invocation_id
    child_handle = services.invocations.get(child_started.invocation_id or "")
    assert child_handle is not None
    assert child_handle.session_id != parent_handle.session_id
    assert child_handle.parent_invocation_id == parent_handle.invocation_id
    assert child_handle.status == "completed"
    services.close()


@pytest.mark.asyncio
async def test_subworkflow_common_result_matches_engines(tmp_path):
    signatures = []
    for engine_name, engine_type in ENGINE_CASES:
        services = RuntimeServices(
            RuntimeConfig(), model=FakeModel(), database_root=tmp_path / engine_name
        )
        engine = engine_type()
        await engine.initialize(services)
        services.workflow_catalog.register(_child_workflow())
        parent = compile_workflow(_parent_workflow())
        result = await engine.invoke(
            parent, _handle(services, engine_name, parent), {"value": "same"}
        )
        signatures.append((result.status, result.output))
        services.close()
    assert signatures[0] == signatures[1] == ("completed", {"result": "same"})
