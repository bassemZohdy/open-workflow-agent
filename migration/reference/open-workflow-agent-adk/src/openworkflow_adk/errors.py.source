"""Workflow error types and conversion helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class OpenWorkflowError(Exception):
    """A structured OpenWorkflow error that remains useful to ADK callers."""

    type: str = "about:blank"
    status: int | None = None
    title: str | None = None
    detail: str | None = None
    instance: str | None = None

    def __post_init__(self) -> None:
        super().__init__(self.detail or self.title or self.type)

    def as_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "type": self.type,
                "status": self.status,
                "title": self.title,
                "detail": self.detail,
                "instance": self.instance,
            }.items()
            if value is not None
        }

    @classmethod
    def from_exception(cls, error: Exception) -> OpenWorkflowError:
        if isinstance(error, cls):
            return error
        return cls(title=type(error).__name__, detail=str(error))
