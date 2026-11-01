"""Actual pinned SDK stream driving, with no public capability advertisement."""

from __future__ import annotations

import json
import os

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.native_streams import NativeStreamLimits, NativeStreamLog
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow, generate_default_workflow

pytest.importorskip("langgraph.checkpoint.sqlite")
from open_workflow_agent_langgraph import LangGraphWorkflowEngine  # noqa: E402

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("observation", ["normal", "closed", "oversized"])
async def test_native_stream_driver_preserves_effects_and_hides_private_state(
    tmp_path, monkeypatch, backend, observation
):
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    config = RuntimeConfig()
    if datasource:
        config.persistence.datasource = datasource
    model = FakeModel({"reply": "x" * 200 if observation == "oversized" else "done"})
    services = RuntimeServices(config, model=model, database_root=None if datasource else tmp_path)
    engine = LangGraphWorkflowEngine()
    try:
        await engine.initialize(services)
        workflow = generate_default_workflow()
        workflow["do"] = [
            {"prepare": {"set": {"request": "coffee"}}},
            {"answer": {"call": "llm:1.0.0@default"}},
            {"finish": {"set": {"ok": True}}},
        ]
        plan = compile_workflow(workflow)
        handle = services.invocations.create(
            engine="langgraph",
            session_id="shared-session",
            user_id="correlation-user",
            workflow_name=plan.name,
            workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
        )
        log = NativeStreamLog(
            handle.invocation_id,
            NativeStreamLimits(max_event_bytes=320) if observation == "oversized" else None,
        )
        _, subscription = log.subscribe()
        if observation == "closed":
            log.close()
        original = engine.native.execute_plan

        async def observed(*args, **kwargs):
            kwargs["_stream_observer"] = log
            return await original(*args, **kwargs)

        monkeypatch.setattr(engine.native, "execute_plan", observed)
        result = await engine.invoke(plan, handle, {"start": "input"})
        assert result.status == "completed", result.error
        assert result.output == {"ok": True}
        assert len(model.calls) == 1
        assert engine.capabilities().streaming is False
        assert not any("stream" in name for name in engine.capabilities().native_extensions)
        if observation == "normal":
            frames, replay = log.subscribe()
            values = [json.loads(frame.payload) for frame in frames]
            assert {value["mode"] for value in values} == {"values", "updates"}
            assert [value["data"] for value in values if value["mode"] == "values"][-1] == {
                "ok": True
            }
            updates = [value for value in values if value["mode"] == "updates"]
            assert [value["task_reference"] for value in updates] == [
                "/do/0/prepare",
                "/do/1/answer",
                "/do/2/finish",
            ]
            assert all(
                set(value)
                == {"schema", "id", "invocation_id", "mode", "scope", "task_reference", "data"}
                for value in values
            )
            assert all(value["scope"] == "/do" for value in values)
            assert all(value["invocation_id"] == handle.invocation_id for value in values)
            replay.close()
        elif observation == "oversized":
            with pytest.raises(BufferError, match="frame_limit"):
                await subscription.receive(timeout=1)
            with pytest.raises(BufferError):
                log.subscribe()
        subscription.close()
        log.close()
    finally:
        await engine.shutdown()
        services.close()


async def test_private_observer_identity_is_checked_before_native_execution(services):
    from open_workflow_agent.errors import InvocationStateError

    engine = LangGraphWorkflowEngine()
    await engine.initialize(services)
    try:
        with pytest.raises(InvocationStateError, match="another invocation"):
            await engine.native.execute_plan(
                engine.executor,
                compile_workflow(),
                {},
                metadata={},
                invocation_id="one",
                _stream_observer=NativeStreamLog("two"),
            )
        assert services.model.calls == []
    finally:
        await engine.shutdown()
