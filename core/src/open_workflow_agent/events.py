"""Framework-neutral workflow event envelopes and bounded event delivery."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    """Portable event data; transport-specific broker metadata is excluded."""

    id: str
    source: str
    type: str
    time: str
    subject: str | None = None
    datacontenttype: str | None = None
    dataschema: str | None = None
    data: Any = None
    extensions: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "id": self.id,
            "source": self.source,
            "type": self.type,
            "time": self.time,
        }
        for key, item in (
            ("subject", self.subject),
            ("datacontenttype", self.datacontenttype),
            ("dataschema", self.dataschema),
        ):
            if item is not None:
                value[key] = item
        if self.data is not None:
            value["data"] = self.data
        value.update(self.extensions)
        return value

    def raw(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class EventBus(Protocol):
    durable: bool

    async def publish(
        self, properties: Mapping[str, Any], *, default_source: str
    ) -> EventEnvelope: ...

    def receive(self, strategy: Mapping[str, Any]) -> Awaitable[EventEnvelope]: ...


class InMemoryEventBus:
    """Bounded portable event delivery for one runtime process.

    Delivery is broadcast to active matching listeners. It intentionally has no
    replay or durability semantics; deployments requiring those remain outside
    this milestone and must advertise a different capability.

    Calling ``receive`` registers the listener synchronously before returning
    its awaitable. This guarantees that a workflow may expose ``waiting`` only
    after the matching in-process subscription exists, avoiding a lost-event
    race between status observation and the first await of the receiver.
    """

    durable = False

    def __init__(self) -> None:
        self.published: list[EventEnvelope] = []
        self._subscribers: dict[int, tuple[Mapping[str, Any], asyncio.Queue[EventEnvelope]]] = {}
        self._next_subscriber = 0
        self._lock = asyncio.Lock()

    async def publish(self, properties: Mapping[str, Any], *, default_source: str) -> EventEnvelope:
        envelope = _make_envelope(properties, default_source=default_source)
        async with self._lock:
            self.published.append(envelope)
            subscribers = tuple(self._subscribers.values())
        for strategy, queue in subscribers:
            if _matches(envelope, strategy):
                queue.put_nowait(envelope)
        return envelope

    def receive(self, strategy: Mapping[str, Any]) -> Awaitable[EventEnvelope]:
        queue: asyncio.Queue[EventEnvelope] = asyncio.Queue()
        subscriber_id = self._next_subscriber
        self._next_subscriber += 1
        self._subscribers[subscriber_id] = (strategy, queue)

        async def wait_for_event() -> EventEnvelope:
            try:
                return await queue.get()
            finally:
                async with self._lock:
                    self._subscribers.pop(subscriber_id, None)

        return wait_for_event()


def _make_envelope(properties: Mapping[str, Any], *, default_source: str) -> EventEnvelope:
    event_type = properties.get("type")
    if not isinstance(event_type, str) or not event_type:
        raise ValueError("event type must be a non-empty string")
    event_id = properties.get("id") or str(uuid4())
    source = properties.get("source") or default_source
    if not isinstance(event_id, str) or not isinstance(source, str):
        raise ValueError("event id and source must be strings")
    event_time = properties.get("time")
    if event_time is None:
        event_time = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    if not isinstance(event_time, str):
        raise ValueError("event time must be an RFC 3339 string")
    known = {
        "id",
        "source",
        "type",
        "time",
        "subject",
        "datacontenttype",
        "dataschema",
        "data",
    }
    return EventEnvelope(
        id=event_id,
        source=source,
        type=event_type,
        time=event_time,
        subject=_optional_string(properties.get("subject"), "subject"),
        datacontenttype=_optional_string(properties.get("datacontenttype"), "datacontenttype"),
        dataschema=_optional_string(properties.get("dataschema"), "dataschema"),
        data=properties.get("data"),
        extensions={key: value for key, value in properties.items() if key not in known},
    )


def _optional_string(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"event {name} must be a string")
    return value


def _matches(event: EventEnvelope, strategy: Mapping[str, Any]) -> bool:
    selected = strategy.get("one")
    if not isinstance(selected, Mapping):
        return False
    event_filter = selected.get("with")
    if not isinstance(event_filter, Mapping):
        return False
    values = event.as_dict()
    return all(values.get(key) == expected for key, expected in event_filter.items())
