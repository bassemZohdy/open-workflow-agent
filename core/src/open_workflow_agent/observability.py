"""Framework-neutral lifecycle events for workflow execution."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

CLOUD_EVENTS_SPECVERSION = "1.0"
LIFECYCLE_CLOUD_EVENT_SCHEMA = "urn:open-workflow-agent:schema:lifecycle:1"
_SAFE_PROGRESS_FIELDS = frozenset({"phase", "completed", "total"})


@dataclass(frozen=True, slots=True)
class WorkflowEvent:
    event_type: str
    invocation_id: str | None = None
    session_id: str | None = None
    workflow_name: str | None = None
    workflow_version: str | None = None
    task_name: str | None = None
    task_reference: str | None = None
    engine: str | None = None
    operation_id: str | None = None
    execution_id: str | None = None
    parent_invocation_id: str | None = None
    parent_task_reference: str | None = None
    event_id: str = field(default_factory=lambda: str(uuid4()))
    event_name: str | None = None
    duration: float | None = None
    status: str | None = None
    attempt: int | None = None
    progress: dict[str, Any] | None = None
    reason: str | None = None
    error: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}

    def to_cloud_event(self) -> CloudEvent:
        """Convert one common lifecycle event to a safe CloudEvents payload."""

        event_id = self.event_id or str(uuid4())
        data: dict[str, Any] = {
            "event_id": event_id,
            "event_type": self.event_type,
        }
        for name in (
            "invocation_id",
            "session_id",
            "workflow_name",
            "workflow_version",
            "task_name",
            "task_reference",
            "engine",
            "operation_id",
            "execution_id",
            "parent_invocation_id",
            "parent_task_reference",
            "event_name",
            "duration",
            "status",
            "attempt",
            "reason",
        ):
            value = getattr(self, name)
            if value is not None:
                data[name] = value
        if isinstance(self.progress, dict):
            progress = {
                key: value
                for key, value in self.progress.items()
                if key in _SAFE_PROGRESS_FIELDS and isinstance(value, (bool, int, float, str))
            }
            if progress:
                data["progress"] = progress
        if isinstance(self.error, dict):
            code = self.error.get("code")
            if isinstance(code, str):
                data["error"] = {"code": code}
        return CloudEvent(
            specversion=CLOUD_EVENTS_SPECVERSION,
            id=event_id,
            source="urn:open-workflow-agent:lifecycle",
            type=f"com.openworkflow.agent.lifecycle.{_cloud_event_type(self.event_type)}.v1",
            subject=self.invocation_id or self.task_reference or self.workflow_name,
            time=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            datacontenttype="application/json",
            dataschema=LIFECYCLE_CLOUD_EVENT_SCHEMA,
            data=data,
        )


@dataclass(frozen=True, slots=True)
class CloudEvent:
    """CloudEvents 1.0 structured JSON representation."""

    specversion: str
    id: str
    source: str
    type: str
    subject: str | None
    time: str
    datacontenttype: str
    dataschema: str
    data: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "specversion": self.specversion,
            "id": self.id,
            "source": self.source,
            "type": self.type,
            "time": self.time,
            "datacontenttype": self.datacontenttype,
            "dataschema": self.dataschema,
            "data": self.data,
        }
        if self.subject is not None:
            value["subject"] = self.subject
        return value

    def raw(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _cloud_event_type(event_type: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", ".", event_type).lower()


class EventSink(Protocol):
    def emit(self, event: WorkflowEvent) -> None: ...


class NullEventSink:
    def emit(self, event: WorkflowEvent) -> None:
        return None


class InMemoryEventSink:
    """Deterministic event sink used by tests and embedding applications."""

    def __init__(self) -> None:
        self.events: list[WorkflowEvent] = []

    def emit(self, event: WorkflowEvent) -> None:
        self.events.append(event)


class LifecycleCloudEventSink:
    """Forward common events and retain a bounded CloudEvents snapshot."""

    def __init__(self, downstream: EventSink, *, max_events: int = 1000) -> None:
        self.downstream = downstream
        self.max_events = max_events
        self.events: list[CloudEvent] = []

    def emit(self, event: WorkflowEvent) -> None:
        if (
            event.event_type == "SandboxExecutionFailed"
            and isinstance(event.error, dict)
            and event.error.get("code") == "invocation_cancelled"
        ):
            event = replace(
                event,
                event_type="SandboxExecutionCancelled",
                status="cancelled",
                reason="cancelled",
            )
        self.downstream.emit(event)
        self.events.append(event.to_cloud_event())
        if len(self.events) > self.max_events:
            del self.events[: len(self.events) - self.max_events]

    def snapshot(self, limit: int = 100) -> list[CloudEvent]:
        return list(self.events[-limit:])
