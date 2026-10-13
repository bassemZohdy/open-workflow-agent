"""Detached admission respects recovery, fair scans and ordered shutdown."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.child_dispatcher import ChildDispatcher
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.effects import EffectIdentity
from open_workflow_agent.errors import WorkflowExecutionError
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.timer_dispatcher import TimerDispatcher
from open_workflow_agent.workflow import (
    _RECONCILE_EXPIRED_RETRY,
    ExecutionState,
    WorkflowExecutor,
)


async def test_expired_retry_cannot_admit_a_new_detached_child(tmp_path):
    services = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
    services.workflow_runner = AsyncMock()
    services.workflow_catalog.register(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "guards",
                "name": "child",
                "version": "1.0.0",
            },
            "do": [{"finish": {"set": {"done": True}}}],
        }
    )
    state = ExecutionState(
        data={},
        variables={"_task_reference": "/do/0/start"},
        metadata={"invocation_id": "parent", "_workflow_fingerprint": "fingerprint"},
        execution_path=(("/do", 1),),
    )
    token = _RECONCILE_EXPIRED_RETRY.set(True)
    try:
        with pytest.raises(WorkflowExecutionError, match="retry budget expired"):
            await WorkflowExecutor(services.catalog, services=services)._run_subworkflow(
                {
                    "await": False,
                    "workflow": {"namespace": "guards", "name": "child", "version": "1.0.0"},
                },
                state,
            )
        for table in ("child_workflow_bindings", "detached_child_jobs", "child_workflow_budgets"):
            assert (
                services.children.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                == 0
            )
        services.workflow_runner.assert_not_awaited()
        assert services.model.calls == []
    finally:
        _RECONCILE_EXPIRED_RETRY.reset(token)
        services.close()


async def test_child_scan_advances_past_an_unavailable_prefix():
    identities = sorted(
        [
            EffectIdentity(
                "parent", "fp", f"/do/{i}/child", (("/do", 1),), "policy:detached-child:v1"
            )
            for i in range(3)
        ],
        key=lambda item: item.key,
    )
    visited = []

    def page(engine, *, job_key, after_key):
        remaining = [item for item in identities if after_key is None or item.key > after_key]
        return remaining[:1]

    def get(identity):
        visited.append(identity)
        return SimpleNamespace(output={"child_id": identity.key})

    services = SimpleNamespace(
        children=SimpleNamespace(pending_jobs=page),
        config=RuntimeConfig(),
        effect_journal=SimpleNamespace(get=get),
    )
    engine = SimpleNamespace(
        engine_name="adk", _active={item.key for item in identities}, _ownership_tasks={}
    )
    dispatcher = ChildDispatcher(services, engine)
    for _ in range(4):
        await dispatcher.tick()
    assert visited == identities
    assert dispatcher._cursor is None


async def test_timer_shutdown_stops_the_poller_before_draining_children():
    children_draining = asyncio.Event()
    poller_finished = asyncio.Event()

    async def poll():
        try:
            await asyncio.Event().wait()
        finally:
            poller_finished.set()

    async def stop_children():
        children_draining.set()
        assert poller_finished.is_set()

    dispatcher = TimerDispatcher(SimpleNamespace(), SimpleNamespace(), clock=lambda: 0)
    dispatcher.children.stop = stop_children
    dispatcher._task = asyncio.create_task(poll())
    await asyncio.sleep(0)
    await dispatcher.stop()
    assert children_draining.is_set() and dispatcher._task is None
