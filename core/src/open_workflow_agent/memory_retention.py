"""Versioned deployment policy for native memory record expiry, not history."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

PROFILE = "owa/memory-record-retention/v1"


@dataclass(frozen=True, slots=True)
class MemoryRecordRetention:
    id: str
    version: str
    ttl_seconds: int

    def __post_init__(self) -> None:
        if (
            any(
                type(value) is not str
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,127}", value)
                for value in (self.id, self.version)
            )
            or type(self.ttl_seconds) is not int
            or not 1 <= self.ttl_seconds <= 31_536_000
        ):
            raise ValueError("memory record retention requires a versioned finite policy")

    @property
    def descriptor(self) -> dict[str, Any]:
        return {
            "profile": PROFILE,
            "id": self.id,
            "version": self.version,
            "ttl_seconds": self.ttl_seconds,
        }

    @classmethod
    def from_descriptor(cls, raw: Any) -> MemoryRecordRetention:
        if (
            type(raw) is not dict
            or set(raw) != {"profile", "id", "version", "ttl_seconds"}
            or raw["profile"] != PROFILE
        ):
            raise ValueError("memory record retention profile is unsupported or invalid")
        return cls(raw["id"], raw["version"], raw["ttl_seconds"])
