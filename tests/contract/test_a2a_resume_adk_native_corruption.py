"""Public A2A resume rejects contradictory native ADK input history."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from open_workflow_agent.a2a import A2A_PROTOCOL_VERSION
from open_workflow_agent.api import create_app
from test_native_checkpoint_fencing import _snapshot as _native_snapshot
from test_workflow_version_recovery import (
    PRINCIPAL,
    TOKEN,
    TOKEN_ENV,
    Receiver,
    _actual_native,
    _clock,
    _common_snapshot,
    _config,
    _definition,
    _services,
)

pytestmark = pytest.mark.usefixtures("explicit_workflow_delivery")


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("surface", ["jsonrpc", "http_json"])
async def test_a2a_resume_rejects_corrupt_adk_event_history_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    surface: str,
) -> None:
    """Authenticated A2A resume validates actual ADK history before taking ownership."""
    adk = pytest.importorskip("open_workflow_agent_adk")
    events = pytest.importorskip("google.adk.events")
    genai_types = pytest.importorskip("google.genai.types")
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    _clock(monkeypatch, [1000.0])
    receiver = Receiver(tmp_path / "receiver.sqlite3")
    config = _config(_definition("1.0.0"), backend, a2a_transport=surface)
    services = _services(tmp_path, config, receiver)
    engine = adk.AdkWorkflowEngine()
    _actual_native(engine, "adk")
    app = create_app(config=config, services=services, engine=engine)
    headers = {"Authorization": "Bearer " + TOKEN}
    handle: Any = None

    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                headers=headers,
            ) as client:
                started = await client.post(
                    "/v1/invoke",
                    json={
                        "input": {"secret": "retained-adk-secret"},
                        "session_id": "a2a-adk-corrupt-session",
                        "user_id": "owa-user",
                    },
                )
                assert started.status_code == 200 and started.json()["status"] == "waiting"
                handle = services.invocations.get(started.json()["invocation_id"])
                assert handle is not None and handle.owner_principal == PRINCIPAL
                assert handle.session_id is not None
                assert len(receiver.calls) == len(receiver.rows()) == 1

                sessions = engine.native._session_service(services.engine_database_path("adk"))
                try:
                    session = await sessions.get_session(
                        app_name="open-workflow-agent",
                        user_id=engine._native_user(handle) or "owa-user",
                        session_id=handle.session_id,
                    )
                    assert session is not None
                    await sessions.append_event(
                        session=session,
                        event=events.Event(
                            invocation_id=handle.engine_execution_reference,
                            author="user",
                            content=genai_types.Content(
                                role="user",
                                parts=[genai_types.Part(text=json.dumps({"forged-input": True}))],
                            ),
                        ),
                    )
                finally:
                    await engine.native._close_sessions(sessions)

                common_before = _common_snapshot(services, handle.invocation_id)
                native_before = _native_snapshot(services, handle, backend)
                message = {
                    "role": "ROLE_USER",
                    "messageId": "reject-corrupt-adk-event-history",
                    "taskId": handle.invocation_id,
                    "contextId": handle.session_id,
                    "parts": [{"text": "resume corrupted ADK history"}],
                }
                if surface == "jsonrpc":
                    response = await client.post(
                        "/a2a",
                        headers={"a2a-version": A2A_PROTOCOL_VERSION},
                        json={
                            "jsonrpc": "2.0",
                            "id": "corrupt-adk-history-resume",
                            "method": "SendMessage",
                            "params": {"message": message},
                        },
                    )
                    assert response.status_code == 200, response.text
                    assert response.json()["error"]["message"] == "workflow execution failed"
                    assert response.json()["error"]["data"][0]["metadata"]["code"] == (
                        "invalid_invocation_state"
                    )
                else:
                    response = await client.post(
                        "/a2a/message:send",
                        headers={"A2A-Version": A2A_PROTOCOL_VERSION},
                        json={"message": message},
                    )
                    assert response.status_code == 500, response.text
                    assert response.json()["error"]["details"][0]["metadata"]["owaCode"] == (
                        "invalid_invocation_state"
                    )

                assert "retained-adk-secret" not in response.text
                assert "forged-input" not in response.text and TOKEN not in response.text
                assert _common_snapshot(services, handle.invocation_id) == common_before
                assert _native_snapshot(services, handle, backend) == native_before
                assert (
                    not engine._active
                    and not engine._execution_leases
                    and not engine._ownership_tasks
                )
                assert len(receiver.calls) == len(receiver.rows()) == 1
                assert not services.model.calls
    finally:
        await engine.shutdown()
        services.close()
