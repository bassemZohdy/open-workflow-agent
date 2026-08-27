from __future__ import annotations

import asyncio
import json

import pytest
from open_workflow_agent.observability import InMemoryEventSink, LifecycleCloudEventSink, WorkflowEvent
from open_workflow_agent.streaming import StreamLimits, lifecycle_sse_stream


def _sink(*, max_subscribers: int = 32) -> LifecycleCloudEventSink:
    return LifecycleCloudEventSink(InMemoryEventSink(), max_subscribers=max_subscribers)


def _event(event_id: str, *, status: str = "running") -> WorkflowEvent:
    return WorkflowEvent(
        "WorkflowStatusChanged",
        invocation_id="invocation-1",
        event_id=event_id,
        status=status,
    )


@pytest.mark.asyncio
async def test_stream_registers_before_waiting_and_preserves_emission_order() -> None:
    sink = _sink()
    stream = lifecycle_sse_stream(
        sink,
        limits=StreamLimits(max_events=2, max_bytes=4096, timeout_seconds=1, queue_size=2),
    )
    first_frame = asyncio.create_task(anext(stream))
    await asyncio.sleep(0)

    sink.emit(_event("event-1"))
    sink.emit(_event("event-2", status="completed"))

    first = await asyncio.wait_for(first_frame, timeout=1)
    second = await asyncio.wait_for(anext(stream), timeout=1)
    terminal = await asyncio.wait_for(anext(stream), timeout=1)
    await stream.aclose()

    assert first.startswith(b"id: event-1\nevent: lifecycle\n")
    assert second.startswith(b"id: event-2\nevent: lifecycle\n")
    assert b'"status":"running"' in first
    assert b'"status":"completed"' in second
    assert terminal == b'event: stream.end\ndata: {"reason":"event_limit"}\n\n'


@pytest.mark.asyncio
async def test_last_event_id_replays_only_newer_bounded_snapshot() -> None:
    sink = _sink()
    sink.emit(_event("event-1"))
    sink.emit(_event("event-2"))
    sink.emit(_event("event-3"))

    stream = lifecycle_sse_stream(
        sink,
        last_event_id="event-1",
        limits=StreamLimits(max_events=2, max_bytes=4096, timeout_seconds=1),
    )
    frames = [await anext(stream), await anext(stream), await anext(stream)]
    await stream.aclose()

    assert frames[0].startswith(b"id: event-2\n")
    assert frames[1].startswith(b"id: event-3\n")
    assert b'"reason":"event_limit"' in frames[2]


def test_unknown_last_event_id_fails_closed() -> None:
    sink = _sink()
    sink.emit(_event("event-1"))

    stream = lifecycle_sse_stream(sink, last_event_id="expired-event")
    with pytest.raises(LookupError, match="bounded replay window"):
        asyncio.run(anext(stream))


@pytest.mark.asyncio
async def test_slow_consumer_is_terminated_on_bounded_queue_overflow() -> None:
    sink = _sink()
    stream = lifecycle_sse_stream(
        sink,
        limits=StreamLimits(max_events=10, max_bytes=4096, timeout_seconds=1, queue_size=1),
    )
    first_frame = asyncio.create_task(anext(stream))
    await asyncio.sleep(0)
    sink.emit(_event("event-1"))
    assert (await first_frame).startswith(b"id: event-1\n")

    sink.emit(_event("event-2"))
    sink.emit(_event("event-3"))
    terminal = await asyncio.wait_for(anext(stream), timeout=1)
    await stream.aclose()

    assert terminal == b'event: stream.end\ndata: {"reason":"backpressure"}\n\n'


@pytest.mark.asyncio
async def test_stream_enforces_timeout_and_cleans_subscription() -> None:
    sink = _sink()
    stream = lifecycle_sse_stream(
        sink,
        limits=StreamLimits(max_events=2, max_bytes=4096, timeout_seconds=0.01),
    )
    terminal = await asyncio.wait_for(anext(stream), timeout=1)
    await stream.aclose()

    assert terminal == b'event: stream.end\ndata: {"reason":"timeout"}\n\n'
    assert sink._subscribers == {}


def test_stream_limits_are_bounded() -> None:
    with pytest.raises(ValueError, match="max_events"):
        StreamLimits(max_events=0)
    with pytest.raises(ValueError, match="max_bytes"):
        StreamLimits(max_bytes=128)
    with pytest.raises(ValueError, match="timeout_seconds"):
        StreamLimits(timeout_seconds=301)
    with pytest.raises(ValueError, match="queue_size"):
        StreamLimits(queue_size=0)


def test_sse_frames_are_valid_json_and_do_not_expose_native_state() -> None:
    sink = _sink()
    sink.emit(
        WorkflowEvent(
            "WorkflowCompleted",
            invocation_id="invocation-1",
            event_id="event-safe",
            engine="adk",
            status="completed",
        )
    )
    cloud = sink.snapshot(1)[0]
    payload = json.loads(cloud.raw())

    assert payload["id"] == "event-safe"
    assert payload["data"]["invocation_id"] == "invocation-1"
    assert "checkpoint" not in payload["data"]
    assert "pod" not in payload["data"]
