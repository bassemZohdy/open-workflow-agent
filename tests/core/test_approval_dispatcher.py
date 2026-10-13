"""Managed expiry observation stops before close and exposes only safe errors."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from open_workflow_agent.approval_dispatcher import ApprovalExpiryDispatcher
from open_workflow_agent.errors import OwaError, WorkflowExecutionError


@pytest.mark.parametrize(
    "poll,limit",
    [
        (0, 1),
        (-1, 1),
        (float("nan"), 1),
        (float("inf"), 1),
        (True, 1),
        (61, 1),
        (0.1, 0),
        (0.1, 1001),
        (0.1, True),
        (0.1, 1.5),
    ],
)
def test_expiry_observer_rejects_unbounded_or_hot_loop_declarations(poll, limit):
    with pytest.raises(ValueError):
        ApprovalExpiryDispatcher(SimpleNamespace(), poll_seconds=poll, scan_limit=limit)


@pytest.mark.asyncio
async def test_default_disabled_observer_performs_no_expiry_or_background_task():
    service = SimpleNamespace(protected_store=None, expire_due=AsyncMock())
    dispatcher = ApprovalExpiryDispatcher(service, poll_seconds=0.01, scan_limit=8)
    await dispatcher.start()
    assert await dispatcher.tick() == 0 and dispatcher._task is None
    await dispatcher.stop()
    service.expire_due.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("close", [False, True])
async def test_repeated_start_is_single_observer_and_stop_fences_expiry_before_store_close(close):
    started = asyncio.Event()
    interrupted = asyncio.Event()
    calls = []

    async def expire_due(**kwargs):
        calls.append(kwargs)
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            interrupted.set()

    service = SimpleNamespace(protected_store=object(), expire_due=expire_due)
    dispatcher = ApprovalExpiryDispatcher(service, poll_seconds=0.01, scan_limit=8)
    await dispatcher.start()
    first = dispatcher._task
    await asyncio.wait_for(started.wait(), 5)
    await dispatcher.start()
    assert dispatcher._task is first
    if close:
        dispatcher.close()
    await dispatcher.stop()
    assert interrupted.is_set() and first.cancelled()
    assert calls == [{"limit": 8}] and await dispatcher.tick() == 0


@pytest.mark.asyncio
async def test_failed_expiry_is_sanitized_and_does_not_end_future_observation(caplog):
    observed = asyncio.Event()
    count = 0

    async def expire_due(**kwargs):
        nonlocal count
        count += 1
        if count == 1:
            raise WorkflowExecutionError("private-token-and-request-body")
        observed.set()
        return 0

    service = SimpleNamespace(protected_store=object(), expire_due=expire_due)
    dispatcher = ApprovalExpiryDispatcher(service, poll_seconds=0.01, scan_limit=2)
    await dispatcher.start()
    try:
        await asyncio.wait_for(observed.wait(), 5)
        assert "private-token-and-request-body" not in caplog.text
        assert "workflow_execution_error" in caplog.text
        assert count >= 2
    finally:
        await dispatcher.stop()


@pytest.mark.asyncio
async def test_observer_does_not_log_unreviewed_dynamic_error_codes(caplog):
    class PrivateFailure(OwaError):
        code = "private-token-in-dynamic-code"

    observed = asyncio.Event()
    calls = 0

    async def expire_due(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PrivateFailure("private-request-body")
        observed.set()
        return 0

    dispatcher = ApprovalExpiryDispatcher(
        SimpleNamespace(protected_store=object(), expire_due=expire_due),
        poll_seconds=0.01,
        scan_limit=2,
    )
    await dispatcher.start()
    try:
        await asyncio.wait_for(observed.wait(), 5)
        assert "private-token" not in caplog.text and "private-request-body" not in caplog.text
        assert "approval_expiry_failed" in caplog.text
    finally:
        await dispatcher.stop()
