"""Framework-neutral lifecycle events for workflow execution."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Protocol


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
    engine_execution_reference: str | None = None
    duration: float | None = None
    status: str | None = None
    attempt: int | None = None
    error: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


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
