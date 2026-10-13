"""Detached admission respects recovery, fair scans and ordered shutdown."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from engine_cases import engine_cases
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
    compile_workflow,
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
        return [item.key for item in remaining[:1]]

    def get(identity):
        visited.append(identity)
        return SimpleNamespace(output={"child_id": identity.key})

    services = SimpleNamespace(
        children=SimpleNamespace(
            pending_job_keys=page,
            job_identity=lambda key: next(item for item in identities if item.key == key),
        ),
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


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("damage", ["malformed-index", "missing-index", "changed-index"])
async def test_corrupt_child_index_cannot_block_later_valid_durable_delivery(
    tmp_path, monkeypatch: pytest.MonkeyPatch, engine_name: str, engine_type: Any, damage: str
) -> None:
    async def no_automatic_delivery(_dispatcher: TimerDispatcher) -> None:
        return None

    monkeypatch.setattr(TimerDispatcher, "start", no_automatic_delivery)
    services = RuntimeServices(
        RuntimeConfig(), model=FakeModel({"delivered": True}), database_root=tmp_path
    )
    engine = engine_type()
    document = {"dsl": "1.0.3", "namespace": "child-delivery-guards", "version": "1.0.0"}
    parent_plan = compile_workflow(
        {"document": {**document, "name": "parent"}, "do": [{"finish": {"set": {"done": True}}}]}
    )
    child_plan = services.workflow_catalog.register(
        {
            "document": {**document, "name": "child"},
            "do": [{"action": {"call": "llm:1.0.0@default", "with": {"prompt": "${ .name }"}}}],
        }
    )
    parent = services.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id="correlation",
        owner_principal="principal",
        workflow_name=parent_plan.name,
        workflow_version=parent_plan.version,
        workflow_fingerprint=parent_plan.fingerprint,
    )
    children = []
    dispatcher = ChildDispatcher(services, engine)
    try:
        await engine.initialize(services)
        parent._execution_lease = services.execution_ownership.acquire(parent, seconds=30)
        try:
            decisions = []
            for index in (1, 2):
                identity = EffectIdentity(
                    parent.invocation_id,
                    parent.workflow_fingerprint,
                    f"/do/{index}/child",
                    (("/do", 1),),
                    "run:workflow",
                )
                input_data = {"name": f"child-{index}"}
                child = services.children.reserve(identity, parent, child_plan, input_data)
                children.append(child)
                services.children.enqueue(identity, parent, child_plan, input_data, {})
                decisions.append(replace(identity, operation="policy:detached-child:v1"))
            services.invocations.update(parent, status="completed", output={})
        finally:
            services.execution_ownership.release(parent._execution_lease)
            parent._execution_lease = None
        damaged, valid = sorted(decisions, key=lambda identity: identity.key)
        corrupt_receipt = services.effect_journal.get(damaged)
        corrupt_child = services.invocations.get(corrupt_receipt.output["child_id"])
        valid_receipt = services.effect_journal.get(valid)
        valid_child = services.invocations.get(valid_receipt.output["child_id"])
        corrupt_before = corrupt_child._persisted_payload
        valid_prompt = services.invocations.read_input(valid_child)["name"]
        connection = services.children.connection
        if damage == "missing-index":
            connection.execute(
                "DELETE FROM workflow_effect_invocations WHERE identity=?", (damaged.key,)
            )
        else:
            payload = (
                "{"
                if damage == "malformed-index"
                else json.dumps(
                    {
                        "invocation_id": damaged.invocation_id,
                        "workflow_fingerprint": damaged.workflow_fingerprint,
                        "task_reference": damaged.task_reference,
                        "occurrence": [["/do", 99]],
                        "operation": damaged.operation,
                    }
                )
            )
            connection.execute(
                "UPDATE workflow_effect_invocations SET payload=? WHERE identity=?",
                (payload, damaged.key),
            )
        connection.commit()
        damaged_index = connection.execute(
            "SELECT payload FROM workflow_effect_invocations WHERE identity=?", (damaged.key,)
        ).fetchone()
        with pytest.raises(WorkflowExecutionError, match="identity index"):
            services.children.pending_jobs(engine_name)

        # Use actual database keyset pages of one candidate. Corrupt evidence
        # must advance the scan before the valid job becomes visible.
        read_page = services.children.pending_job_keys

        def one_candidate(engine, **kwargs):
            return read_page(engine, limit=1, **kwargs)

        monkeypatch.setattr(services.children, "pending_job_keys", one_candidate)
        await dispatcher.tick()
        assert dispatcher._cursor == damaged.key
        assert len(dispatcher._errors) == 1
        assert services.model.calls == []
        assert not dispatcher._workers
        await dispatcher.tick()
        async with asyncio.timeout(30):
            while dispatcher._workers:
                await asyncio.sleep(0.005)
        assert dispatcher._cursor == valid.key
        assert len(dispatcher._errors) == 1
        assert services.model.calls == [{"prompt": valid_prompt}]
        delivered = services.invocations.get(valid_child.invocation_id)
        assert delivered.status == "completed" and delivered.output == {"delivered": True}
        assert (
            services.invocations.get(corrupt_child.invocation_id)._persisted_payload
            == corrupt_before
        )
        assert (
            connection.execute(
                "SELECT payload FROM workflow_effect_invocations WHERE identity=?", (damaged.key,)
            ).fetchone()
            == damaged_index
        )
        assert services.effect_journal.get(valid) == valid_receipt
        await dispatcher.tick()
        assert dispatcher._cursor is None
        assert services.model.calls == [{"prompt": valid_prompt}]
    finally:
        await dispatcher.stop()
        for child in children:
            saved = services.invocations.get(child.invocation_id)
            if saved is not None and saved.status in {"running", "waiting"}:
                await engine.cancel(saved)
        await engine.shutdown()
        services.close()


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
