"""Immutable explicit decisions about an already dispatched native write."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, replace
from typing import Any, Literal

from .effects import EffectIdentity, EffectReceipt, effect_request_digest
from .errors import MemoryError
from .memory_mutations import MemoryMutation
from .security import _valid_bounded_reference

PROFILE = "owa/memory-write-resolution/v1"
PREFIX = "policy:memory-resolution:v1:"


@dataclass(frozen=True, slots=True)
class MemoryWriteResolution:
    """Trusted caller attestation, not a workflow input or proof of nonexecution."""

    operation_id: str
    principal: str
    decision: Literal["complete", "abandon_unknown"]
    acknowledge_unknown: bool = False

    def __post_init__(self) -> None:
        if (
            type(self.operation_id) is not str
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", self.operation_id)
            or not _valid_bounded_reference(self.principal, maximum_bytes=256)
            or type(self.decision) is not str
            or self.decision not in {"complete", "abandon_unknown"}
            or type(self.acknowledge_unknown) is not bool
            or self.acknowledge_unknown != (self.decision == "abandon_unknown")
        ):
            raise ValueError("memory resolution requires an explicit finite decision")

    @property
    def descriptor(self) -> dict[str, Any]:
        return {"profile": PROFILE, **asdict(self)}

    @classmethod
    def from_descriptor(cls, value: Any) -> MemoryWriteResolution:
        if (
            type(value) is not dict
            or set(value)
            != {"profile", "operation_id", "principal", "decision", "acknowledge_unknown"}
            or value["profile"] != PROFILE
        ):
            raise ValueError("memory resolution descriptor is invalid")
        return cls(**{key: item for key, item in value.items() if key != "profile"})


def resolution_identity(identity: EffectIdentity) -> EffectIdentity:
    old = "policy:memory-mutation:v1:"
    if not identity.operation.startswith(old):
        raise ValueError("memory resolution requires a mutation identity")
    return replace(identity, operation=PREFIX + identity.operation[len(old) :])


def resolution_receipt(
    request: MemoryMutation, storage_identity: str, resolution: MemoryWriteResolution
) -> EffectReceipt:
    value = {
        "profile": PROFILE,
        "mutation": json.loads(json.dumps(asdict(request.identity))),
        "request_digest": request.request_digest,
        "storage_identity": storage_identity,
        "resolution": resolution.descriptor,
    }
    return EffectReceipt(
        effect_request_digest(PROFILE, value),
        "memory-resolution:v1",
        status="completed",
        output=value,
    )


def abandonment_error(digest: str) -> MemoryError:
    return MemoryError(
        "memory write was explicitly abandoned with an unknown outcome",
        details={"reason": "write_abandoned", "outcome": "unknown", "resolution_digest": digest},
    )


def abandoned_receipt(request: MemoryMutation, resolution: EffectReceipt) -> EffectReceipt:
    error = abandonment_error(resolution.request_digest)
    return EffectReceipt(
        request.request_digest,
        "memory-write:v1",
        status="faulted",
        error=error.as_dict(),
        error_status_code=error.status_code,
    )
