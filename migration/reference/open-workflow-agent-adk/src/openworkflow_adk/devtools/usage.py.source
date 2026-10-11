"""Opt-in, privacy-preserving usage counters."""

from __future__ import annotations

from collections import Counter
from typing import Any


class UsageMetrics:
    """Collect aggregate counters only when explicitly enabled."""

    def __init__(self, *, enabled: bool = False) -> None:
        self.enabled = enabled
        self.counters: Counter[str] = Counter()

    def record(self, event: str, **dimensions: Any) -> None:
        if not self.enabled:
            return
        # Do not accept user identifiers or workflow payloads as dimensions.
        safe = {key: str(value) for key, value in dimensions.items() if key in {"kind", "status"}}
        key = event + (":" + ":".join(f"{k}={safe[k]}" for k in sorted(safe)) if safe else "")
        self.counters[key] += 1

    def snapshot(self) -> dict[str, int]:
        return dict(self.counters)
