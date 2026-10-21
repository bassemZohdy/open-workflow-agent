"""Recovery rejects contradictory actual SDK input events for one invocation."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.errors import InvocationStateError
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow
from open_workflow_agent_adk import AdkWorkflowEngine


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize(
    "damage", ["clean", "matching-repeat", "changed-input", "malformed-json", "duplicate-keys"]
)
async def test_adk_all_native_input_events_match_the_bound_input_before_resume(
    tmp_path: Path, backend: str, damage: str
) -> None:
    engine = AdkWorkflowEngine()
    if not engine.native.available:
        pytest.skip("independent native ADK environment is required")
    from google.adk.events import Event
    from google.genai import types

    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    config = (
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}})
        if datasource
        else RuntimeConfig()
    )
    model = FakeModel({"done": True})

    def services() -> RuntimeServices:
        return RuntimeServices(config, model=model, database_root=None if datasource else tmp_path)

    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "test",
                "name": "native-event-consistency",
                "version": "1.0.0",
            },
            "do": [
                {"pause": {"wait": {"seconds": 300}}},
                {"effect": {"call": "agent:1.0.0@default"}},
            ],
        }
    )
    first = services()
    await engine.initialize(first)
    handle = first.invocations.create(
        engine="adk",
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    original = {"original": True}
    running = asyncio.create_task(engine.invoke(plan, handle, original))
    try:
        waiting = await asyncio.wait_for(asyncio.shield(running), timeout=20)
        assert waiting.status == "waiting", waiting.error
        assert handle.invocation_id not in engine._active
        assert handle.invocation_id not in engine._ownership_tasks
        assert handle._execution_lease is None
        assert first.invocations.read_input(handle) == original
        assert model.calls == []
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()
        first.close()

    restarted = services()
    recovered = AdkWorkflowEngine()
    await recovered.initialize(restarted)
    loaded = restarted.invocations.get(handle.invocation_id)
    assert loaded is not None and loaded.status == "waiting"
    sessions: Any = None
    try:
        if damage != "clean":
            sessions = recovered.native._session_service(restarted.engine_database_path("adk"))
            session = await sessions.get_session(
                app_name="open-workflow-agent", user_id="owa-user", session_id=handle.session_id
            )
            assert session is not None
            content = {
                "matching-repeat": json.dumps(original),
                "changed-input": json.dumps({"private-event-sentinel": True}),
                "malformed-json": "private-event-sentinel",
                "duplicate-keys": '{"original":false,"original":true}',
            }[damage]
            await sessions.append_event(
                session=session,
                event=Event(
                    invocation_id=handle.engine_execution_reference,
                    author="user",
                    content=types.Content(role="user", parts=[types.Part(text=content)]),
                ),
            )
            await recovered.native._close_sessions(sessions)
            sessions = None
        before = loaded._persisted_payload
        if damage in {"clean", "matching-repeat"}:
            result = await asyncio.wait_for(recovered.resume(loaded, {}, plan), 20)
            assert result.status == "completed" and len(model.calls) == 1
        else:
            with pytest.raises(InvocationStateError) as failure:
                await asyncio.wait_for(recovered.resume(loaded, {}, plan), 20)
            assert "private-event-sentinel" not in json.dumps(failure.value.as_dict())
            assert model.calls == []
            saved = restarted.invocations.get(handle.invocation_id)
            assert saved is not None and saved._persisted_payload == before
            assert loaded._persisted_payload == before and loaded.status == "waiting"
    finally:
        if sessions is not None:
            await recovered.native._close_sessions(sessions)
        await recovered.shutdown()
        restarted.close()
