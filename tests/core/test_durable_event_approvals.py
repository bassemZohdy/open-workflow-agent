"""Approval projection and generic durable delivery share atomic scoped writes."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from open_workflow_agent.approvals import (
    APPROVAL_DECISION_EVENT,
    APPROVAL_REQUEST_EVENT,
    ApprovalEventBus,
)
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.effects import EffectIdentity, effect_request_digest
from open_workflow_agent.errors import EventValidationError, WorkflowExecutionError
from open_workflow_agent.services import RuntimeServices


def _config(backend: str, monkeypatch: Any) -> RuntimeConfig:
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    monkeypatch.setenv("OWA_EVENT_APPROVAL_TEST_TOKEN", "approval-test-token")
    return RuntimeConfig.model_validate(
        {
            "persistence": {"datasource": datasource} if datasource else {},
            "security": {
                "profiles": {
                    "operator": {
                        "type": "bearer",
                        "token": {"from_env": "OWA_EVENT_APPROVAL_TEST_TOKEN"},
                    }
                }
            },
            "approvals": {"enabled": True, "operator_security_profile": "operator"},
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("boundary", ["generic", "workflow"])
async def test_approval_request_projection_failure_rolls_back_event_and_receipt(
    tmp_path: Path, monkeypatch: Any, backend: str, boundary: str
) -> None:
    config = _config(backend, monkeypatch)
    services = RuntimeServices(config, database_root=None if backend == "postgresql" else tmp_path)
    bus, store = services.event_bus, services.event_store
    assert isinstance(bus, ApprovalEventBus) and store is not None
    approval_id, principal = str(uuid4()), "principal-" + str(uuid4())
    properties = {
        "id": approval_id,
        "subject": approval_id,
        "type": APPROVAL_REQUEST_EVENT,
        "data": {"question": "continue?"},
    }
    identity = EffectIdentity(
        str(uuid4()), "fingerprint", "/do/0/approval", (("/do/0/approval", 1),), "emit"
    )
    create_request = services.approvals.store.create_request

    def fail_after_request(*args: Any, **kwargs: Any) -> Any:
        create_request(*args, **kwargs)
        raise RuntimeError("injected request projection failure")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(services.approvals.store, "create_request", fail_after_request)
            with pytest.raises(WorkflowExecutionError, match="transaction could not be stored"):
                if boundary == "workflow":
                    await bus.publish_workflow_event(
                        identity,
                        effect_request_digest("emit", properties),
                        properties,
                        default_source="urn:contract:approval",
                        scope=principal,
                        execution_lease=None,
                    )
                else:
                    await bus.publish_scoped(
                        properties, default_source="urn:contract:approval", scope=principal
                    )
        assert services.approvals.store.get(approval_id) is None
        assert not store.arrivals(scope=principal)
        assert services.effect_journal.get(identity) is None
        assert not store.connection.execute(
            "SELECT identity FROM event_outbox WHERE identity = ?", (identity.key,)
        ).fetchall()
        accepted = await bus.publish_scoped(
            properties, default_source="urn:contract:approval", scope=principal
        )
        assert accepted.id == approval_id
        assert services.approvals.store.get(approval_id) is not None
    finally:
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_approval_decision_ingestion_failure_rolls_back_authorized_decision(
    tmp_path: Path, monkeypatch: Any, backend: str
) -> None:
    config = _config(backend, monkeypatch)
    services = RuntimeServices(config, database_root=None if backend == "postgresql" else tmp_path)
    bus, store = services.event_bus, services.event_store
    assert isinstance(bus, ApprovalEventBus) and store is not None
    approval_id, scope, operation = str(uuid4()), "principal-" + str(uuid4()), str(uuid4())
    await bus.publish_scoped(
        {"id": approval_id, "subject": approval_id, "type": APPROVAL_REQUEST_EVENT, "data": {}},
        default_source="urn:contract:approval",
        scope=scope,
    )
    ingest = store.ingest_in_transaction

    def fail_after_ingestion(*args: Any, **kwargs: Any) -> Any:
        ingest(*args, **kwargs)
        raise RuntimeError("injected decision delivery failure")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(store, "ingest_in_transaction", fail_after_ingestion)
            with pytest.raises(WorkflowExecutionError, match="transaction could not be stored"):
                await services.approvals.decide(
                    approval_id,
                    decision="approved",
                    operator_id="test-operator",
                    value=None,
                    operation_key=operation,
                )
        request = services.approvals.store.get(approval_id)
        assert (
            request is not None and request.status == "pending" and request.decision_event is None
        )
        assert [event.type for _, event in store.arrivals(scope=scope)] == [APPROVAL_REQUEST_EVENT]
        record = await services.approvals.decide(
            approval_id,
            decision="approved",
            operator_id="test-operator",
            value=None,
            operation_key=operation,
        )
        assert record.status == "approved"
        assert [event.type for _, event in store.arrivals(scope=scope)] == [
            APPROVAL_REQUEST_EVENT,
            APPROVAL_DECISION_EVENT,
        ]
    finally:
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_reopened_approval_decision_retains_trusted_request_scope(
    tmp_path: Path, monkeypatch: Any, backend: str
) -> None:
    config = _config(backend, monkeypatch)
    services = RuntimeServices(config, database_root=None if backend == "postgresql" else tmp_path)
    bus = services.event_bus
    assert isinstance(bus, ApprovalEventBus)
    approval_id, scope = str(uuid4()), "principal-" + str(uuid4())
    await bus.publish_scoped(
        {
            "id": approval_id,
            "subject": approval_id,
            "type": APPROVAL_REQUEST_EVENT,
            "data": {},
            "principal": "forged",
        },
        default_source="urn:contract:approval",
        scope=scope,
    )
    services.close()
    restarted = RuntimeServices(config, database_root=None if backend == "postgresql" else tmp_path)
    try:
        record = await restarted.approvals.decide(
            approval_id,
            decision="approved",
            operator_id="test-operator",
            value={"allowed": True},
            operation_key=str(uuid4()),
        )
        assert record.event_scope == scope
        store = restarted.event_store
        assert store is not None
        decisions = [
            event
            for _, event in store.arrivals(scope=scope)
            if event.type == APPROVAL_DECISION_EVENT
        ]
        assert len(decisions) == 1 and decisions[0].subject == approval_id
        assert not any(
            event.subject == approval_id for _, event in store.arrivals(scope="anonymous")
        )
        assert not store.arrivals(scope="forged")
    finally:
        restarted.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_approval_decision_backpressure_preserves_original_pending_request(
    tmp_path: Path, monkeypatch: Any, backend: str
) -> None:
    config = _config(backend, monkeypatch)
    services = RuntimeServices(config, database_root=None if backend == "postgresql" else tmp_path)
    bus, store = services.event_bus, services.event_store
    assert isinstance(bus, ApprovalEventBus) and store is not None
    approval_id, scope = str(uuid4()), "principal-" + str(uuid4())
    try:
        accepted = await bus.publish_scoped(
            {"id": approval_id, "subject": approval_id, "type": APPROVAL_REQUEST_EVENT, "data": {}},
            default_source="urn:contract:approval",
            scope=scope,
        )
        count = store.connection.execute("SELECT COUNT(*) FROM event_inbox").fetchone()[0]
        original_limits = store.limits
        store.limits = replace(store.limits, max_retained_events=count)
        with pytest.raises(EventValidationError):
            await services.approvals.decide(
                approval_id,
                decision="approved",
                operator_id="test-operator",
                value=None,
                operation_key=str(uuid4()),
            )
        record = services.approvals.store.get(approval_id)
        assert record is not None and record.status == "pending" and record.decision_event is None
        assert [event.as_dict() for _, event in store.arrivals(scope=scope)] == [accepted.as_dict()]
        store.limits = original_limits
    finally:
        services.close()
