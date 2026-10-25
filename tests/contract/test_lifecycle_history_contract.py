"""Actual native invocation history survives service restart on both stores."""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import httpx
import pytest
from engine_cases import engine_cases
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow

pytestmark = pytest.mark.usefixtures("explicit_workflow_delivery")
ENGINES = [case for case in engine_cases() if case[0] in {"adk", "langgraph"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("name,engine_type", ENGINES)
async def test_native_history_restart_preserves_original_ids_without_redispatch(
    tmp_path, backend, name, engine_type
):
    engine = engine_type()
    available = engine.native.available if name == "adk" else engine.native.task_nodes_available
    if not available:
        if os.getenv("OWA_TEST_REQUIRED_ENGINE") == name:
            pytest.fail("selected native history SDK is unavailable")
        pytest.skip("actual independent native SDK is required")
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and datasource is None:
        pytest.skip("actual PostgreSQL datasource is required")
    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "persistence": {"datasource": datasource},
            "observability": {"history": {"enabled": True}},
        }
    )
    calls = []

    async def peer(request):
        calls.append(request.headers["Idempotency-Key"])
        return httpx.Response(200, json={"ok": True})

    def services():
        runtime = RuntimeServices(
            config, model=FakeModel(), database_root=None if datasource else tmp_path
        )
        runtime.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(peer)))
        runtime.tools.protocols = runtime.protocols
        return runtime

    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "history",
                "name": uuid4().hex,
                "version": "1.0.0",
            },
            "do": [
                {
                    "once": {
                        "call": "http",
                        "with": {"method": "POST", "endpoint": "https://service.test/once"},
                    }
                },
                {"pause": {"wait": {"seconds": 300}}},
                {"done": {"set": {"ok": True}}},
            ],
        }
    )
    first = services()
    await engine.initialize(first)
    try:
        await asyncio.wait_for(first.recovery_definitions.prepare_profile(name), timeout=60)
        handle = first.invocations.create(
            engine=name,
            session_id=None,
            user_id=None,
            workflow_name=plan.name,
            workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
        )
        waiting = await engine.invoke(plan, handle, {"private_input": "must-not-persist"})
        assert waiting.status == "waiting", waiting.error
        retained = {
            event.id: event.as_dict()
            for event in first.lifecycle_events.snapshot(1000)
            if event.data.get("invocation_id") == handle.invocation_id
        }
        assert retained and len(calls) == 1
        assert "must-not-persist" not in repr(retained)
    finally:
        await engine.shutdown()
        first.close()
    restored, next_engine = services(), engine_type()
    await next_engine.initialize(restored)
    try:
        before = {
            event.id: event.as_dict()
            for event in restored.lifecycle_events.snapshot(1000)
            if event.id in retained
        }
        assert before == retained
        persisted = restored.invocations.get(handle.invocation_id)
        assert persisted is not None
        result = await next_engine.resume(persisted, {"ignored": True}, plan)
        assert result.status == "completed" and result.output == {"ok": True}, result.error
        final = [
            event.as_dict()
            for event in restored.lifecycle_events.snapshot(1000)
            if event.data.get("invocation_id") == handle.invocation_id
        ]
        assert any(event["data"]["event_type"] == "WorkflowCompleted" for event in final)
        assert all(event in final for event in retained.values())
        assert len(calls) == 1 and restored.model.calls == []
        assert "must-not-persist" not in repr(final)
    finally:
        await next_engine.shutdown()
        restored.close()
