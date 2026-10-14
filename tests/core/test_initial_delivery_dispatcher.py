"""Fair initial-delivery polling fails closed around authority and concurrency."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from open_workflow_agent.errors import WorkflowExecutionError
from open_workflow_agent.initial_delivery_dispatcher import InitialDeliveryDispatcher


class _PendingDeliveries:
    def __init__(self, pending: list[str], binding: object | None = None) -> None:
        self.pending = pending
        self.binding = binding or SimpleNamespace(skill_id="skill")

    def pending_ids(self, _engine: str, *, after_id: str | None) -> list[str]:
        if after_id is None:
            return self.pending
        return [item for item in self.pending if item > after_id]

    def get(self, _handle: object) -> object:
        return self.binding


@pytest.mark.asyncio
async def test_tick_records_authority_rejection_without_dispatching() -> None:
    handle = SimpleNamespace(invocation_id="inv-1", status="running")
    deliveries = _PendingDeliveries(["inv-1"])
    services = SimpleNamespace(
        initial_deliveries=deliveries,
        invocations=SimpleNamespace(get=lambda _id: handle),
        resolve_original_definition=lambda _handle: object(),
        config=SimpleNamespace(execution_timing=SimpleNamespace(max_concurrency=1)),
    )

    class Engine:
        engine_name = "langgraph"
        _ownership_tasks: dict[str, object] = {}

        async def dispatch_initial_delivery(self, *_args: object) -> None:
            raise AssertionError("unauthorized delivery must not dispatch")

    dispatcher = InitialDeliveryDispatcher(services, Engine())
    dispatcher._authorize = lambda *_args: False
    await dispatcher.tick()

    assert len(dispatcher._errors) == 1
    assert isinstance(dispatcher._errors[0], WorkflowExecutionError)
    assert dispatcher._workers == {}


@pytest.mark.asyncio
async def test_empty_page_resets_poll_cursor_and_terminal_delivery_cleans_worker() -> None:
    handle = SimpleNamespace(invocation_id="inv-2", status="completed")
    deliveries = _PendingDeliveries([])
    services = SimpleNamespace(
        initial_deliveries=deliveries,
        invocations=SimpleNamespace(get=lambda _id: handle),
        config=SimpleNamespace(
            execution_timing=SimpleNamespace(max_concurrency=1, poll_interval_seconds=60)
        ),
    )
    dispatched: list[str] = []

    class Engine:
        engine_name = "langgraph"
        _ownership_tasks: dict[str, object] = {}

        async def dispatch_initial_delivery(self, value: object, _authorize: object) -> None:
            dispatched.append(str(value.invocation_id))

    dispatcher = InitialDeliveryDispatcher(services, Engine())
    dispatcher._cursor = "previous-page"
    await dispatcher.tick()
    assert dispatcher._cursor is None

    deliveries.pending = ["inv-2"]
    await dispatcher.tick()
    await asyncio.gather(*tuple(dispatcher._workers.values()))
    assert dispatched == ["inv-2"]
    assert dispatcher._workers == {}

    deliveries.pending = []
    await dispatcher.start(lambda *_args: True)
    await asyncio.sleep(0)
    assert dispatcher._task is not None
    await dispatcher.stop()
    assert dispatcher._task is None
    assert dispatcher._authorize is None


@pytest.mark.asyncio
async def test_tick_fails_closed_for_absent_binding_or_invocation() -> None:
    handle = SimpleNamespace(invocation_id="inv-missing", status="running")
    deliveries = _PendingDeliveries(["inv-missing"])
    deliveries.binding = None
    services = SimpleNamespace(
        initial_deliveries=deliveries,
        invocations=SimpleNamespace(get=lambda _id: handle),
        resolve_original_definition=lambda _handle: object(),
        config=SimpleNamespace(execution_timing=SimpleNamespace(max_concurrency=1)),
    )

    class Engine:
        engine_name = "langgraph"
        _ownership_tasks: dict[str, object] = {}

        async def dispatch_initial_delivery(self, *_args: object) -> None:
            raise AssertionError("missing binding must prevent delivery")

    dispatcher = InitialDeliveryDispatcher(services, Engine())
    dispatcher._authorize = lambda *_args: True
    await dispatcher.tick()
    assert len(dispatcher._errors) == 1
    assert "binding is absent" in str(dispatcher._errors[0])

    deliveries = _PendingDeliveries(["inv-missing"])
    services.initial_deliveries = deliveries
    services.invocations.get = lambda _id: None
    dispatcher = InitialDeliveryDispatcher(services, Engine())
    await dispatcher.tick()
    assert len(dispatcher._errors) == 1
    assert "invocation is absent" in str(dispatcher._errors[0])


@pytest.mark.asyncio
async def test_dispatch_worker_records_vanished_invocation_and_engine_failure() -> None:
    handle = SimpleNamespace(invocation_id="inv-dispatch", status="completed")
    lookups: list[object | None] = [handle, None]
    services = SimpleNamespace(
        initial_deliveries=_PendingDeliveries(["inv-dispatch"]),
        invocations=SimpleNamespace(get=lambda _id: lookups.pop(0)),
        config=SimpleNamespace(execution_timing=SimpleNamespace(max_concurrency=1)),
    )

    class Engine:
        engine_name = "langgraph"
        _ownership_tasks: dict[str, object] = {}

        async def dispatch_initial_delivery(self, *_args: object) -> None:
            raise WorkflowExecutionError("engine delivery failed")

    dispatcher = InitialDeliveryDispatcher(services, Engine())
    await dispatcher.tick()
    await asyncio.gather(*tuple(dispatcher._workers.values()))
    assert dispatcher._workers == {}
    assert len(dispatcher._errors) == 1
    assert "invocation is absent" in str(dispatcher._errors[0])

    lookups[:] = [handle, handle]
    dispatcher._cursor = None
    await dispatcher.tick()
    await asyncio.gather(*tuple(dispatcher._workers.values()))
    assert dispatcher._workers == {}
    assert len(dispatcher._errors) == 2
    assert "engine delivery failed" in str(dispatcher._errors[-1])


@pytest.mark.asyncio
async def test_tick_respects_capacity_and_poll_loop_records_errors() -> None:
    handle = SimpleNamespace(invocation_id="inv-waiting", status="completed")
    deliveries = _PendingDeliveries(["inv-waiting"])
    services = SimpleNamespace(
        initial_deliveries=deliveries,
        invocations=SimpleNamespace(get=lambda _id: handle),
        config=SimpleNamespace(
            execution_timing=SimpleNamespace(max_concurrency=1, poll_interval_seconds=0.001)
        ),
    )

    class Engine:
        engine_name = "langgraph"
        _ownership_tasks: dict[str, object] = {}

        async def dispatch_initial_delivery(self, *_args: object) -> None:
            raise AssertionError("at-capacity polling must not dispatch another worker")

    dispatcher = InitialDeliveryDispatcher(services, Engine())
    blocker = asyncio.create_task(asyncio.Event().wait())
    dispatcher._workers["already-running"] = blocker
    await dispatcher.tick()
    assert dispatcher._cursor is None
    await dispatcher.stop()

    async def fail_poll() -> None:
        raise RuntimeError("temporary poll failure")

    dispatcher.tick = fail_poll
    await dispatcher.start(lambda *_args: True)
    for _ in range(20):
        if dispatcher._errors:
            break
        await asyncio.sleep(0.001)
    await dispatcher.stop()
    assert dispatcher._errors
    assert isinstance(dispatcher._errors[0], RuntimeError)
