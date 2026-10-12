"""Private immutable policy clocks, independent of native checkpoint formats."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .effects import EffectIdentity
from .errors import WorkflowExecutionError


@dataclass(frozen=True, slots=True)
class DeadlineAnchor:
    started_at: float
    duration: float

    @classmethod
    def from_value(cls, value: Any, duration: float) -> DeadlineAnchor:
        if not isinstance(value, dict) or set(value) != {"profile", "started_at", "duration"}:
            raise WorkflowExecutionError("workflow deadline record is inconsistent")
        started = value["started_at"]
        if (
            value["profile"] != "owa/deadline/v1"
            or isinstance(started, bool)
            or not isinstance(started, (int, float))
            or not math.isfinite(started)
            or started <= 0
            or value["duration"] != duration
            or not math.isfinite(duration)
            or duration < 0
            or not math.isfinite(started + duration)
        ):
            raise WorkflowExecutionError("workflow deadline record is inconsistent")
        return cls(float(started), duration)

    def remaining(self, now: float) -> float:
        return max(0.0, self.started_at + self.duration - now)


def root_clock_identity(invocation_id: str, fingerprint: str) -> EffectIdentity:
    return EffectIdentity(
        invocation_id, fingerprint, "/workflow", (("/workflow", 1),), "policy:clock:v1"
    )


def clock_started_at(value: Any) -> float:
    if (
        not isinstance(value, dict)
        or set(value) != {"profile", "started_at"}
        or value["profile"] != "owa/timing/v1"
        or isinstance(value["started_at"], bool)
        or not isinstance(value["started_at"], (int, float))
        or not math.isfinite(value["started_at"])
        or value["started_at"] <= 0
    ):
        raise WorkflowExecutionError("workflow policy clock is inconsistent")
    return float(value["started_at"])


@dataclass(frozen=True, slots=True)
class RetrySchedule:
    retry: int
    delay: float
    due_at: float
    allowed: bool

    @classmethod
    def from_value(cls, value: Any, retry: int) -> RetrySchedule:
        if (
            not isinstance(value, dict)
            or set(value) != {"profile", "retry", "delay", "due_at", "allowed", "scheduled_at"}
            or value["profile"] != "owa/retry-schedule/v1"
            or type(value["retry"]) is not int
            or value["retry"] != retry
            or type(value["allowed"]) is not bool
            or any(
                isinstance(value[key], bool)
                or not isinstance(value[key], (int, float))
                or not math.isfinite(value[key])
                for key in ("delay", "due_at", "scheduled_at")
            )
            or value["delay"] < 0
            or value["due_at"] <= 0
            or value["scheduled_at"] <= 0
            or value["due_at"] != value["scheduled_at"] + value["delay"]
        ):
            raise WorkflowExecutionError("workflow retry schedule is inconsistent")
        return cls(retry, float(value["delay"]), float(value["due_at"]), value["allowed"])
