from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from open_workflow_agent.api import create_app
from open_workflow_agent.approvals import (
    APPROVAL_DECISION_EVENT,
    APPROVAL_REQUEST_EVENT,
    ApprovalEventBus,
    ApprovalService,
)
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.errors import ApprovalAuthorizationError, ApprovalConflict
from open_workflow_agent.events import InMemoryEventBus
from open_workflow_agent.services import RuntimeServices


@pytest.mark.asyncio
async def test_durable_approval_decision_is_idempotent_and_replayed_after_restart(tmp_path) -> None:
    database = tmp_path / "runtime.sqlite3"
    service = ApprovalService(
        database,
        enabled=True,
        operator_token="secret",
        event_bus=InMemoryEventBus(),
    )
    assert isinstance(service.event_bus, ApprovalEventBus)
    request = await service.event_bus.publish(
        {
            "id": "approval-1",
            "subject": "approval-1",
            "type": APPROVAL_REQUEST_EVENT,
            "data": {"question": "deploy?"},
        },
        default_source="urn:test",
    )
    assert request.id == "approval-1"

    waiting = asyncio.create_task(
        service.event_bus.receive(
            {"one": {"with": {"type": APPROVAL_DECISION_EVENT, "subject": "approval-1"}}}
        )
    )
    await asyncio.sleep(0)
    record = await service.decide(
        "approval-1",
        decision="approved",
        operator_id="operator-1",
        value={"ticket": "CHG-1"},
        operation_key="decision-1",
    )
    event = await waiting
    assert record.status == "approved"
    assert event.data["decision"] == "approved"

    repeated = await service.decide(
        "approval-1",
        decision="approved",
        operator_id="operator-1",
        value={"ticket": "CHG-1"},
        operation_key="decision-1",
    )
    assert repeated.decision_event == record.decision_event
    with pytest.raises(ApprovalConflict):
        await service.decide(
            "approval-1",
            decision="rejected",
            operator_id="operator-1",
            value=None,
            operation_key="decision-2",
        )
    service.close()

    restarted = ApprovalService(
        database,
        enabled=True,
        operator_token="secret",
        event_bus=InMemoryEventBus(),
    )
    replay = await asyncio.wait_for(
        restarted.event_bus.receive(
            {"one": {"with": {"type": APPROVAL_DECISION_EVENT, "subject": "approval-1"}}}
        ),
        timeout=0.1,
    )
    assert replay.id == "decision-1"
    assert replay.data["operator_id"] == "operator-1"
    restarted.close()


@pytest.mark.asyncio
async def test_approval_decision_cannot_bypass_operator_api(tmp_path) -> None:
    service = ApprovalService(
        tmp_path / "runtime.sqlite3",
        enabled=True,
        operator_token="secret",
        event_bus=InMemoryEventBus(),
    )
    with pytest.raises(ValueError, match="approval decisions"):
        await service.event_bus.publish(
            {
                "id": "decision-direct",
                "subject": "approval-1",
                "type": APPROVAL_DECISION_EVENT,
                "data": {"decision": "approved"},
            },
            default_source="urn:test",
        )
    with pytest.raises(ApprovalAuthorizationError):
        service.authorize("Bearer wrong", "operator-1")
    assert service.authorize("Bearer secret", "operator-1") == "operator-1"
    service.close()


def test_approval_api_requires_authorization_and_reports_capability(tmp_path) -> None:
    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake", "name": "fake/default"},
            "approvals": {"enabled": True, "operator_token": "secret"},
        }
    )
    services = RuntimeServices(config, database_root=tmp_path)
    app = create_app(config=config, services=services)
    with TestClient(app) as client:
        capabilities = client.get("/v1/capabilities")
        assert capabilities.status_code == 200
        assert capabilities.json()["features"]["approvals"] == {
            "approval": True,
            "durable": True,
            "replay": True,
            "operatorAuthorization": "bearer",
        }
        request = client.post(
            "/v1/events",
            json={
                "event": {
                    "id": "approval-api-1",
                    "subject": "approval-api-1",
                    "type": APPROVAL_REQUEST_EVENT,
                    "data": {"question": "release?"},
                }
            },
        )
        assert request.status_code == 200
        assert client.get("/v1/approvals/approval-api-1").json()["status"] == "pending"

        unauthorized = client.post(
            "/v1/approvals/approval-api-1/decision",
            json={"decision": "approved", "value": {}},
        )
        assert unauthorized.status_code == 403

        decision = client.post(
            "/v1/approvals/approval-api-1/decision",
            headers={
                "Authorization": "Bearer secret",
                "X-Operator-Id": "operator-api",
                "Idempotency-Key": "decision-api-1",
            },
            json={"decision": "approved", "value": {"ticket": "CHG-2"}},
        )
        assert decision.status_code == 200
        assert decision.json()["status"] == "approved"
        assert decision.json()["operator_id"] == "operator-api"
        listed = client.get("/v1/approvals", params={"status": "approved"})
        assert listed.status_code == 200
        assert [item["approval_id"] for item in listed.json()] == ["approval-api-1"]
    services.close()
