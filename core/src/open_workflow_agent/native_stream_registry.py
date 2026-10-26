"""Process-local observation retention, separate from checkpoint/recovery authority."""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic

from .config import NativeStreamsConfig
from .errors import ConfigurationError
from .native_extensions import LANGGRAPH_STREAMS, NativeExtensionRequirement
from .native_streams import (
    NativeStreamFrame,
    NativeStreamLimits,
    NativeStreamLog,
    NativeStreamSubscription,
)
from .persistence import ExecutionHandle


@dataclass(slots=True)
class _Entry:
    log: NativeStreamLog
    owner: str
    fingerprint: str
    ended_at: float | None = None


class NativeStreamRegistry:
    def __init__(self, configuration: NativeStreamsConfig) -> None:
        self.configuration = NativeStreamsConfig.model_validate(
            configuration.model_dump(mode="json")
        )
        self._policy = self.configuration.model_dump(mode="json")
        self._entries: dict[str, _Entry] = {}
        self._consumers: set[NativeStreamSubscription] = set()
        self._closed = False

    def verify(self, configuration: NativeStreamsConfig | None) -> None:
        if (
            self._closed
            or configuration is None
            or configuration.model_dump(mode="json") != self._policy
            or self.configuration.model_dump(mode="json") != self._policy
        ):
            raise ConfigurationError(
                "native observation deployment policy is unavailable or changed"
            )

    def _prune(self) -> None:
        deadline = monotonic() - self.configuration.retention_seconds
        for key, entry in tuple(self._entries.items()):
            if entry.ended_at is not None and entry.ended_at <= deadline:
                entry.log.close()
                del self._entries[key]

    def begin(
        self, handle: ExecutionHandle, requirements: tuple[NativeExtensionRequirement, ...]
    ) -> NativeStreamLog | None:
        if self._closed or not any(
            item.required and item.capability_id == LANGGRAPH_STREAMS for item in requirements
        ):
            return None
        self._prune()
        if not handle.owner_principal:
            return None
        entry = self._entries.get(handle.invocation_id)
        if entry is not None:
            if (
                entry.owner != handle.owner_principal
                or entry.fingerprint != handle.workflow_fingerprint
            ):
                return None
            if entry.log.available:
                entry.log.restart()
                entry.ended_at = None
                return entry.log
            entry.log.close()
            del self._entries[handle.invocation_id]
        if len(self._entries) >= self.configuration.max_logs:
            finished = [
                (key, item) for key, item in self._entries.items() if item.ended_at is not None
            ]
            if not finished:
                return None  # Observation pressure cannot fault business execution.
            key, removed = min(finished, key=lambda item: item[1].ended_at or 0)
            removed.log.close()
            del self._entries[key]
        config = self.configuration
        log = NativeStreamLog(
            handle.invocation_id,
            NativeStreamLimits(
                max_events=config.max_events,
                max_bytes=config.max_bytes,
                max_event_bytes=config.max_event_bytes,
                max_subscribers=config.max_subscribers,
                queue_size=config.queue_size,
            ),
        )
        self._entries[handle.invocation_id] = _Entry(
            log, handle.owner_principal, handle.workflow_fingerprint
        )
        return log

    def end(self, handle: ExecutionHandle, reason: str) -> None:
        entry = self._entries.get(handle.invocation_id)
        if entry is not None and entry.owner == handle.owner_principal:
            entry.log.finish(reason)
            entry.ended_at = monotonic()

    def subscribe(
        self, handle: ExecutionHandle, *, last_event_id: str | None = None
    ) -> tuple[tuple[NativeStreamFrame, ...], NativeStreamSubscription]:
        self._prune()
        entry = self._entries.get(handle.invocation_id)
        if (
            self._closed
            or entry is None
            or entry.owner != handle.owner_principal
            or entry.fingerprint != handle.workflow_fingerprint
        ):
            raise LookupError("native observations are not retained in this worker")
        if len(self._consumers) >= self.configuration.max_subscribers:
            raise RuntimeError("native observation subscriber capacity is exhausted")
        backlog, subscription = entry.log.subscribe(last_event_id=last_event_id)
        self._consumers.add(subscription)
        return backlog, subscription

    def release(self, subscription: NativeStreamSubscription) -> None:
        subscription.close()
        self._consumers.discard(subscription)

    def close(self) -> None:
        self._closed = True
        for entry in self._entries.values():
            entry.log.close()
        self._entries.clear()
        for subscription in tuple(self._consumers):
            self.release(subscription)
