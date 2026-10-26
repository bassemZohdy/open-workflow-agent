"""Private observation transport contract, independent of native SDKs."""

from __future__ import annotations

import asyncio
import json
from dataclasses import FrozenInstanceError

import pytest
from open_workflow_agent.native_streams import NativeStreamLimits, NativeStreamLog


def test_replay_and_live_registration_share_an_order_without_mutable_values():
    log = NativeStreamLog("invocation-a")
    data = {"notes": ["first"]}
    first = log.append("values", data, scope="/do")
    data["notes"].append("later")
    assert json.loads(first.payload)["data"] == {"notes": ["first"]}
    with pytest.raises(FrozenInstanceError):
        first.payload = b"replacement"
    second = log.append("updates", 2, scope="/do", task_reference="/do/0/name")
    backlog, subscriber = log.subscribe(last_event_id=first.event_id)
    assert backlog == (second,)
    third = log.append("values", None, scope="/do")
    assert subscriber._queue.get_nowait() == third
    assert json.loads(third.payload)["invocation_id"] == "invocation-a"
    subscriber.close()


@pytest.mark.asyncio
async def test_slow_subscriber_cannot_block_publisher_or_other_observers():
    log = NativeStreamLog("invocation-a", NativeStreamLimits(queue_size=1))
    _, slow = log.subscribe()
    _, fast = log.subscribe()
    first = log.append("values", "a", scope="/do")
    assert await fast.receive(timeout=1) == first
    second = log.append("values", "b", scope="/do")
    assert await fast.receive(timeout=1) == second
    with pytest.raises(BufferError):
        await slow.receive(timeout=1)
    with pytest.raises(EOFError):
        await slow.receive(timeout=1)
    backlog, replacement = log.subscribe(last_event_id=first.event_id)
    assert backlog == (second,)
    replacement.close()
    fast.close()


@pytest.mark.parametrize("budget", ["count", "bytes"])
def test_eviction_is_bounded_and_cursor_loss_is_explicit(budget):
    limits = (
        NativeStreamLimits(max_events=2)
        if budget == "count"
        else NativeStreamLimits(max_bytes=600, max_event_bytes=300)
    )
    log = NativeStreamLog("invocation-a", limits)
    frames = [log.append("values", "x" * 40, scope="/do") for _ in range(5)]
    backlog, subscriber = log.subscribe()
    assert len(backlog) <= 2
    assert sum(len(frame.payload) for frame in backlog) <= limits.max_bytes
    assert backlog[-1] == frames[-1]
    with pytest.raises(LookupError):
        log.subscribe(last_event_id=frames[0].event_id)
    # A cursor immediately preceding the first retained item has no gap.
    index = frames.index(backlog[0])
    replay, other = log.subscribe(last_event_id=frames[index - 1].event_id)
    assert replay == backlog
    subscriber.close()
    other.close()


@pytest.mark.parametrize(
    "cursor", ["", "not-a-cursor", "{epoch}:0", "{epoch}:01", "{epoch}:99", "{epoch}:١", "x" * 1000]
)
def test_unknown_malformed_and_future_cursors_fail_closed(cursor):
    log = NativeStreamLog("invocation-a")
    frame = log.append("values", {}, scope="/do")
    cursor = cursor.replace("{epoch}", frame.event_id.partition(":")[0])
    with pytest.raises(LookupError):
        log.subscribe(last_event_id=cursor)


def test_restart_and_other_invocation_cursors_cannot_select_replay():
    first = NativeStreamLog("invocation-a")
    frame = first.append("values", {}, scope="/do")
    for log in (NativeStreamLog("invocation-a"), NativeStreamLog("invocation-b")):
        log.append("values", {}, scope="/do")
        with pytest.raises(LookupError):
            log.subscribe(last_event_id=frame.event_id)


def test_oversized_observation_does_not_consume_cursor_or_mutate_history():
    log = NativeStreamLog("invocation-a", NativeStreamLimits(max_event_bytes=256))
    first = log.append("values", None, scope="/do")
    with pytest.raises(ValueError):
        log.append("values", "x" * 1000, scope="/do")
    second = log.append("values", None, scope="/do")
    assert second.event_id.endswith(":2")
    frames, subscriber = log.subscribe()
    assert frames == (first, second)
    subscriber.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["frame_limit", "projection_failure"])
async def test_failed_projection_terminates_readers_and_rejects_incomplete_replay(reason):
    log = NativeStreamLog("invocation-a")
    _, subscriber = log.subscribe()
    log.append("values", {"ok": True}, scope="/do")
    log.fail(reason)
    with pytest.raises(BufferError, match=reason):
        await subscriber.receive(timeout=1)
    with pytest.raises(BufferError, match=reason):
        log.subscribe()


@pytest.mark.asyncio
async def test_cancelled_read_does_not_close_log_or_future_observations():
    log = NativeStreamLog("invocation-a")
    _, subscriber = log.subscribe()
    read = asyncio.create_task(subscriber.receive(timeout=10))
    await asyncio.sleep(0)
    read.cancel()
    with pytest.raises(asyncio.CancelledError):
        await read
    frame = log.append("values", {"still_running": True}, scope="/do")
    assert await subscriber.receive(timeout=1) == frame
    log.close()


@pytest.mark.asyncio
async def test_close_wakes_consumer_and_releases_capacity_without_execution_control():
    log = NativeStreamLog("invocation-a", NativeStreamLimits(max_subscribers=1))
    _, subscriber = log.subscribe()
    with pytest.raises(RuntimeError):
        log.subscribe()
    waiting = asyncio.create_task(subscriber.receive(timeout=10))
    await asyncio.sleep(0)
    subscriber.close()
    with pytest.raises(EOFError):
        await waiting
    # Closing an observer leaves publication usable and frees its slot.
    log.append("values", {}, scope="/do")
    _, replacement = log.subscribe()
    log.close()
    log.close()
    with pytest.raises(EOFError):
        await replacement.receive(timeout=1)
    with pytest.raises(RuntimeError):
        log.append("values", {}, scope="/do")
    with pytest.raises(RuntimeError):
        log.subscribe()


@pytest.mark.parametrize(
    "settings",
    [
        {"max_events": True},
        {"max_events": 0},
        {"queue_size": 1001},
        {"max_bytes": 255},
        {"max_event_bytes": 255},
        {"max_subscribers": 0},
        {"max_bytes": 300, "max_event_bytes": 400},
    ],
)
def test_invalid_or_inconsistent_resource_bounds_are_rejected(settings):
    with pytest.raises(ValueError):
        NativeStreamLimits(**settings)


@pytest.mark.parametrize(
    "mode,scope,reference",
    [
        ("debug", "/do", None),
        ("values", "/do-private", None),
        ("values", "/checkpoint/x", None),
        ("updates", "/do", None),
        ("updates", "/do", "native-node-id"),
    ],
)
def test_unpublished_modes_and_nonpublic_references_are_rejected(mode, scope, reference):
    with pytest.raises(ValueError):
        NativeStreamLog("invocation-a").append(mode, {}, scope=scope, task_reference=reference)
