from __future__ import annotations

import httpx
import pytest
from open_workflow_agent.api import create_app
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.observability import WorkflowEvent
from open_workflow_agent.services import RuntimeServices


@pytest.mark.asyncio
async def test_lifecycle_sse_endpoint_replays_and_advertises_common_profile(tmp_path) -> None:
    config = RuntimeConfig.model_validate({"model": {"provider": "fake"}})
    services = RuntimeServices(config, model=FakeModel(), database_root=tmp_path)
    services.lifecycle_events.emit(
        WorkflowEvent("WorkflowStarted", invocation_id="inv-1", event_id="event-1")
    )
    services.lifecycle_events.emit(
        WorkflowEvent(
            "WorkflowCompleted",
            invocation_id="inv-1",
            event_id="event-2",
            status="completed",
        )
    )
    app = create_app(config=config, services=services)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            capabilities = (await client.get("/v1/capabilities")).json()
            profile = capabilities["features"]["lifecycleStreaming"]
            assert profile["enabled"] is True
            assert profile["transport"] == "sse"
            assert profile["a2aStreaming"] is True
            assert capabilities["features"]["streaming"] is False

            response = await client.get(
                "/v1/events/lifecycle/stream?max_events=1&timeout_seconds=1",
                headers={"Last-Event-ID": "event-1"},
            )
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            assert "id: event-2\n" in response.text
            assert "event: lifecycle\n" in response.text
            assert 'event: stream.end\ndata: {"reason":"event_limit"}' in response.text


@pytest.mark.asyncio
async def test_lifecycle_sse_endpoint_rejects_expired_replay_cursor(tmp_path) -> None:
    config = RuntimeConfig.model_validate({"model": {"provider": "fake"}})
    services = RuntimeServices(config, model=FakeModel(), database_root=tmp_path)
    services.lifecycle_events.emit(
        WorkflowEvent("WorkflowStarted", invocation_id="inv-1", event_id="event-1")
    )
    app = create_app(config=config, services=services)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/v1/events/lifecycle/stream?max_events=1&timeout_seconds=1",
                headers={"Last-Event-ID": "expired-event"},
            )
            assert response.status_code == 409
            assert response.json()["error"]["code"] == "stream_replay_unavailable"
