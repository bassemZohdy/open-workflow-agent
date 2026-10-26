"""Finite exact-field search requests, independent of native provider SDKs."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .effects import effect_request_digest
from .errors import ModelError
from .model_responses import bounded_model_json

PROFILE = "owa/memory-search/v1"


@dataclass(frozen=True, slots=True)
class MemorySearchQuery:
    filter_json: str = "{}"
    limit: int = 20

    def __post_init__(self) -> None:
        if (
            type(self.limit) is not int
            or not 1 <= self.limit <= 64
            or type(self.filter_json) is not str
        ):
            raise ValueError("native memory search requires finite result and filter bounds")
        try:
            if len(self.filter_json.encode("utf-8")) > 4096:
                raise ValueError("oversized filter")
            value = json.loads(self.filter_json)
            if (
                type(value) is not dict
                or len(value) > 16
                or any(
                    type(key) is not str
                    or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", key)
                    or type(item) not in (str, bool, int, float, type(None))
                    for key, item in value.items()
                )
            ):
                raise ValueError("invalid filter")
            canonical = bounded_model_json(dict(sorted(value.items())), limit=4096)
            if self.filter_json != canonical:
                raise ValueError("noncanonical filter")
        except Exception:
            raise ValueError(
                "native memory search requires a bounded scalar equality filter"
            ) from None

    @classmethod
    def create(cls, filter: Any = None, limit: int = 20) -> MemorySearchQuery:
        value = {} if filter is None else filter
        if type(value) is not dict or any(type(key) is not str for key in value):
            raise ValueError("native memory search requires an object filter")
        try:
            return cls(bounded_model_json(dict(sorted(value.items())), limit=4096), limit)
        except ModelError:
            raise ValueError("native memory search requires a bounded JSON filter") from None

    @property
    def filter(self) -> dict[str, Any]:
        return dict(json.loads(self.filter_json))

    @property
    def descriptor(self) -> dict[str, Any]:
        return {"profile": PROFILE, "filter": self.filter, "limit": self.limit}

    @property
    def key(self) -> str:
        return effect_request_digest(PROFILE, self.descriptor)

    @classmethod
    def from_descriptor(cls, value: Any) -> MemorySearchQuery:
        if (
            type(value) is not dict
            or set(value) != {"profile", "filter", "limit"}
            or value["profile"] != PROFILE
            or type(value["filter"]) is not dict
        ):
            raise ValueError("native memory search descriptor is invalid")
        return cls.create(value["filter"], value["limit"])
