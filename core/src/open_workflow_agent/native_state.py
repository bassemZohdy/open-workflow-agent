"""Typed public state queries; checkpoint storage and projection belong to adapters."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

from .errors import OwaError
from .expressions import validate_expression_data

if TYPE_CHECKING:
    from .config import NativeStateConfig
    from .persistence import ExecutionHandle
    from .workflow import WorkflowPlan

CAPABILITY = "langgraph.state.read@1.0.0"
_REFERENCE = re.compile(r"^[a-f0-9]{64}$")


class NativeStateUnavailable(OwaError):
    code = "native_state_unavailable"
    status_code = 409


class NativeStateCapacity(OwaError):
    code = "native_state_capacity_exceeded"
    status_code = 429


@dataclass(frozen=True, slots=True)
class NativeStateQuery:
    kind: Literal["state", "history"] = "state"
    reference: str | None = None
    before: str | None = None
    limit: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or self.kind not in {"state", "history"}:
            raise ValueError("native state query kind is invalid")
        if type(self.limit) is not int or not 1 <= self.limit <= 100:
            raise ValueError("native state query page is outside its bound")
        for reference in (self.reference, self.before):
            if reference is not None and (
                not isinstance(reference, str) or not _REFERENCE.fullmatch(reference)
            ):
                raise ValueError("native state reference is invalid")
        if (self.kind == "state" and (self.before is not None or self.limit != 1)) or (
            self.kind == "history" and self.reference is not None
        ):
            raise ValueError("native state query selectors are inconsistent")


@dataclass(frozen=True, slots=True)
class NativeStateResult:
    payload_json: str

    def __post_init__(self) -> None:
        if not isinstance(self.payload_json, str) or len(self.payload_json.encode()) > 16_777_216:
            raise ValueError("native state result exceeds its transport bound")
        value = json.loads(self.payload_json)
        validate_expression_data(value)
        if not isinstance(value, dict) or value.get("profile") != CAPABILITY:
            raise ValueError("native state result has no supported profile")

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = json.loads(self.payload_json)
        return value


class NativeStateReader(Protocol):
    def verify(self, configuration: NativeStateConfig | None) -> None: ...

    async def read(
        self, handle: ExecutionHandle, plan: WorkflowPlan, query: NativeStateQuery
    ) -> NativeStateResult: ...
