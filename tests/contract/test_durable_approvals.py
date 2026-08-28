from __future__ import annotations

import asyncio

import pytest
from engine_cases import engine_cases
from open_workflow_agent.approvals import APPROVAL_DECISION_EVENT, APPROVAL_REQUEST_EVENT
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.events import InMemoryEventBus
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow

ENGINE_CASES = engine_cases()


def _config() -> RuntimeConfig:
    return RuntimeConfig.model_validate(
        {"approvals": {"enabled": True, "operator_token": "test-operator-token"}}
    )


def _workflow():
    return {
        "document": {
            "dsl": "1.0.3",
            "namespace": "approval-tests",
            "name": "durable-approval",
            "version": "1.0.0",
        },
        "do": [
            {
                "approval": {
                    "listen": {
                        "to": {
                            "one": {
                                "with": {
                                    "type": APPROVAL_DECISION_EVENT,
                                    "subject": "approval-contract-1",
                                }
                            }
                        },
                        "read": "data",
                    }
                }
            },
            {"finish": {"set": {"decision": "${ .decision }"}}},
        ],
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


@pytest.mark.asyncio
@pytest.mark.parametrize(("engine_name", "engine_type"), ENGINE_CASES)
async def test_durable_approval_replay_is_engine_neutral(tmp_path, engine_name, engine_type):
    database_root = tmp_path / engine_name
    first = RuntimeServices(
        _config(),
        model=FakeModel(),
        database_root=database_root,
        event_bus=InMemoryEventBus(),
    )
    await first.event_bus.publish(
        {
            "id": "approval-contract-1",
            "subject": "approval-contract-1",
            "type": APPROVAL_REQUEST_EVENT,
            "data": {"question": "continue?"},
        },
        default_source="urn:contract",
    )
    await first.approvals.decide(
        "approval-contract-1",
        decision="approved",
        operator_id="operator-contract",
        value={"ticket": "CONTRACT-1"},
        operation_key="approval-decision-contract-1",
    )
    first.close()

    restarted = RuntimeServices(
        _config(),
        model=FakeModel(),
        database_root=database_root,
        event_bus=InMemoryEventBus(),
    )
    engine = engine_type()
    await engine.initialize(restarted)
    plan = compile_workflow(_workflow())
    result = await asyncio.wait_for(
        engine.invoke(plan, _handle(restarted, engine_name, plan), {}),
        timeout=1.0,
    )
    assert result.status == "completed"
    assert result.output == {"decision": "approved"}
    assert any(event.event_type == "EventReceived" for event in restarted.events.events)
    assert any(event.event_type == "WorkflowResumed" for event in restarted.events.events)
    restarted.close()
