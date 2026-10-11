"""Both native engines reconcile saved progress with common action receipts."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.errors import InvocationStateError
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow
from open_workflow_agent_adk import AdkWorkflowEngine
from open_workflow_agent_langgraph import LangGraphWorkflowEngine


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "engine_name,engine_type", [("adk", AdkWorkflowEngine), ("langgraph", LangGraphWorkflowEngine)]
)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("scope", ["root", "nested"])
@pytest.mark.parametrize("outcome", ["value", "null", "fault"])
@pytest.mark.parametrize("damage", ["clean", "missing", "changed", "pending"])
async def test_native_progress_requires_unchanged_common_action_receipts(
    tmp_path: Path,
    engine_name: str,
    engine_type: Any,
    backend: str,
    scope: str,
    outcome: str,
    damage: str,
) -> None:
    engine = engine_type()
    available = (
        engine.native.available if engine_name == "adk" else engine.native.task_nodes_available
    )
    if not available:
        pytest.skip("independent native engine environment is required")
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    config = (
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}})
        if datasource
        else RuntimeConfig()
    )
    calls: list[str] = []

    async def request(message: httpx.Request) -> httpx.Response:
        calls.append(message.url.path)
        if message.url.path == "/once" and outcome == "fault":
            return httpx.Response(503, json={"rejected": True})
        return httpx.Response(
            200,
            content=b"null" if outcome == "null" else b'{"accepted":true}',
            headers={"Content-Type": "application/json"},
        )

    def services() -> RuntimeServices:
        result = RuntimeServices(
            config, model=FakeModel(), database_root=None if datasource else tmp_path
        )
        result.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(request)))
        result.tools.protocols = result.protocols
        return result

    body = [
        {
            "remember": {
                "try": [
                    {
                        "action": {
                            "call": "http",
                            "with": {"method": "POST", "endpoint": "https://service.test/once"},
                        }
                    }
                ],
                "catch": {"do": [{"handled": {"set": {"handled": True}}}]},
            }
        },
        {"pause": {"wait": {"seconds": 300}}},
        {
            "next": {
                "call": "http",
                "with": {"method": "POST", "endpoint": "https://service.test/next"},
            }
        },
    ]
    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "test",
                "name": "receipt-consistency",
                "version": "1.0.0",
            },
            "do": body if scope == "root" else [{"outer": {"do": body}}],
        }
    )
    first = services()
    existing = {
        row[0]
        for row in first.effect_journal.connection.execute(
            "SELECT identity FROM workflow_effects"
        ).fetchall()
    }
    await engine.initialize(first)
    handle = first.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    running = asyncio.create_task(engine.invoke(plan, handle, {"original": True}))
    receipt_key = ""
    try:
        async with asyncio.timeout(20):
            while handle.status != "waiting":
                if running.done():
                    pytest.fail(str(running.result()))
                await asyncio.sleep(0.005)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        assert calls == ["/once"]
        keys = {
            row[0]
            for row in first.effect_journal.connection.execute(
                "SELECT identity FROM workflow_effects"
            ).fetchall()
        } - existing
        assert len(keys) == 1
        receipt_key = keys.pop()
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()
        first.close()

    restarted = services()
    recovered = engine_type()
    await recovered.initialize(restarted)
    loaded = restarted.invocations.get(handle.invocation_id)
    assert loaded is not None and loaded.status == "waiting"
    connection = restarted.effect_journal.connection
    try:
        row = connection.execute(
            "SELECT payload FROM workflow_effects WHERE identity = ?", (receipt_key,)
        ).fetchone()
        assert row is not None
        payload = json.loads(row[0])
        assert payload["status"] == ("faulted" if outcome == "fault" else "completed")
        if outcome == "null":
            assert payload["output"] is None
        if damage == "missing":
            connection.execute("DELETE FROM workflow_effects WHERE identity = ?", (receipt_key,))
        elif damage in {"changed", "pending"}:
            if damage == "pending":
                payload.update(status="pending", output=None, error=None, error_status_code=None)
            elif outcome == "fault":
                payload["error"]["message"] = "private-receipt-sentinel"
            else:
                payload["output"] = {"private-receipt-sentinel": True}
            connection.execute(
                "UPDATE workflow_effects SET payload = ?, status = ? WHERE identity = ?",
                (json.dumps(payload), payload["status"], receipt_key),
            )
        connection.commit()
        before = loaded._persisted_payload
        if damage == "clean":
            result = await asyncio.wait_for(recovered.resume(loaded, {}, plan), 20)
            assert result.status == "completed" and calls == ["/once", "/next"]
        else:
            with pytest.raises(InvocationStateError) as failure:
                await asyncio.wait_for(recovered.resume(loaded, {}, plan), 20)
            assert "private-receipt-sentinel" not in json.dumps(failure.value.as_dict())
            assert calls == ["/once"]
            saved = restarted.invocations.get(handle.invocation_id)
            assert saved is not None and saved._persisted_payload == before
            assert loaded._persisted_payload == before and loaded.status == "waiting"
    finally:
        await recovered.shutdown()
        restarted.close()
