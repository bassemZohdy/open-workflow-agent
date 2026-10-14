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
