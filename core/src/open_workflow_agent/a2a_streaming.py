"""A2A v1 streaming/resubscription over the common lifecycle event infrastructure.

Translates common lifecycle CloudEvents into official A2A v1 streamed
responses (``Task``, ``TaskStatusUpdateEvent``, ``TaskArtifactUpdateEvent``).
Engine-native checkpoint or stream objects are never exposed. Streams reuse
the bounded common SSE mechanics: bounded queues, event/byte/time limits, and
fail-closed backpressure. Disconnecting a stream never cancels the underlying
invocation.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from time import monotonic
from typing import Any

from .a2a_tasks import _output_part, a2a_task_state, project_a2a_task
from .observability import CloudEvent, LifecycleCloudEventSink
from .streaming import StreamLimits

_A2A_SSE_MEDIA_TYPE = "text/event-stream"
_OUTPUT_ARTIFACT_NAME = "workflow-output"

# Common lifecycle event type -> (A2A state, terminal).
_TRANSITIONS: dict[str, tuple[str, bool]] = {
    "WorkflowStarted": ("TASK_STATE_WORKING", False),
    "WorkflowResumed": ("TASK_STATE_WORKING", False),
    "WorkflowWaiting": ("TASK_STATE_INPUT_REQUIRED", False),
    "WorkflowCompleted": ("TASK_STATE_COMPLETED", True),
    "WorkflowFaulted": ("TASK_STATE_FAILED", True),
    "WorkflowCancelled": ("TASK_STATE_CANCELED", True),
}
_TERMINAL_STATES = frozenset(state for state, terminal in _TRANSITIONS.values() if terminal)


def _sse_frame(event_id: str, payload: dict[str, Any]) -> bytes:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event_id}\ndata: {data}\n\n".encode()


def _status_update(task_id: str, context_id: str, state: str, *, final: bool) -> dict[str, Any]:
    return {
        "taskId": task_id,
        "contextId": context_id,
        "status": {"state": state},
        "final": final,
    }


def _artifact_update(task_id: str, context_id: str, output: Any) -> dict[str, Any]:
    return {
        "taskId": task_id,
        "contextId": context_id,
        "artifact": {
            "artifactId": f"{task_id}:output",
            "name": _OUTPUT_ARTIFACT_NAME,
            "parts": [_output_part(output)],
        },
        "lastChunk": True,
    }


def a2a_stream_media_type() -> str:
    return _A2A_SSE_MEDIA_TYPE


def a2a_sse_stream(
    sink: LifecycleCloudEventSink,
    invocations: Any,
    task_id: str,
    *,
    limits: StreamLimits | None = None,
) -> AsyncIterator[bytes]:
    """Create a bounded SSE stream of A2A streamed responses for one task.

    The stream opens with the current Task projection, then translates each
    matching common lifecycle event into protocol-native status/artifact
    updates and closes after the first terminal transition. A task that is
    already terminal yields only its projection. Limits bound events, bytes,
    and duration; a client that needs more re-subscribes.
    """

    selected = limits or StreamLimits()
    backlog, subscription = sink.subscribe(
        last_event_id=None,
        queue_size=selected.queue_size,
    )

    async def stream() -> AsyncIterator[bytes]:
        started = monotonic()
        sent_events = 0
        sent_bytes = 0
        last_state: str | None = None

        def frame_bytes(payload: dict[str, Any], event_id: str) -> bytes | None:
            nonlocal sent_events, sent_bytes
            encoded = _sse_frame(event_id, payload)
            if sent_events >= selected.max_events or sent_bytes + len(encoded) > selected.max_bytes:
                return None
            sent_events += 1
            sent_bytes += len(encoded)
            return encoded

        def context_id() -> str:
            handle = invocations.get(task_id)
            if handle is None or not isinstance(handle.session_id, str) or not handle.session_id:
                return task_id
            return handle.session_id

        try:
            handle = invocations.get(task_id)
            if handle is None:
                return
            last_state = a2a_task_state(handle.status)
            initial = frame_bytes({"task": project_a2a_task(handle)}, f"{task_id}:snapshot")
            if initial is not None:
                yield initial
            if last_state in _TERMINAL_STATES:
                return

            async for source in _event_sources(backlog, subscription, selected, started):
                if source is None:
                    return
                data = source.data if isinstance(source.data, dict) else {}
                if data.get("invocation_id") != task_id:
                    continue
                transition = _TRANSITIONS.get(str(data.get("event_type")))
                if transition is None:
                    continue
                state, terminal = transition
                if state == last_state and not terminal:
                    continue
                if (
                    terminal
                    and state == "TASK_STATE_COMPLETED"
                    and (current := invocations.get(task_id)) is not None
                    and current.output is not None
                ):
                    artifact = frame_bytes(
                        {"artifactUpdate": _artifact_update(task_id, context_id(), current.output)},
                        f"{source.id}:artifact",
                    )
                    if artifact is None:
                        return
                    yield artifact
                encoded = frame_bytes(
                    {"statusUpdate": _status_update(task_id, context_id(), state, final=terminal)},
                    source.id,
                )
                if encoded is None:
                    return
                yield encoded
                last_state = state
                if terminal:
                    return
        finally:
            subscription.close()

    return stream()


async def _event_sources(
    backlog: list[CloudEvent],
    subscription: Any,
    limits: StreamLimits,
    started: float,
) -> AsyncIterator[CloudEvent | None]:
    """Yield backlog events first, then live events until limits expire.

    ``None`` marks a bounded end: no more events within the stream budget.
    """

    for cloud in backlog:
        yield cloud
    while True:
        remaining = limits.timeout_seconds - (monotonic() - started)
        if remaining <= 0:
            yield None
            return
        try:
            yield await subscription.receive(timeout=remaining)
        except TimeoutError:
            yield None
            return
        except BufferError:
            yield None
            return
        except asyncio.CancelledError:
            raise


__all__ = [
    "a2a_sse_stream",
    "a2a_stream_media_type",
]
