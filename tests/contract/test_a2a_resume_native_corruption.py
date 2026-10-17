"""Public A2A resume rejects corrupted native LangGraph checkpoints."""

from __future__ import annotations

import copy
import importlib
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from open_workflow_agent.a2a import A2A_PROTOCOL_VERSION
from open_workflow_agent.api import create_app
from open_workflow_agent_langgraph import LangGraphWorkflowEngine
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
async def test_a2a_resume_rejects_corrupt_native_checkpoint_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    surface: str,
) -> None:
    """The authenticated A2A route must validate the native wait before resuming."""
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    _clock(monkeypatch, [1000.0])
    receiver = Receiver(tmp_path / "receiver.sqlite3")
    config = _config(_definition("1.0.0"), backend, a2a_transport=surface)
    services = _services(tmp_path, config, receiver)
    engine = LangGraphWorkflowEngine()
    if not engine.native.task_nodes_available:
        pytest.skip("independent native LangGraph checkpoint environment is required")
    _actual_native(engine, "langgraph")
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
                    "/v1/invoke", json={"input": {"secret": "retained-a2a-secret"}}
                )
                assert started.status_code == 200 and started.json()["status"] == "waiting"
                handle = services.invocations.get(started.json()["invocation_id"])
                assert handle is not None and handle.owner_principal == PRINCIPAL

                async def corrupt(saver: Any) -> None:
                    from open_workflow_agent_langgraph.graph import LangGraphScopeExecutor

                    driver = LangGraphScopeExecutor(app.state.plan, saver, handle.invocation_id)
                    saved = await saver.aget_tuple(driver.checkpoint_config("/do", 1))
                    assert saved is not None
                    checkpoint = copy.deepcopy(saved.checkpoint)
                    channels = checkpoint["channel_values"]
                    snapshot = json.loads(channels["snapshot"])
                    snapshot["variables"]["workflow"]["id"] = "forged-workflow-sentinel"
                    channels["snapshot"] = json.dumps(snapshot)
                    thread = saved.config["configurable"]["thread_id"]
                    namespace = saved.config["configurable"].get("checkpoint_ns", "")
                    if backend == "postgresql":
                        json_types = importlib.import_module("psycopg.types.json")
                        updated = await saver.conn.execute(
                            "UPDATE checkpoints SET checkpoint = "
                            "jsonb_set(checkpoint, ARRAY['channel_values', %s], %s, false) "
                            "WHERE thread_id = %s AND checkpoint_ns = %s AND checkpoint_id = %s",
                            (
                                "snapshot",
                                json_types.Jsonb(channels["snapshot"]),
                                thread,
                                namespace,
                                checkpoint["id"],
                            ),
                        )
                    else:
                        encoding, payload = saver.serde.dumps_typed(checkpoint)
                        updated = await saver.conn.execute(
                            "UPDATE checkpoints SET type = ?, checkpoint = ? "
                            "WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?",
                            (encoding, payload, thread, namespace, checkpoint["id"]),
                        )
                    assert updated.rowcount == 1
                    await updated.close()
                    await saver.conn.commit()
                    altered = await saver.aget_tuple(saved.config)
                    assert altered is not None
                    assert altered.checkpoint["channel_values"]["snapshot"] == channels["snapshot"]

                await engine.native._with_saver(corrupt)
                before = _common_snapshot(services, handle.invocation_id)
                native_before = _native_snapshot(services, handle, backend)
                message = {
                    "role": "ROLE_USER",
                    "messageId": "reject-corrupt-native-checkpoint",
                    "taskId": handle.invocation_id,
                    **({"contextId": handle.session_id} if handle.session_id is not None else {}),
                    "parts": [{"text": "resume corrupted native state"}],
                }
                if surface == "jsonrpc":
                    response = await client.post(
                        "/a2a",
                        headers={"a2a-version": A2A_PROTOCOL_VERSION},
                        json={
                            "jsonrpc": "2.0",
                            "id": "corrupt-native-resume",
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

                assert "retained-a2a-secret" not in response.text
                assert (
                    "forged-workflow-sentinel" not in response.text and TOKEN not in response.text
                )
                assert _common_snapshot(services, handle.invocation_id) == before
                assert _native_snapshot(services, handle, backend) == native_before
                assert len(receiver.calls) == len(receiver.rows()) == 1
                assert not services.model.calls
    finally:
        await engine.shutdown()
        services.close()
