from __future__ import annotations

import asyncio

import httpx
import pytest
from open_workflow_agent.api import create_app
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.services import RuntimeServices


@pytest.mark.asyncio
async def test_invoke_capabilities_health_and_reload(tmp_path):
    services = RuntimeServices(
        RuntimeConfig(), model=FakeModel({"response": "hello"}), database_root=tmp_path
    )
    app = create_app(config=RuntimeConfig(), services=services)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/health/live")).json() == {"status": "ok"}
            capabilities = (await client.get("/v1/capabilities")).json()
            assert capabilities["workflowDsl"] == "1.0.3"
            assert capabilities["protocols"] == ["http", "mcp", "a2a", "openapi"]
            assert capabilities["policies"] == ["retry", "timeout"]
            assert {"try", "wait", "raise"} <= set(capabilities["tasks"])
            assert capabilities["features"]["cancellation"] is True
            assert capabilities["features"]["waiting"] is True
            assert capabilities["features"]["events"] == {
                "emit": True,
                "listen": True,
                "durable": False,
            }
            event = await client.post(
                "/v1/events",
                json={"event": {"id": "api-event-1", "type": "api.test", "data": {"ok": True}}},
            )
            assert event.status_code == 200
            assert event.json()["id"] == "api-event-1"
            result = await client.post("/v1/invoke", json={"input": {"question": "hi"}})
            assert result.status_code == 200
            assert result.json()["status"] == "completed"
            assert (await client.post("/v1/admin/knowledge/reload")).status_code == 200


@pytest.mark.asyncio
async def test_api_limits_payloads_and_normalizes_not_found(tmp_path):
    config = RuntimeConfig.model_validate(
        {"model": {"provider": "fake"}, "server": {"max_request_bytes": 32}}
    )
    services = RuntimeServices(
        config, model=FakeModel({"response": "hello"}), database_root=tmp_path
    )
    app = create_app(config=config, services=services)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/health/ready")).json() == {"status": "ok"}
            oversized = await client.post("/v1/invoke", json={"input": "x" * 100})
            assert oversized.status_code == 413
            assert oversized.json()["error"]["code"] == "request_too_large"
            missing = await client.post("/v1/invocations/missing/resume", json={"input": {}})
            assert missing.status_code == 404
            assert missing.json()["error"]["code"] == "invocation_not_found"
            malformed = await client.post("/v1/invoke", json={"unexpected": True})
            assert malformed.status_code == 422
            assert malformed.json()["error"]["code"] == "request_validation_error"


@pytest.mark.asyncio
async def test_api_cancel_waiting_invocation_is_idempotent(tmp_path):
    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "workflow": {
                "definition": {
                    "document": {
                        "dsl": "1.0.3",
                        "namespace": "tests",
                        "name": "api-cancel",
                        "version": "1.0.0",
                    },
                    "do": [{"pause": {"wait": {"seconds": 5}}}],
                }
            },
        }
    )
    services = RuntimeServices(config, model=FakeModel(), database_root=tmp_path)
    app = create_app(config=config, services=services)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            invoke_task = asyncio.create_task(client.post("/v1/invoke", json={"input": {}}))
            for _ in range(100):
                waiting = next(
                    (
                        event
                        for event in services.events.events
                        if event.event_type == "WorkflowWaiting"
                    ),
                    None,
                )
                if waiting is not None:
                    break
                await asyncio.sleep(0.005)
            assert waiting is not None
            cancelled = await client.post(
                f"/v1/invocations/{waiting.invocation_id}/cancel",
                headers={"Idempotency-Key": "cancel-api-1"},
            )
            assert cancelled.status_code == 200
            assert cancelled.json()["status"] == "cancelled"
            duplicate = await client.post(
                f"/v1/invocations/{waiting.invocation_id}/cancel",
                headers={"Idempotency-Key": "cancel-api-1"},
            )
            assert duplicate.json()["status"] == "cancelled"
            invoke_result = await invoke_task
            assert invoke_result.status_code == 200
            assert invoke_result.json()["status"] == "cancelled"
