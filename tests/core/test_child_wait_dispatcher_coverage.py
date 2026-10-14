"""Behavioral checks for bounded child-wait recovery dispatch."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest
from open_workflow_agent.child_wait_dispatcher import ChildWaitDispatcher
from open_workflow_agent.effects import EffectIdentity
from open_workflow_agent.errors import UnsupportedWorkflowFeature, WorkflowExecutionError


def _workflow_source() -> dict[str, Any]:
    return {
        "document": {
            "dsl": "1.0.3",
            "namespace": "wait-dispatch",
            "name": "child",
            "version": "1.0.0",
        },
        "do": [{"finish": {"set": {"done": True}}}],
    }


def _registration(parent: str = "parent") -> Any:
    identity = EffectIdentity(
        parent,
        "workflow-fingerprint",
        "/do/0/start",
        (("/do/0/start", 1),),
        "run:workflow",
    )
    return SimpleNamespace(identity=identity, workflow_source_json=json.dumps(_workflow_source()))


class _EmptyRows:
    def execute(self, statement: str, parameters: tuple[Any, ...] = ()) -> Any:
        return SimpleNamespace(fetchall=lambda: [])


@pytest.mark.asyncio
async def test_tick_dispatches_recovered_wait_through_resolved_workflow() -> None:
    registration = _registration()
    resolved: list[Any] = []
    dispatched: list[Any] = []

    async def dispatch_child_wait(selected: Any, load_plan: Any) -> None:
        dispatched.append((selected, await load_plan()))

    services = SimpleNamespace(
        child_waits=SimpleNamespace(
            pending_keys=lambda engine, *, now, after_key: ["wait-key"],
            registration_for_key=lambda key: registration if key == "wait-key" else None,
        ),
        children=SimpleNamespace(connection=_EmptyRows()),
        workflow_catalog=SimpleNamespace(
            resolve=lambda document: resolved.append(document) or "plan"
        ),
        config=SimpleNamespace(child_workflows=SimpleNamespace(max_concurrency=2)),
    )
    engine = SimpleNamespace(
        engine_name="portable",
        executor=None,
        _active=set(),
        _ownership_tasks={},
        dispatch_child_wait=dispatch_child_wait,
    )
    dispatcher = ChildWaitDispatcher(services, engine)

    await dispatcher.tick()
    await asyncio.gather(*tuple(dispatcher._workers.values()))

    assert resolved == [_workflow_source()["document"]]
    assert dispatched == [(registration, "plan")]
    assert dispatcher._cursor == "wait-key"
    assert dispatcher._workers == {}
    assert not dispatcher._errors


@pytest.mark.asyncio
async def test_tick_records_missing_registration_and_clears_exhausted_scan_cursor() -> None:
    services = SimpleNamespace(
        child_waits=SimpleNamespace(
            pending_keys=lambda engine, *, now, after_key: ["orphan-key"],
            registration_for_key=lambda key: None,
        ),
        children=SimpleNamespace(connection=_EmptyRows()),
        config=SimpleNamespace(child_workflows=SimpleNamespace(max_concurrency=1)),
    )
    engine = SimpleNamespace(
        engine_name="portable", executor=None, _active=set(), _ownership_tasks={}
    )
    dispatcher = ChildWaitDispatcher(services, engine)

    await dispatcher.tick()
    assert dispatcher._cursor == "orphan-key"
    assert len(dispatcher._errors) == 1
    assert isinstance(dispatcher._errors[0], WorkflowExecutionError)

    services.child_waits.pending_keys = lambda engine, *, now, after_key: []
    await dispatcher.tick()
    assert dispatcher._cursor is None
    assert dispatcher._cancel_cursor is None


@pytest.mark.asyncio
async def test_tick_skips_wait_owned_by_active_parent_and_enforces_capacity() -> None:
    first, second = _registration("ready-parent"), _registration("blocked-parent")
    registrations = {"first": first, "second": second}
    release = asyncio.Event()

    async def dispatch_child_wait(selected: Any, load_plan: Any) -> None:
        await release.wait()

    services = SimpleNamespace(
        child_waits=SimpleNamespace(
            pending_keys=lambda engine, *, now, after_key: ["first", "second"],
            registration_for_key=lambda key: registrations[key],
        ),
        children=SimpleNamespace(connection=_EmptyRows()),
        config=SimpleNamespace(child_workflows=SimpleNamespace(max_concurrency=1)),
    )
    engine = SimpleNamespace(
        engine_name="portable",
        executor=None,
        _active={"blocked-parent"},
        _ownership_tasks={},
        dispatch_child_wait=dispatch_child_wait,
    )
    dispatcher = ChildWaitDispatcher(services, engine)

    await dispatcher.tick()
    assert dispatcher._cursor == "first"
    assert set(dispatcher._workers) == {"ready-parent"}
    release.set()
    await asyncio.gather(*tuple(dispatcher._workers.values()))


@pytest.mark.asyncio
async def test_dispatch_failure_is_retained_and_worker_slot_is_released() -> None:
    registration = _registration()

    async def dispatch_child_wait(selected: Any, load_plan: Any) -> None:
        raise RuntimeError("native owner unavailable")

    services = SimpleNamespace(
        child_waits=SimpleNamespace(registration_for_key=lambda key: registration),
        workflow_catalog=SimpleNamespace(resolve=lambda document: "plan"),
        config=SimpleNamespace(child_workflows=SimpleNamespace(max_concurrency=1)),
    )
    engine = SimpleNamespace(
        engine_name="portable",
        _active=set(),
        _ownership_tasks={},
        dispatch_child_wait=dispatch_child_wait,
    )
    dispatcher = ChildWaitDispatcher(services, engine)

    await dispatcher.tick(run_key="wait-key")
    await asyncio.gather(*tuple(dispatcher._workers.values()))

    assert len(dispatcher._errors) == 1
    assert str(dispatcher._errors[0]) == "native owner unavailable"
    assert dispatcher._workers == {}


@pytest.mark.asyncio
async def test_cancellation_reconciliation_only_cancels_existing_same_engine_parents() -> None:
    batches = iter(
        [
            [
                ("binding-missing", "missing-parent"),
                ("binding-other-engine", "other-engine-parent"),
                ("binding-current", "current-parent"),
            ],
            [],
        ]
    )
    cancelled: list[Any] = []

    class Rows:
        def execute(self, statement: str, parameters: tuple[Any, ...] = ()) -> Any:
            assert "child_workflow_bindings" in statement
            return SimpleNamespace(fetchall=lambda: next(batches))

    parents = {
        "other-engine-parent": SimpleNamespace(engine="adk"),
        "current-parent": SimpleNamespace(engine="portable"),
    }

    async def cancel(parent: Any) -> None:
        cancelled.append(parent)

    services = SimpleNamespace(
        child_waits=SimpleNamespace(pending_keys=lambda engine, *, now, after_key: []),
        children=SimpleNamespace(connection=Rows()),
        invocations=SimpleNamespace(get=lambda parent_id: parents.get(parent_id)),
        config=SimpleNamespace(child_workflows=SimpleNamespace(max_concurrency=1)),
    )
    engine = SimpleNamespace(
        engine_name="portable",
        executor=None,
        _active=set(),
        _ownership_tasks={},
        _cancel_synchronous_children=cancel,
    )
    dispatcher = ChildWaitDispatcher(services, engine)

    await dispatcher.tick()
    assert cancelled == [parents["current-parent"]]
    assert dispatcher._cancel_cursor == "binding-current"
    assert len(dispatcher._errors) == 1
    assert str(dispatcher._errors[0]) == "child cancellation parent is absent"

    await dispatcher.tick()
    assert dispatcher._cancel_cursor is None


@pytest.mark.asyncio
async def test_stop_cancels_and_joins_a_child_wait_worker() -> None:
    registration = _registration()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def dispatch_child_wait(selected: Any, load_plan: Any) -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    services = SimpleNamespace(
        child_waits=SimpleNamespace(registration_for_key=lambda key: registration),
        workflow_catalog=SimpleNamespace(resolve=lambda document: "plan"),
        config=SimpleNamespace(child_workflows=SimpleNamespace(max_concurrency=1)),
    )
    engine = SimpleNamespace(
        engine_name="portable",
        _active=set(),
        _ownership_tasks={},
        dispatch_child_wait=dispatch_child_wait,
    )
    dispatcher = ChildWaitDispatcher(services, engine)

    await dispatcher.tick(run_key="wait-key")
    await started.wait()
    await dispatcher.stop()

    assert cancelled.is_set()
    assert dispatcher._workers == {}


@pytest.mark.asyncio
async def test_tick_skips_parent_with_existing_native_ownership_task() -> None:
    registration = _registration("owned-parent")
    services = SimpleNamespace(
        child_waits=SimpleNamespace(registration_for_key=lambda key: registration),
        config=SimpleNamespace(child_workflows=SimpleNamespace(max_concurrency=2)),
    )
    engine = SimpleNamespace(
        engine_name="portable",
        executor=None,
        _active=set(),
        _ownership_tasks={"owned-parent": object()},
    )
    dispatcher = ChildWaitDispatcher(services, engine)

    await dispatcher.tick(run_key="wait-key")
    assert dispatcher._workers == {}
    assert not dispatcher._errors


@pytest.mark.asyncio
async def test_dispatch_records_registration_lost_after_scan() -> None:
    registration = _registration()
    lookups = 0

    def lookup(key: str) -> Any:
        nonlocal lookups
        lookups += 1
        return registration if lookups == 1 else None

    services = SimpleNamespace(
        child_waits=SimpleNamespace(registration_for_key=lookup),
        config=SimpleNamespace(child_workflows=SimpleNamespace(max_concurrency=1)),
    )
    engine = SimpleNamespace(engine_name="portable", _active=set(), _ownership_tasks={})
    dispatcher = ChildWaitDispatcher(services, engine)

    await dispatcher.tick(run_key="wait-key")
    await asyncio.gather(*tuple(dispatcher._workers.values()))

    assert lookups == 2
    assert len(dispatcher._errors) == 1
    assert str(dispatcher._errors[0]) == "child wait registration is absent"


@pytest.mark.asyncio
async def test_dispatch_uses_sandbox_compiler_for_supported_feature_fallback(monkeypatch) -> None:
    from open_workflow_agent import child_wait_dispatcher as module

    registration = _registration()
    compiled: list[Any] = []
    dispatched: list[Any] = []

    async def resolve_fallback(source: Any, **kwargs: Any) -> str:
        compiled.append((source, kwargs))
        return "sandbox-compiled-plan"

    async def dispatch_child_wait(selected: Any, load_plan: Any) -> None:
        dispatched.append(await load_plan())

    monkeypatch.setattr(module, "resolve_and_compile_sandbox_workflow", resolve_fallback)

    def resolve(document: Any) -> Any:
        raise UnsupportedWorkflowFeature("requires admitted sandbox extension")

    services = SimpleNamespace(
        child_waits=SimpleNamespace(registration_for_key=lambda key: registration),
        workflow_catalog=SimpleNamespace(resolve=resolve),
        config=SimpleNamespace(
            sandbox=object(),
            workflow=SimpleNamespace(external_catalogs=[]),
            child_workflows=SimpleNamespace(max_concurrency=1),
        ),
        external_catalogs=object(),
        catalog=object(),
        grpc_enabled=False,
        role_function_calls=frozenset(),
    )
    engine = SimpleNamespace(
        engine_name="portable",
        _active=set(),
        _ownership_tasks={},
        dispatch_child_wait=dispatch_child_wait,
    )
    dispatcher = ChildWaitDispatcher(services, engine)

    await dispatcher.tick(run_key="wait-key")
    await asyncio.gather(*tuple(dispatcher._workers.values()))

    assert dispatched == ["sandbox-compiled-plan"]
    assert compiled[0][1]["sandbox"] is services.config.sandbox
    assert not dispatcher._errors
