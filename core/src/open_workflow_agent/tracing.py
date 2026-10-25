"""Bounded, opt-in common execution traces; never execution authority.

Only generated W3C version-00 contexts are supported. This is not an incoming
header propagator, an OpenTelemetry exporter, or a durable span store.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from threading import RLock
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from .lifecycle import INVOCATION_STATES

if TYPE_CHECKING:
    from .config import TracingConfig
    from .persistence import ExecutionHandle

_VERSION_ZERO = re.compile(r"00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})\Z")


def valid_traceparent(value: object) -> bool:
    """Accept only the generated, fixed-length version-00 wire form."""
    if not isinstance(value, str):
        return False
    match = _VERSION_ZERO.fullmatch(value)
    return bool(match and int(match[1], 16) and int(match[2], 16))


def invocation_trace_id(invocation_id: str) -> str:
    """Stable diagnostic correlation across segments of one common identity."""
    return hashlib.sha256(b"owa:trace:1\0" + invocation_id.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True, slots=True)
class TraceContext:
    trace_id: str
    span_id: str
    propagate_http: bool

    @property
    def traceparent(self) -> str:
        return f"00-{self.trace_id}-{self.span_id}-01"


_CURRENT: ContextVar[TraceContext | None] = ContextVar("owa_trace", default=None)


def current_traceparent(*, for_http: bool = False) -> str | None:
    context = _CURRENT.get()
    if context is None or (for_http and not context.propagate_http):
        return None
    return context.traceparent


@dataclass(frozen=True, slots=True)
class SpanRecord:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    kind: Literal["invocation", "task"]
    invocation_id: str | None
    workflow_fingerprint: str | None
    task_reference: str | None
    parent_invocation_trace_id: str | None
    started_at: str
    duration_seconds: float
    outcome: Literal["completed", "error", "cancelled"]
    invocation_status: str


def _bounded(value: str | None, maximum: int) -> str | None:
    if value is None:
        return None
    try:
        if len(value.encode("utf-8")) > maximum:
            return None
    except UnicodeError:
        return None
    return value


class ExecutionTracer:
    """In-process diagnostic spans with fixed fields and explicit loss counters.

    All buffers are bounded. Saturation drops spans and clears this execution's
    context, so it cannot accidentally inherit another runtime's tracing policy.
    No payload, credential, exception text, URL, model prompt or native state is
    accepted. Records do not drive retry, replay, authorization or checkpoints.
    """

    def __init__(self, config: TracingConfig) -> None:
        self.config = config
        self._lock = RLock()
        self._records: deque[tuple[SpanRecord, int]] = deque()
        self._retained_bytes = 0
        self._active = 0
        self.dropped_spans = 0
        self.evicted_spans = 0

    def snapshot(self) -> tuple[SpanRecord, ...]:
        with self._lock:
            return tuple(record for record, _ in self._records)

    def capabilities(self) -> dict[str, object]:
        return {
            "enabled": self.config.enabled,
            "profile": "owa/execution-tracing/v1",
            "context": "generated_w3c_version_00",
            "httpPropagation": self.config.enabled and self.config.propagate_http,
            "incomingContext": False,
            "durableSpans": False,
            "exporter": False,
            "executionAuthority": False,
        }

    @contextmanager
    def invocation_scope(self, handle: ExecutionHandle) -> Iterator[None]:
        with self._scope(handle, kind="invocation", task_reference=None):
            yield

    @contextmanager
    def task_scope(self, handle: ExecutionHandle, reference: str) -> Iterator[None]:
        with self._scope(handle, kind="task", task_reference=reference):
            yield

    @contextmanager
    def _scope(
        self,
        handle: ExecutionHandle,
        *,
        kind: Literal["invocation", "task"],
        task_reference: str | None,
    ) -> Iterator[None]:
        with self._lock:
            admitted = self.config.enabled and self._active < self.config.max_active_spans
            if admitted:
                self._active += 1
            elif self.config.enabled:
                self.dropped_spans += 1
        if not admitted:
            token = _CURRENT.set(None)
            try:
                yield
            finally:
                _CURRENT.reset(token)
            return
        trace_id = invocation_trace_id(handle.invocation_id)
        previous = _CURRENT.get()
        parent_span_id = (
            previous.span_id if previous is not None and previous.trace_id == trace_id else None
        )
        context = TraceContext(trace_id, uuid4().hex[:16], self.config.propagate_http)
        token = _CURRENT.set(context)
        started = time.monotonic()
        started_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        outcome: Literal["completed", "error", "cancelled"] = "completed"
        try:
            yield
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except BaseException:
            outcome = "error"
            raise
        finally:
            _CURRENT.reset(token)
            status = handle.status if handle.status in INVOCATION_STATES else "unknown"
            if kind == "invocation" and outcome == "completed":
                if status == "faulted":
                    outcome = "error"
                elif status == "cancelled":
                    outcome = "cancelled"
            record = SpanRecord(
                trace_id=trace_id,
                span_id=context.span_id,
                parent_span_id=parent_span_id,
                kind=kind,
                invocation_id=_bounded(handle.invocation_id, 256),
                workflow_fingerprint=_bounded(handle.workflow_fingerprint, 256),
                task_reference=_bounded(task_reference, 4096),
                parent_invocation_trace_id=(
                    invocation_trace_id(handle.parent_invocation_id)
                    if handle.parent_invocation_id is not None
                    else None
                ),
                started_at=started_at,
                duration_seconds=max(0.0, time.monotonic() - started),
                outcome=outcome,
                invocation_status=status,
            )
            size = len(json.dumps(asdict(record), ensure_ascii=False).encode("utf-8"))
            with self._lock:
                self._active -= 1
                if size > self.config.max_retained_bytes:
                    self.dropped_spans += 1
                else:
                    self._records.append((record, size))
                    self._retained_bytes += size
                    while (
                        len(self._records) > self.config.max_spans
                        or self._retained_bytes > self.config.max_retained_bytes
                    ):
                        _, removed = self._records.popleft()
                        self._retained_bytes -= removed
                        self.evicted_spans += 1
