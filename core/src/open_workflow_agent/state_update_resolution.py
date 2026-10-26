"""Immutable owner decisions about an already dispatched state control."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING, Any, Literal

from .effects import EffectIdentity, EffectReceipt, effect_request_digest
from .errors import InvocationStateError
from .security import _valid_bounded_reference

if TYPE_CHECKING:
    from .state_updates import StateUpdateRequest

PROFILE = "owa/state-update-resolution/v1"
PREFIX = "policy:state-resolution:v1:"


@dataclass(frozen=True, slots=True)
class StateUpdateResolution:
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
            raise ValueError("state resolution requires an explicit finite decision")

    @property
    def descriptor(self) -> dict[str, Any]:
        return {"profile": PROFILE, **asdict(self)}

    @classmethod
    def from_descriptor(cls, value: Any) -> StateUpdateResolution:
        if (
            type(value) is not dict
            or set(value)
            != {"profile", "operation_id", "principal", "decision", "acknowledge_unknown"}
            or value["profile"] != PROFILE
        ):
            raise ValueError("state resolution descriptor is invalid")
        return cls(**{key: item for key, item in value.items() if key != "profile"})


def resolution_identity(identity: EffectIdentity) -> EffectIdentity:
    from .state_updates import PREFIX as UPDATE_PREFIX

    if not identity.operation.startswith(UPDATE_PREFIX):
        raise ValueError("state resolution requires a state control identity")
    return replace(identity, operation=PREFIX + identity.operation[len(UPDATE_PREFIX) :])


def resolution_request_digest(request: StateUpdateRequest, decision: StateUpdateResolution) -> str:
    return effect_request_digest(
        PROFILE, {"request_digest": request.request_digest, "resolution": decision.descriptor}
    )


def resolution_receipt(
    request: StateUpdateRequest, decision: StateUpdateResolution, authorization_digest: str
) -> EffectReceipt:
    value = {
        "profile": PROFILE,
        "control_key": request.identity.key,
        "request_digest": request.request_digest,
        "storage_identity": request.storage_identity,
        "authorization_digest": authorization_digest,
        "resolution": decision.descriptor,
    }
    return EffectReceipt(
        effect_request_digest(PROFILE, value),
        "state-resolution:v1",
        status="completed",
        output=value,
    )


def abandonment_error(digest: str) -> InvocationStateError:
    return InvocationStateError(
        "state control was abandoned with an unknown outcome",
        details={
            "stage": "state_update",
            "reason": "control_abandoned",
            "outcome": "unknown",
            "resolution_digest": digest,
        },
    )


def abandoned_receipt(request: StateUpdateRequest, audit: EffectReceipt) -> EffectReceipt:
    error = abandonment_error(audit.request_digest)
    return EffectReceipt(
        request.request_digest,
        "state-update:v1",
        status="faulted",
        error=error.as_dict(),
        error_status_code=error.status_code,
    )
