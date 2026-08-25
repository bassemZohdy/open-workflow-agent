from __future__ import annotations

import asyncio

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow
from open_workflow_agent_adk import AdkWorkflowEngine
from open_workflow_agent_langgraph import LangGraphWorkflowEngine

ENGINE_CASES = [("adk", AdkWorkflowEngine), ("langgraph", LangGraphWorkflowEngine)]


def _workflow(tasks):
    return {
        "document": {
            "dsl": "1.0.3",
            "namespace": "event-tests",
            "name": "portable-eventing",
            "version": "1.0.0",
        },
        "do": tasks,
    }


def _handle(services, engine_name, plan):
    return services.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )


async def _until(predicate):
    for _ in range(200):
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("eventing condition was not reached")


@pytest.mark.asyncio
@pytest.mark.parametrize(("engine_name", "engine_type"), ENGINE_CASES)
async def test_emit_and_listen_have_equivalent_common_results(tmp_path, engine_name, engine_type):
    services = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
    engine = engine_type()
    await engine.initialize(services)
    listen_plan = compile_workflow(
        _workflow(
            [
                {
                    "approval": {
                        "listen": {
                            "to": {
                                "one": {
                                    "with": {
                                        "type": "approval.granted",
                                        "subject": "case-1",
                                    }
                                }
                            },
                            "read": "data",
                        }
                    }
                },
                {"finish": {"set": {"approved": "${ .approved }"}}},
            ]
        )
    )
    handle = _handle(services, engine_name, listen_plan)
    invocation = asyncio.create_task(engine.invoke(listen_plan, handle, {}))
    await _until(lambda: handle.status == "waiting")
    published = await services.event_bus.publish(
        {
            "id": "approval-1",
            "type": "approval.granted",
            "subject": "case-1",
            "data": {"approved": True},
        },
        default_source="urn:operator",
    )
    result = await invocation
    assert result.status == "completed"
    assert result.output == {"approved": True}
    assert published.id == "approval-1"
    assert any(event.event_type == "EventReceived" for event in services.events.events)
    assert any(event.event_type == "WorkflowResumed" for event in services.events.events)

    emit_plan = compile_workflow(
        _workflow(
            [
                {
                    "created": {
                        "emit": {
                            "event": {
                                "with": {
                                    "id": "created-1",
                                    "type": "case.created",
                                    "data": {"case": "${ .case }"},
                                }
                            }
                        }
                    }
                },
                {"finish": {"set": {"done": True}}},
            ]
        )
    )
    emit_handle = _handle(services, engine_name, emit_plan)
    emit_result = await engine.invoke(emit_plan, emit_handle, {"case": "case-1"})
    assert emit_result.status == "completed"
    assert emit_result.output == {"done": True}
    assert services.event_bus.published[-1].as_dict()["data"] == {"case": "case-1"}
    assert any(event.event_type == "EventEmitted" for event in services.events.events)
    services.close()


@pytest.mark.asyncio
async def test_eventing_common_lifecycle_signature_matches_engines(tmp_path):
    signatures = []
    for engine_name, engine_type in ENGINE_CASES:
        services = RuntimeServices(
            RuntimeConfig(), model=FakeModel(), database_root=tmp_path / engine_name
        )
        engine = engine_type()
        await engine.initialize(services)
        plan = compile_workflow(
            _workflow(
                [
                    {
                        "created": {
                            "emit": {
                                "event": {
                                    "with": {
                                        "id": "shared-event",
                                        "type": "case.created",
                                        "data": {"ok": True},
                                    }
                                }
                            }
                        }
                    }
                ]
            )
        )
        result = await engine.invoke(plan, _handle(services, engine_name, plan), {})
        signatures.append(
            (
                result.status,
                result.output,
                [
                    (event.event_type, event.status, event.task_reference, event.event_name)
                    for event in services.events.events
                    if event.event_type in {"EventEmitted", "TaskStarted", "TaskCompleted"}
                ],
            )
        )
        services.close()
    assert signatures[0] == signatures[1]
