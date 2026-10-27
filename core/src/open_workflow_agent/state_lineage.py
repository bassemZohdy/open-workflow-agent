"""Immutable lineage commands contain common identities and digests, never native state."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any

from .effects import EffectIdentity, effect_request_digest
from .errors import InvocationStateError, ModelError
from .model_responses import bounded_model_json
from .native_lineage_policy import EffectPolicy, LineageKind
from .security import _valid_bounded_reference

PROFILE = "owa/state-lineage/v1"
PREFIX = "policy:state-lineage:v1:"
_HEX = re.compile(r"[a-f0-9]{64}")


def lineage_error(reason: str) -> InvocationStateError:
    return InvocationStateError(
        "state lineage evidence is unavailable or inconsistent",
        details={"stage": "state_lineage", "reason": reason},
    )


def _json(value: Any, limit: int = 131_072) -> str:
    try:
        return bounded_model_json(value, limit=limit)
    except (ModelError, ValueError, RecursionError):
        raise lineage_error("data_invalid") from None


@dataclass(frozen=True, slots=True)
class StateLineageRequest:
    source_invocation_id: str
    target_invocation_id: str
    workflow_fingerprint: str
    request_id: str
    source_reference: str
    source_basis_digest: str
    policy_digest: str
    storage_identity: str
    kind: LineageKind
    effect_policy: EffectPolicy
    acknowledge_reexecution: bool
    task_reference: str | None = None
    values_json: str | None = None

    def __post_init__(self) -> None:
        if (
            any(
                not _valid_bounded_reference(value, maximum_bytes=256)
                for value in (
                    self.source_invocation_id,
                    self.target_invocation_id,
                    self.workflow_fingerprint,
                )
            )
            or self.source_invocation_id == self.target_invocation_id
            or any(
                type(value) is not str or not _HEX.fullmatch(value)
                for value in (
                    self.request_id,
                    self.source_reference,
                    self.source_basis_digest,
                    self.policy_digest,
                    self.storage_identity,
                )
            )
            or type(self.kind) is not str
            or self.kind not in {"replay", "branch"}
            or type(self.effect_policy) is not str
            or self.effect_policy not in {"read_only", "reexecute"}
            or type(self.acknowledge_reexecution) is not bool
            or self.acknowledge_reexecution != (self.effect_policy == "reexecute")
        ):
            raise lineage_error("request_invalid")
        if self.kind == "replay":
            if self.task_reference is not None or self.values_json is not None:
                raise lineage_error("replay_cannot_update")
        else:
            if (
                type(self.task_reference) is not str
                or len(self.task_reference.encode()) > 4096
                or not re.fullmatch(
                    r"/do/(0|[1-9][0-9]*)/(?:[^/~\x00-\x1f]|~[01])+", self.task_reference
                )
                or type(self.values_json) is not str
                or len(self.values_json.encode()) > 65_536
            ):
                raise lineage_error("update_invalid")
            try:
                values = json.loads(self.values_json)
                if type(values) is not dict or _json(values, 65_536) != self.values_json:
                    raise lineage_error("update_invalid")
            except (ValueError, RecursionError):
                raise lineage_error("update_invalid") from None
        _json(self.descriptor)

    @property
    def identity(self) -> EffectIdentity:
        return EffectIdentity(
            self.source_invocation_id,
            self.workflow_fingerprint,
            "/do",
            (("/do", 1),),
            PREFIX + self.request_id,
        )

    @property
    def descriptor(self) -> dict[str, Any]:
        return {"profile": PROFILE, **asdict(self)}

    @classmethod
    def from_descriptor(cls, value: Any) -> StateLineageRequest:
        if (
            type(value) is not dict
            or set(value) != {"profile", *cls.__dataclass_fields__}
            or value["profile"] != PROFILE
        ):
            raise lineage_error("request_invalid")
        return cls(**{key: item for key, item in value.items() if key != "profile"})

    @property
    def request_digest(self) -> str:
        return effect_request_digest(PROFILE, self.descriptor)

    @property
    def values(self) -> dict[str, Any] | None:
        return json.loads(self.values_json) if self.values_json is not None else None
