"""Authenticated event ingestion, sanitized conflicts and embedded backpressure."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from open_workflow_agent.api import create_app
from open_workflow_agent.approvals import APPROVAL_DECISION_EVENT
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.services import RuntimeServices


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_api_ingestion_uses_authenticated_scope_and_ignores_payload_identity(
    tmp_path: Path, monkeypatch: Any, backend: str
) -> None:
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    principal, identity = "event-principal-" + str(uuid4()), str(uuid4())
    monkeypatch.setenv("OWA_EVENT_API_TEST_TOKEN", "event-api-test-secret")
    config = RuntimeConfig.model_validate(
        {
            "persistence": {"datasource": datasource} if datasource else {},
            "security": {
                "profiles": {
                    "events": {
                        "type": "bearer",
                        "principal": principal,
                        "token": {"from_env": "OWA_EVENT_API_TEST_TOKEN"},
                    }
                }
            },
            "server": {"api_security_profile": "events"},
        }
    )
    services = RuntimeServices(config, database_root=None if datasource else tmp_path)
    event = {
        "id": identity,
        "source": "urn:contract:api",
        "type": "fixture.scoped",
        "data": None,
        "principal": "forged",
        "user_id": "forged",
    }
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(config=config, services=services)),
            base_url="http://test",
        ) as client:
            denied = await client.post("/v1/events", json={"event": event})
            assert denied.status_code == 401
            headers = {"authorization": "Bearer event-api-test-secret"}
            accepted = await client.post("/v1/events", json={"event": event}, headers=headers)
            assert accepted.status_code == 200, accepted.text
            duplicate = await client.post("/v1/events", json={"event": event}, headers=headers)
            assert duplicate.status_code == 200 and duplicate.json() == accepted.json()
            assert "data" in accepted.json() and accepted.json()["data"] is None
            conflict = await client.post(
                "/v1/events",
                json={"event": {**event, "data": "private-conflict-payload"}},
                headers=headers,
            )
            assert conflict.status_code == 400
            assert "private-conflict-payload" not in conflict.text
        store = services.event_store
        assert store is not None
        scoped = store.arrivals(scope=principal)
        assert [envelope.id for _, envelope in scoped] == [identity]
        assert not store.arrivals(scope="forged")
        assert not any(envelope.id == identity for _, envelope in store.arrivals(scope="anonymous"))
    finally:
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["capacity", "size", "reserved-approval"])
async def test_event_api_rejects_excess_or_protected_input_without_partial_acceptance(
    tmp_path: Path, monkeypatch: Any, case: str
) -> None:
    monkeypatch.setenv("OWA_EVENT_OPERATOR_TOKEN", "operator-test-token")
    config = RuntimeConfig.model_validate(
        {
            "events": {
                "max_event_bytes": 1024,
                "max_retained_events": 1 if case == "capacity" else 4,
            },
            "security": {
                "profiles": {
                    "operator": {
                        "type": "bearer",
                        "token": {"from_env": "OWA_EVENT_OPERATOR_TOKEN"},
                    }
                }
            },
            "approvals": {
                "enabled": case == "reserved-approval",
                "operator_security_profile": "operator",
            },
        }
    )
    services = RuntimeServices(config, database_root=tmp_path)
    first = {"id": "first", "type": "fixture.bound", "data": {"accepted": True}}
    second = {"id": "second", "type": "fixture.bound", "data": {"accepted": False}}
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(config=config, services=services)),
            base_url="http://test",
        ) as client:
            accepted = await client.post("/v1/events", json={"event": first})
            assert accepted.status_code == 200
            if case == "size":
                second["data"] = "sensitive-payload-" * 100
            elif case == "reserved-approval":
                for index, event_type in enumerate(
                    (
                        APPROVAL_DECISION_EVENT + ".forged",
                        APPROVAL_DECISION_EVENT.replace(".", "X"),
                    )
                ):
                    lookalike = await client.post(
                        "/v1/events",
                        json={"event": {"id": f"lookalike-{index}", "type": event_type}},
                    )
                    assert lookalike.status_code == 200
                second["type"] = APPROVAL_DECISION_EVENT
            denied = await client.post("/v1/events", json={"event": second})
            assert denied.status_code == 400
            assert "sensitive-payload" not in denied.text
            duplicate = await client.post("/v1/events", json={"event": first})
            assert duplicate.status_code == 200 and duplicate.json() == accepted.json()
        store = services.event_store
        assert store is not None
        assert [event.id for _, event in store.arrivals()] == (
            ["first", "lookalike-0", "lookalike-1"] if case == "reserved-approval" else ["first"]
        )
    finally:
        services.close()
