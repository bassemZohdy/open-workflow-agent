"""Native A2A resumes reject a mismatched Task context before changing state."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from engine_cases import engine_cases
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
ENGINES = [case for case in engine_cases() if case[0] in {"adk", "langgraph"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("surface", ["jsonrpc", "http_json"])
async def test_a2a_resume_rejects_context_mismatch_before_native_or_delivery_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: Any,
    backend: str,
    surface: str,
) -> None:
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    _clock(monkeypatch, [1000.0])
    engine = engine_type()
    _actual_native(engine, engine_name)
    receiver = Receiver(tmp_path / "receiver.sqlite3")
    config = _config(_definition("1.0.0"), backend, a2a_transport=surface)
    services = _services(tmp_path, config, receiver)
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
                    "/v1/invoke", json={"input": {"secret": "context-mismatch-original"}}
                )
                assert started.status_code == 200 and started.json()["status"] == "waiting"
                handle = services.invocations.get(started.json()["invocation_id"])
                assert handle is not None and handle.owner_principal == PRINCIPAL
                assert handle.session_id is not None
                assert services.invocations.read_input(handle) == {
                    "secret": "context-mismatch-original"
                }
                assert len(receiver.calls) == len(receiver.rows()) == 1

                common_before = _common_snapshot(services, handle.invocation_id)
                native_before = _native_snapshot(services, handle, backend)
                delivery_before = services.initial_deliveries.connection.execute(
                    """SELECT engine,fingerprint,payload,digest,status
                    FROM initial_invocation_deliveries WHERE invocation_id=?""",
                    (handle.invocation_id,),
                ).fetchall()

                resume_calls: list[bool] = []

                async def forbidden_resume(*_args: Any, **_kwargs: Any) -> Any:
                    resume_calls.append(True)
                    raise AssertionError("mismatched Task context reached native resume")

                monkeypatch.setattr(engine, "resume", forbidden_resume)
                message = {
                    "role": "ROLE_USER",
                    "messageId": "reject-foreign-task-context",
                    "taskId": handle.invocation_id,
                    "contextId": "foreign-context",
                    "parts": [{"text": "must be rejected before resume"}],
                }
                if surface == "jsonrpc":
                    response = await client.post(
                        "/a2a",
                        headers={"a2a-version": A2A_PROTOCOL_VERSION},
                        json={
                            "jsonrpc": "2.0",
                            "id": "foreign-context-resume",
                            "method": "SendMessage",
                            "params": {"message": message},
                        },
                    )
                    assert response.status_code == 200, response.text
                    assert "error" in response.json()
                else:
                    response = await client.post(
                        "/a2a/message:send",
                        headers={"A2A-Version": A2A_PROTOCOL_VERSION},
                        json={"message": message},
                    )
                    assert 400 <= response.status_code < 500, response.text
                    assert "error" in response.json()

                assert (
                    "context-mismatch-original" not in response.text and TOKEN not in response.text
                )
                assert "context" in response.text.lower()
                assert resume_calls == []
                assert _common_snapshot(services, handle.invocation_id) == common_before
                assert _native_snapshot(services, handle, backend) == native_before
                assert (
                    services.initial_deliveries.connection.execute(
                        """SELECT engine,fingerprint,payload,digest,status
                        FROM initial_invocation_deliveries WHERE invocation_id=?""",
                        (handle.invocation_id,),
                    ).fetchall()
                    == delivery_before
                )
                assert services.invocations.read_input(handle) == {
                    "secret": "context-mismatch-original"
                }
                assert not engine._active and not engine._execution_leases
                assert not engine._ownership_tasks
                assert len(receiver.calls) == len(receiver.rows()) == 1
                assert not services.model.calls
    finally:
        if handle is not None:
            await engine.cancel(handle, operation_id="context-mismatch-fixture-cleanup")
        await engine.shutdown()
        services.close()
