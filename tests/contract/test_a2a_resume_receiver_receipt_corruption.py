"""A2A resume fails closed when a previously delivered receiver receipt is damaged."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from engine_cases import engine_cases
from open_workflow_agent.a2a import A2A_PROTOCOL_VERSION
from open_workflow_agent.api import create_app
from open_workflow_agent.effects import EffectIdentity
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
@pytest.mark.parametrize("damage", ["missing", "malformed"])
async def test_a2a_resume_rejects_damaged_receiver_receipt_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: Any,
    backend: str,
    surface: str,
    damage: str,
) -> None:
    """A completed outbound call cannot be replayed when its journal proof is damaged."""
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    _clock(monkeypatch, [1000.0])
    engine = engine_type()
    receiver = Receiver(tmp_path / "receiver.sqlite3")
    config = _config(_definition("1.0.0"), backend, a2a_transport=surface)
    _actual_native(engine, engine_name)
    services = _services(tmp_path, config, receiver)
    app = create_app(config=config, services=services, engine=engine)
    headers = {"Authorization": "Bearer " + TOKEN}
    handle: Any = None
    saved_receipt: tuple[str, str, str, str] | None = None
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                headers=headers,
            ) as client:
                started = await client.post(
                    "/v1/invoke", json={"input": {"secret": "receiver-receipt-secret"}}
                )
                assert started.status_code == 200 and started.json()["status"] == "waiting"
                handle = services.invocations.get(started.json()["invocation_id"])
                assert handle is not None and handle.owner_principal == PRINCIPAL
                assert len(receiver.calls) == len(receiver.rows()) == 1

                connection = services.effect_journal.connection
                rows = connection.execute(
                    "SELECT i.identity,i.payload,e.owner,e.status,e.payload "
                    "FROM workflow_effect_invocations i JOIN workflow_effects e "
                    "ON e.identity=i.identity WHERE i.invocation_id=?",
                    (handle.invocation_id,),
                ).fetchall()
                matches = [
                    row
                    for row in rows
                    if EffectIdentity.from_value(json.loads(row[1])).task_reference
                    == "/do/0/commit"
                ]
                assert len(matches) == 1
                matched = matches[0]
                saved_receipt = (matched[0], matched[2], matched[3], matched[4])
                if damage == "missing":
                    changed = connection.execute(
                        "DELETE FROM workflow_effects WHERE identity=?", (saved_receipt[0],)
                    )
                else:
                    changed = connection.execute(
                        "UPDATE workflow_effects SET payload=? WHERE identity=?",
                        ("{malformed-receiver-receipt", saved_receipt[0]),
                    )
                assert changed.rowcount == 1
                connection.commit()

                common_before = _common_snapshot(services, handle.invocation_id)
                native_before = _native_snapshot(services, handle, backend)
                message = {
                    "role": "ROLE_USER",
                    "messageId": f"reject-{damage}-receiver-receipt",
                    "taskId": handle.invocation_id,
                    **({"contextId": handle.session_id} if handle.session_id is not None else {}),
                    "parts": [{"text": "resume after receiver receipt damage"}],
                }
                if surface == "jsonrpc":
                    response = await client.post(
                        "/a2a",
                        headers={"a2a-version": A2A_PROTOCOL_VERSION},
                        json={
                            "jsonrpc": "2.0",
                            "id": "damaged-receiver-receipt-resume",
                            "method": "SendMessage",
                            "params": {"message": message},
                        },
                    )
                    assert response.status_code == 200, response.text
                    assert response.json()["error"]["code"] == -32000
                    assert response.json()["error"]["message"] == "task is not accepting input"
                else:
                    response = await client.post(
                        "/a2a/message:send",
                        headers={"A2A-Version": A2A_PROTOCOL_VERSION},
                        json={"message": message},
                    )
                    assert response.status_code == 409, response.text
                    assert response.json()["error"]["details"][0]["metadata"]["owaCode"] == (
                        "task_not_resumeable"
                    )

                assert "receiver-receipt-secret" not in response.text and TOKEN not in response.text
                assert _common_snapshot(services, handle.invocation_id) == common_before
                assert _native_snapshot(services, handle, backend) == native_before
                assert not engine._active and not engine._execution_leases
                assert len(receiver.calls) == len(receiver.rows()) == 1
                assert not services.model.calls
    finally:
        if saved_receipt is not None:
            connection = services.effect_journal.connection
            connection.execute(
                "INSERT INTO workflow_effects(identity,owner,status,payload) VALUES (?,?,?,?) "
                "ON CONFLICT(identity) DO UPDATE SET owner=excluded.owner,status=excluded.status,"
                "payload=excluded.payload",
                saved_receipt,
            )
            connection.commit()
        if handle is not None:
            await engine.cancel(handle, operation_id="receiver-receipt-fixture-cleanup")
        await engine.shutdown()
        services.close()
