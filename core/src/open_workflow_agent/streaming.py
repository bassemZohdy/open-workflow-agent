"""Bounded, engine-neutral Server-Sent Events streaming helpers."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from time import monotonic
from typing import Any

from .observability import CloudEvent, LifecycleCloudEventSink


@dataclass(frozen=True, slots=True)
class StreamLimits:
    """Per-connection limits for the portable lifecycle stream."""

    max_events: int = 100
    max_bytes: int = 1_048_576
    timeout_seconds: float = 30.0
    queue_size: int = 64

    def __post_init__(self) -> None:
        if self.max_events < 1 or self.max_events > 1000:
            raise ValueError("max_events must be between 1 and 1000")
        if self.max_bytes < 256 or self.max_bytes > 16_777_216:
            raise ValueError("max_bytes must be between 256 and 16777216")
        if self.timeout_seconds <= 0 or self.timeout_seconds > 300:
            raise ValueError("timeout_seconds must be greater than zero and at most 300")
        if self.queue_size < 1 or self.queue_size > 1000:
            raise ValueError("queue_size must be between 1 and 1000")


def encode_sse(event: CloudEvent, *, event_name: str = "lifecycle") -> bytes:
    payload = json.dumps(event.as_dict(), ensure_ascii=False, separators=(",", ":"))
    return f"id: {event.id}\nevent: {event_name}\ndata: {payload}\n\n".encode()


def encode_terminal(*, reason: str) -> bytes:
    payload = json.dumps({"reason": reason}, separators=(",", ":"))
    return f"event: stream.end\ndata: {payload}\n\n".encode()


def lifecycle_sse_stream(
    sink: LifecycleCloudEventSink,
    *,
    last_event_id: str | None = None,
    limits: StreamLimits | None = None,
) -> AsyncIterator[bytes]:
    """Create an ordered bounded snapshot+live lifecycle stream.

    Registration happens before this function returns, so an HTTP endpoint can
    validate replay state and establish the subscriber before yielding response
    control. A slow consumer is terminated fail-closed on queue overflow rather
    than allowing unbounded buffering. Disconnecting this observation stream
    does not cancel the associated workflow invocation.
    """

    selected = limits or StreamLimits()
    backlog, subscription = sink.subscribe(
        last_event_id=last_event_id,
        queue_size=selected.queue_size,
    )

    async def stream() -> AsyncIterator[bytes]:
        started = monotonic()
        sent_events = 0
        sent_bytes = 0
        terminal_reason = "timeout"

        try:
            for event in backlog:
                if sent_events >= selected.max_events:
                    terminal_reason = "event_limit"
                    break
                frame = encode_sse(event)
                if sent_bytes + len(frame) > selected.max_bytes:
                    terminal_reason = "byte_limit"
                    break
                yield frame
                sent_events += 1
                sent_bytes += len(frame)
            else:
                while sent_events < selected.max_events:
                    remaining = selected.timeout_seconds - (monotonic() - started)
                    if remaining <= 0:
                        terminal_reason = "timeout"
                        break
                    try:
                        event = await subscription.receive(timeout=remaining)
                    except TimeoutError:
                        terminal_reason = "timeout"
                        break
                    except BufferError:
                        terminal_reason = "backpressure"
                        break
                    frame = encode_sse(event)
                    if sent_bytes + len(frame) > selected.max_bytes:
                        terminal_reason = "byte_limit"
                        break
                    yield frame
                    sent_events += 1
                    sent_bytes += len(frame)
                else:
                    terminal_reason = "event_limit"

            terminal = encode_terminal(reason=terminal_reason)
            if sent_bytes + len(terminal) <= selected.max_bytes:
                yield terminal
        except asyncio.CancelledError:
            raise
        finally:
            subscription.close()

    return stream()


def streaming_capabilities() -> dict[str, Any]:
    """Describe only the bounded common streaming surface implemented here."""

    defaults = StreamLimits()
    return {
        "enabled": True,
        "transport": "sse",
        "endpoint": "/v1/events/lifecycle/stream",
        "eventModel": "cloudevents-1.0-lifecycle",
        "ordering": "emission_order_per_runtime",
        "replay": "bounded_snapshot_by_last_event_id",
        "disconnectCancelsInvocation": False,
        "backpressure": "bounded_queue_disconnect",
        "defaults": {
            "maxEvents": defaults.max_events,
            "maxBytes": defaults.max_bytes,
            "timeoutSeconds": defaults.timeout_seconds,
            "queueSize": defaults.queue_size,
        },
        "a2aStreaming": True,
        "grpcStreaming": False,
        "pushNotifications": False,
    }
