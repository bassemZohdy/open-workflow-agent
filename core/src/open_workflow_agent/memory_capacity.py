"""Versioned deployment limits for one principal/collection native namespace."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .effects import effect_request_digest

PROFILE = "owa/memory-namespace-bounds/v1"
TABLE = "owa_memory_namespaces_v1"


@dataclass(frozen=True, slots=True)
class MemoryNamespaceBounds:
    id: str
    version: str
    max_records: int = 1000
    max_value_bytes: int = 16_777_216
    cleanup_limit: int = 32

    def __post_init__(self) -> None:
        if (
            any(
                type(value) is not str
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,127}", value)
                for value in (self.id, self.version)
            )
            or type(self.max_records) is not int
            or not 1 <= self.max_records <= 10_000
            or type(self.max_value_bytes) is not int
            or not 1 <= self.max_value_bytes <= 67_108_864
            or type(self.cleanup_limit) is not int
            or not 1 <= self.cleanup_limit <= 64
        ):
            raise ValueError("memory namespace bounds require a versioned finite policy")

    @property
    def descriptor(self) -> dict[str, Any]:
        return {
            "profile": PROFILE,
            "id": self.id,
            "version": self.version,
            "max_records": self.max_records,
            "max_value_bytes": self.max_value_bytes,
            "cleanup_limit": self.cleanup_limit,
        }

    @property
    def digest(self) -> str:
        return effect_request_digest(PROFILE, self.descriptor)

    @classmethod
    def from_descriptor(cls, raw: Any) -> MemoryNamespaceBounds:
        if (
            type(raw) is not dict
            or set(raw)
            != {"profile", "id", "version", "max_records", "max_value_bytes", "cleanup_limit"}
            or raw["profile"] != PROFILE
        ):
            raise ValueError("memory namespace bounds profile is unsupported or invalid")
        return cls(
            raw["id"],
            raw["version"],
            raw["max_records"],
            raw["max_value_bytes"],
            raw["cleanup_limit"],
        )
