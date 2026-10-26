"""Immutable local-model and exact cosine-index contract; no provider objects."""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from typing import Any

MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DIMENSIONS = 384
PROFILE = "owa/local-minilm-memory/v1"


@dataclass(frozen=True, slots=True)
class MemorySemanticIndex:
    field: str = "text"
    max_text_bytes: int = 4096
    timeout_seconds: float = 10.0
    artifact_sha256: str = ""

    def __post_init__(self) -> None:
        if (
            type(self.field) is not str
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", self.field)
            or type(self.max_text_bytes) is not int
            or not 1 <= self.max_text_bytes <= 16_384
            or type(self.timeout_seconds) not in (int, float)
            or not math.isfinite(self.timeout_seconds)
            or not 0 < self.timeout_seconds <= 30
            or type(self.artifact_sha256) is not str
            or (self.artifact_sha256 and not re.fullmatch(r"[a-f0-9]{64}", self.artifact_sha256))
        ):
            raise ValueError("semantic memory requires a bounded installed local index")
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))

    def validate_text(self, text: Any) -> str:
        if (
            type(text) is not str
            or not text.strip()
            or len(text.encode("utf-8")) > self.max_text_bytes
        ):
            raise ValueError("semantic memory requires bounded nonempty text")
        return text

    def text(self, value: Any) -> str:
        if type(value) is not dict:
            raise ValueError("semantic memory requires an object record")
        return self.validate_text(value.get(self.field))

    @property
    def descriptor(self) -> dict[str, Any]:
        return {
            "profile": PROFILE,
            "model": MODEL,
            "dimensions": DIMENSIONS,
            "distance": "cosine",
            "index": "exact",
            **asdict(self),
        }

    @classmethod
    def from_descriptor(cls, value: Any) -> MemorySemanticIndex:
        if type(value) is not dict or set(value) != set(cls().descriptor):
            raise ValueError("semantic memory index descriptor is invalid")
        result = cls(
            value["field"],
            value["max_text_bytes"],
            value["timeout_seconds"],
            value["artifact_sha256"],
        )
        if (
            value != result.descriptor
            or type(value["dimensions"]) is not int
            or not result.artifact_sha256
        ):
            raise ValueError("semantic memory index version or model differs")
        return result
