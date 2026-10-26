"""Private bounded observation transport; native state projection stays in adapters.

This is not an execution journal or a public streaming capability. An endpoint
must establish original invocation ownership before selecting one of these logs.
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from dataclasses import dataclass
from typing import Any, Literal
from uuid import uuid4

from .expressions import validate_expression_data

NativeStreamMode = Literal["values", "updates"]


@dataclass(frozen=True, slots=True)
class NativeStreamLimits:
    max_events: int = 256
    max_bytes: int = 1_048_576
    max_event_bytes: int = 65_536
    max_subscribers: int = 32
    queue_size: int = 64

    def __post_init__(self) -> None:
        for name, maximum in (
            ("max_events", 1000),
            ("max_bytes", 16_777_216),
            ("max_event_bytes", 262_144),
            ("max_subscribers", 128),
            ("queue_size", 1000),
        ):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f"native stream {name} is outside its supported bound")
        if not 256 <= self.max_event_bytes <= self.max_bytes:
            raise ValueError("native stream event bytes must fit the retained byte budget")


@dataclass(frozen=True, slots=True)
class NativeStreamFrame:
    event_id: str
    payload: bytes


class NativeStreamSubscription:
    """An observation consumer whose cancellation never controls execution."""

    def __init__(self, log: NativeStreamLog) -> None:
        self._log = log
        self._queue: asyncio.Queue[NativeStreamFrame | str] = asyncio.Queue(
            maxsize=log.limits.queue_size
        )
        self._closed = False

    def _end(self, reason: str) -> None:
        if self._closed:
            return
        self._closed = True
        self._log._subscribers.discard(self)
        while not self._queue.empty():
            self._queue.get_nowait()
        self._queue.put_nowait(reason)

    async def receive(self, *, timeout: float) -> NativeStreamFrame:
        if self._closed and self._queue.empty():
            raise EOFError("native observation subscription is closed")
        item = await asyncio.wait_for(self._queue.get(), timeout)
        if item in ("overflow", "frame_limit", "projection_failure"):
            raise BufferError(f"native observation is unavailable: {item}")
        if not isinstance(item, NativeStreamFrame):
            raise EOFError("native observation subscription is closed")
        return item

    def close(self) -> None:
        self._end("closed")


class NativeStreamLog:
    """One invocation's process-local ring, owned by its event loop.

    Publishing never awaits subscribers. Count/byte eviction and slow consumers
    cannot block an executing workflow. Neither this log nor its cursor proves
    checkpoint durability or grants permission to read an invocation.
    """

    def __init__(self, invocation_id: str, limits: NativeStreamLimits | None = None) -> None:
        if not isinstance(invocation_id, str) or not 1 <= len(invocation_id) <= 128:
            raise ValueError("native stream invocation identity is invalid")
        self.invocation_id = invocation_id
        self.limits = limits or NativeStreamLimits()
        self._epoch = uuid4().hex
        self._sequence = 0
        self._events: deque[tuple[int, NativeStreamFrame]] = deque()
        self._bytes = 0
        self._subscribers: set[NativeStreamSubscription] = set()
        self._closed = False
        self._failure: str | None = None

    def append(
        self,
        mode: NativeStreamMode,
        data: Any,
        *,
        scope: str,
        task_reference: str | None = None,
    ) -> NativeStreamFrame:
        if self._closed:
            raise RuntimeError("native observation log is closed")
        if not isinstance(mode, str) or mode not in {"values", "updates"}:
            raise ValueError("native observation mode is unsupported")
        for reference in (scope, task_reference):
            if reference is not None and (
                not isinstance(reference, str)
                or not (reference == "/do" or reference.startswith("/do/"))
                or len(reference.encode("utf-8")) > 4096
            ):
                raise ValueError("native observation task reference is invalid")
        if scope is None or (mode == "updates" and task_reference is None):
            raise ValueError("native observation requires its public scope/task reference")
        validate_expression_data(data)
        sequence = self._sequence + 1
        event_id = f"{self._epoch}:{sequence}"
        payload = json.dumps(
            {
                "schema": "owa/native-observation/v1",
                "id": event_id,
                "invocation_id": self.invocation_id,
                "mode": mode,
                "scope": scope,
                "task_reference": task_reference,
                "data": data,
            },
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(payload) > self.limits.max_event_bytes:
            raise ValueError("native observation exceeds its frame byte budget")
        frame = NativeStreamFrame(event_id, payload)
        self._sequence = sequence
        self._events.append((sequence, frame))
        self._bytes += len(payload)
        while len(self._events) > self.limits.max_events or self._bytes > self.limits.max_bytes:
            _, removed = self._events.popleft()
            self._bytes -= len(removed.payload)
        for subscriber in tuple(self._subscribers):
            try:
                subscriber._queue.put_nowait(frame)
            except asyncio.QueueFull:
                subscriber._end("overflow")
        return frame

    def _cursor(self, event_id: str) -> int:
        if not isinstance(event_id, str) or len(event_id) > 64:
            raise LookupError("native observation replay is unavailable")
        epoch, separator, raw_sequence = event_id.partition(":")
        if (
            epoch != self._epoch
            or separator != ":"
            or not raw_sequence.isascii()
            or not raw_sequence.isdecimal()
            or raw_sequence.startswith("0")
        ):
            raise LookupError("native observation replay is unavailable")
        sequence = int(raw_sequence)
        first = self._events[0][0] if self._events else self._sequence + 1
        if not max(1, first - 1) <= sequence <= self._sequence:
            raise LookupError("native observation replay is unavailable")
        return sequence

    def subscribe(
        self, *, last_event_id: str | None = None
    ) -> tuple[tuple[NativeStreamFrame, ...], NativeStreamSubscription]:
        if self._failure is not None:
            raise BufferError(f"native observation is unavailable: {self._failure}")
        if self._closed:
            raise RuntimeError("native observation log is closed")
        sequence = self._cursor(last_event_id) if last_event_id is not None else 0
        if len(self._subscribers) >= self.limits.max_subscribers:
            raise RuntimeError("native observation subscriber capacity is exhausted")
        backlog = tuple(frame for number, frame in self._events if number > sequence)
        subscription = NativeStreamSubscription(self)
        self._subscribers.add(subscription)
        return backlog, subscription

    def fail(self, reason: Literal["frame_limit", "projection_failure"]) -> None:
        """End observations explicitly when a safe projection cannot be retained."""
        if reason not in {"frame_limit", "projection_failure"}:
            raise ValueError("native observation failure reason is unsupported")
        if self._closed:
            return
        self._failure = reason
        for subscription in tuple(self._subscribers):
            subscription._end(reason)
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for subscription in tuple(self._subscribers):
            subscription.close()
        self._events.clear()
        self._bytes = 0
