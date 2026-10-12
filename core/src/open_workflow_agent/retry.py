"""Typed private realization of the pinned OWS retry policy."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from .errors import WorkflowExecutionError, WorkflowSemanticError


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_retries: int | None
    duration: float | None = None
    attempt_duration: float | None = None
    delay: float = 0
    backoff: Literal["constant", "linear", "exponential"] = "constant"
    jitter_from: float = 0
    jitter_to: float = 0

    @classmethod
    def from_value(cls, value: Any, *, duration: Callable[[Any], float | None]) -> RetryPolicy:
        if value is None:
            return cls(max_retries=0)
        if not isinstance(value, Mapping):
            raise WorkflowSemanticError("retry policy must be a resolved object")
        limit = value.get("limit", {})
        attempt = limit.get("attempt", {})
        count = attempt.get("count")
        if count is not None and (type(count) is not int or count < 0):
            raise WorkflowSemanticError("retry count must be a nonnegative integer")

        def seconds(raw: Any) -> float | None:
            result = duration(raw)
            if result is not None and (not math.isfinite(result) or result < 0):
                raise WorkflowSemanticError("retry durations must be finite and nonnegative")
            return result

        backoff = value.get("backoff", {})
        strategy: Literal["constant", "linear", "exponential"] = (
            "exponential"
            if "exponential" in backoff
            else "linear"
            if "linear" in backoff
            else "constant"
        )
        jitter = value.get("jitter", {})
        lower = seconds(jitter.get("from")) or 0
        upper = seconds(jitter.get("to")) or 0
        if lower > upper:
            raise WorkflowSemanticError("retry jitter lower bound must not exceed its upper bound")
        return cls(
            max_retries=count,
            duration=seconds(limit.get("duration")),
            attempt_duration=seconds(attempt.get("duration")),
            delay=seconds(value.get("delay")) or 0,
            backoff=strategy,
            jitter_from=lower,
            jitter_to=upper,
        )

    def allows_retry(self, retries: int) -> bool:
        return self.max_retries is None or retries < self.max_retries

    def delay_for(self, retry: int, draw: Callable[[float, float], float]) -> float:
        try:
            base = (
                math.ldexp(self.delay, retry)
                if self.backoff == "exponential"
                else self.delay * (retry + 1)
                if self.backoff == "linear"
                else self.delay
            )
        except OverflowError:
            raise WorkflowExecutionError("retry backoff exceeds the supported duration") from None
        jitter = draw(self.jitter_from, self.jitter_to) if self.jitter_to else 0
        result = base + jitter
        if not math.isfinite(result) or not self.jitter_from <= jitter <= self.jitter_to:
            raise WorkflowExecutionError("retry delay is outside its declared bounds")
        return result
