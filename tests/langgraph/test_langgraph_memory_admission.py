"""Existing in-memory native evidence is inspected without creating checkpoint rows."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

pytest.importorskip("langgraph.graph")

from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.errors import InvocationStateError
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.timer_dispatcher import TimerDispatcher
from open_workflow_agent.workflow import compile_workflow
from open_workflow_agent_langgraph import LangGraphWorkflowEngine
from open_workflow_agent_langgraph.graph import LangGraphScopeExecutor


def _common_rows(services, handle):
    return tuple(
        services.invocations.connection.execute(statement, (handle.invocation_id,)).fetchall()
        for statement in (
            "SELECT * FROM invocations WHERE invocation_id=?",
            "SELECT * FROM invocation_ownership WHERE invocation_id=?",
            "SELECT * FROM recovery_invocation_definitions WHERE invocation_id=?",
            "SELECT * FROM workflow_effect_invocations WHERE invocation_id=?",
        )
    )


def _memory_rows(saver):
    return copy.deepcopy((dict(saver.storage), dict(saver.writes), dict(saver.blobs)))


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [False, True])
async def test_memory_continuation_presence_preflight_is_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: bool
) -> None:
    async def explicit(_dispatcher):
        return None

    monkeypatch.setattr(TimerDispatcher, "start", explicit)
    services = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
    engine = LangGraphWorkflowEngine()
    await engine.initialize(services)
    engine.native.database_path = None
    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "memory",
                "name": "continuation",
                "version": "1.0.0",
            },
            "do": [
                {"pause": {"wait": {"seconds": 600}}},
                {"finish": {"set": {"original": "${ $workflow.input }"}}},
            ],
        }
    )
    handle = services.invocations.create(
        engine=engine.engine_name,
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    try:
        waiting = await engine.invoke(plan, handle, {"original": True})
        assert waiting.status == "waiting"
        if missing:
            engine.native.checkpointer = InMemorySaver()
        saver = engine.native.checkpointer
        before, memory_before = _common_rows(services, handle), _memory_rows(saver)
        if missing:
            with pytest.raises(InvocationStateError) as failure:
                await engine.resume(handle, None, plan)
            assert failure.value.details["reason"] == "native_state_missing"
        else:
            await engine._preflight_native_continuation(handle)
        assert _common_rows(services, handle) == before
        assert _memory_rows(saver) == memory_before
        if not missing:
            resumed = await engine.resume(handle, None, plan)
            assert resumed.status == "completed"
            assert resumed.output == {"original": {"original": True}}
        assert not engine._execution_leases and not engine._active and not services.model.calls
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
async def test_memory_history_without_archive_cannot_be_readmitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def explicit(_dispatcher):
        return None

    monkeypatch.setattr(TimerDispatcher, "start", explicit)
    services = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
    engine = LangGraphWorkflowEngine()
    await engine.initialize(services)
    engine.native.database_path = None
    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "memory",
                "name": "unadmitted",
                "version": "1.0.0",
            },
            "do": [{"finish": {"set": {"executed": True}}}],
        }
    )
    handle = services.invocations.create(
        engine=engine.engine_name,
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    saver = engine.native.checkpointer
    driver = LangGraphScopeExecutor(plan, saver, handle.invocation_id)
    saver.put(driver.checkpoint_config("/do", 1), empty_checkpoint(), {"source": "input"}, {})
    before, memory_before = _common_rows(services, handle), _memory_rows(saver)
    try:
        assert await engine._native_initial_history_exists(handle)
        with pytest.raises(InvocationStateError) as failure:
            await engine.invoke(plan, handle, {})
        assert failure.value.details["reason"] == "definition_archive_missing"
        assert _common_rows(services, handle) == before
        assert _memory_rows(saver) == memory_before
        assert not engine._active and not engine._execution_leases and not services.model.calls
    finally:
        await engine.shutdown()
        services.close()
