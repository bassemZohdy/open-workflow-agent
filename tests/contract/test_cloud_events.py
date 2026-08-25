from __future__ import annotations

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow
from open_workflow_agent_adk import AdkWorkflowEngine
from open_workflow_agent_langgraph import LangGraphWorkflowEngine

ENGINE_CASES = [("adk", AdkWorkflowEngine), ("langgraph", LangGraphWorkflowEngine)]


def _handle(services, engine_name, plan):
    return services.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("engine_name", "engine_type"), ENGINE_CASES)
async def test_lifecycle_cloud_events_are_common_and_engine_neutral(
    tmp_path, engine_name, engine_type
):
    services = RuntimeServices(
        RuntimeConfig(), model=FakeModel(), database_root=tmp_path / engine_name
    )
    engine = engine_type()
    await engine.initialize(services)
    plan = compile_workflow()
    result = await engine.invoke(plan, _handle(services, engine_name, plan), {"question": "hi"})

    assert result.status == "completed"
    events = services.lifecycle_events.snapshot()
    assert events
    assert all(event.specversion == "1.0" for event in events)
    assert all(event.source == "urn:open-workflow-agent:lifecycle" for event in events)
    assert all(event.dataschema == "urn:open-workflow-agent:schema:lifecycle:1" for event in events)
    assert all("engine_execution_reference" not in event.raw() for event in events)
    assert [event.data["event_type"] for event in events] == [
        "WorkflowStarted",
        "TaskStarted",
        "TaskProgress",
        "TaskCompleted",
        "WorkflowCompleted",
    ]
    services.close()


@pytest.mark.asyncio
async def test_lifecycle_cloud_event_signatures_match_between_engines(tmp_path):
    signatures = []
    for engine_name, engine_type in ENGINE_CASES:
        services = RuntimeServices(
            RuntimeConfig(), model=FakeModel(), database_root=tmp_path / engine_name
        )
        engine = engine_type()
        await engine.initialize(services)
        plan = compile_workflow()
        result = await engine.invoke(plan, _handle(services, engine_name, plan), {})
        signatures.append(
            (
                result.status,
                [
                    (
                        event.type,
                        event.data["event_type"],
                        event.data.get("status"),
                        event.data.get("task_reference"),
                    )
                    for event in services.lifecycle_events.snapshot()
                ],
            )
        )
        services.close()

    assert signatures[0] == signatures[1]
