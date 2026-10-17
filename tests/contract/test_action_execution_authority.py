"""Actual adapters reject stale action admission while retaining dispatched truth."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import httpx
import pytest
from engine_cases import engine_cases
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.errors import InvocationStateConflict
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("boundary", ["before_intent", "after_dispatch"])
async def test_expired_execution_cannot_admit_an_action_but_keeps_its_known_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: type[Any],
    backend: str,
    boundary: str,
) -> None:
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    config = (
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}})
        if datasource
        else RuntimeConfig()
    )
    services = RuntimeServices(
        config, model=FakeModel(), database_root=None if datasource else tmp_path
    )
    engine = engine_type()
    engine._execution_ownership_seconds = 600
    calls: list[bytes] = []
    identity = None

    def expire() -> None:
        services.execution_ownership.connection.execute(
            "UPDATE invocation_ownership SET expires_at = 1 WHERE invocation_id = ?",
            (handle.invocation_id,),
        )
        services.execution_ownership.connection.commit()

    async def request(message: httpx.Request) -> httpx.Response:
        calls.append(message.content)
        expire()
        return httpx.Response(200, json={"accepted": True})

    services.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(request)))
    services.tools.protocols = services.protocols
    real_begin = services.effect_journal.begin

    def begin(action: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal identity
        identity = action
        if boundary == "before_intent":
            expire()
        return real_begin(action, *args, **kwargs)

    monkeypatch.setattr(services.effect_journal, "begin", begin)
    try:
        await engine.initialize(services)
        plan = compile_workflow(
            {
                "document": {
                    "dsl": "1.0.3",
                    "namespace": "test",
                    "name": "action-authority",
                    "version": "1.0.0",
                },
                "do": [
                    {
                        "action": {
                            "call": "http",
                            "with": {
                                "method": "POST",
                                "endpoint": "https://service.test/action",
                                "body": "${ . }",
                            },
                        }
                    }
                ],
            }
        )
        handle = services.invocations.create(
            engine=engine_name,
            session_id=None,
            user_id=None,
            workflow_name=plan.name,
            workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
        )
        with pytest.raises(InvocationStateConflict):
            await asyncio.wait_for(engine.invoke(plan, handle, {"request": True}), 20)
        assert identity is not None
        receipt = services.effect_journal.get(identity)
        if boundary == "before_intent":
            assert not calls and receipt is None
        else:
            assert len(calls) == 1
            assert receipt is not None and receipt.status == "completed"
            assert receipt.output == {"accepted": True}
        saved = services.invocations.get(handle.invocation_id)
        assert saved is not None and saved.status == "running" and saved.error is None
        assert not engine._active and not engine._execution_leases and not engine._ownership_tasks
    finally:
        await engine.shutdown()
        services.close()
