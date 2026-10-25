from __future__ import annotations

import httpx
import pytest
from open_workflow_agent.api import create_app
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.observability import WorkflowEvent
from open_workflow_agent.services import RuntimeServices


@pytest.mark.asyncio
async def test_durable_lifecycle_api_replays_after_restart_and_requires_read_grant(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("OWA_HISTORY_READ_TOKEN", "history-controlled-token")
    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "observability": {"history": {"enabled": True}},
            "server": {"api_security_profile": "reader"},
            "security": {
                "profiles": {
                    "reader": {
                        "type": "bearer",
                        "token": {"from_env": "OWA_HISTORY_READ_TOKEN"},
                        "principal": "history-reader",
                        "roles": ["reader"],
                    }
                }
            },
            "execution_authorization": {
                "enabled": True,
                "id": "history-read",
                "policy": {"rules": []},
            },
        }
    )
    first = RuntimeServices(config, model=FakeModel(), database_root=tmp_path)
    for identifier in ("retained-one", "retained-two"):
        first.lifecycle_events.emit(WorkflowEvent("WorkflowCompleted", event_id=identifier))
    original = [event.as_dict() for event in first.lifecycle_events.snapshot(10)]
    first.close()
    restored = RuntimeServices(config, model=FakeModel(), database_root=tmp_path)
    try:
        app = create_app(config=config, services=restored)
        headers = {"Authorization": "Bearer history-controlled-token"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            denied = await client.get("/v1/events/lifecycle", headers=headers)
            denied_stream = await client.get("/v1/events/lifecycle/stream", headers=headers)
            assert denied.status_code == denied_stream.status_code == 403
            assert [event.as_dict() for event in restored.lifecycle_events.snapshot(10)] == original
            from open_workflow_agent.security import AuthorizationPolicy

            config.execution_authorization.policy = AuthorizationPolicy.model_validate(
                {
                    "rules": [
                        {
                            "actions": ["events.read"],
                            "resources": ["events:lifecycle"],
                            "roles": ["reader"],
                        }
                    ]
                }
            )
            response = await client.get("/v1/events/lifecycle", headers=headers)
            assert response.status_code == 200 and response.json() == original
            profile = (await client.get("/v1/capabilities", headers=headers)).json()["features"][
                "lifecycleStreaming"
            ]
            assert profile["replay"] == "bounded_durable_history_by_last_event_id"
            replay = await client.get(
                "/v1/events/lifecycle/stream?max_events=1&timeout_seconds=1",
                headers={**headers, "Last-Event-ID": "retained-one"},
            )
            assert replay.status_code == 200 and "id: retained-two\n" in replay.text
            unavailable = await client.get(
                "/v1/events/lifecycle/stream", headers={**headers, "Last-Event-ID": "missing"}
            )
            assert unavailable.status_code == 409
    finally:
        restored.close()


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
