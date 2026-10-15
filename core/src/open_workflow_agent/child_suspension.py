"""Private parent-owned suspension evidence for nonresident child waits."""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from typing import Any
from uuid import UUID

from .effects import EffectIdentity
from .errors import WorkflowExecutionError


@dataclass(frozen=True, slots=True)
class _ChildSuspensionEvidence:
    identity: EffectIdentity
    child_id: str
    request_digest: str
    deadline_at: float | None


class ChildSuspension(BaseException):
    """Carry immutable parent-owned evidence outside ordinary fault handling.

    This is private control flow, not a public resume token or an admission
    decision. Delivery must verify the recorded child binding, quotas,
    request and original deadline before using this signal. Traceback/context
    attributes remain writable for Python exception handling.
    """

    __slots__ = ("_evidence",)
    _evidence: _ChildSuspensionEvidence

    def __init__(
        self,
        identity: EffectIdentity,
        child_id: str,
        request_digest: str,
        *,
        deadline_at: float | None = None,
    ) -> None:
        try:
            if (
                not isinstance(identity, EffectIdentity)
                or identity.operation != "run:workflow"
                or not isinstance(identity.occurrence, tuple)
                or any(not isinstance(item, tuple) for item in identity.occurrence)
                or not isinstance(child_id, str)
                or str(UUID(child_id)) != child_id
                or child_id == identity.invocation_id
                or not isinstance(request_digest, str)
                or re.fullmatch(r"[a-f0-9]{64}", request_digest) is None
                or (
                    deadline_at is not None
                    and (
                        isinstance(deadline_at, bool)
                        or not isinstance(deadline_at, (int, float))
                        or not math.isfinite(deadline_at)
                        or deadline_at <= 0
                    )
                )
            ):
                raise ValueError("invalid child suspension fields")
            # Revalidate identity values and retain a distinct immutable value.
            validated_identity = EffectIdentity.from_value(json.loads(json.dumps(asdict(identity))))
        except (TypeError, ValueError, KeyError, OverflowError):
            raise WorkflowExecutionError("child suspension evidence is inconsistent") from None
        object.__setattr__(
            self,
            "_evidence",
            _ChildSuspensionEvidence(
                validated_identity,
                child_id,
                request_digest,
                float(deadline_at) if deadline_at is not None else None,
            ),
        )
        super().__init__(validated_identity.key)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in {"_evidence", "identity", "child_id", "request_digest", "deadline_at"}:
            raise AttributeError("child suspension evidence is immutable")
        super().__setattr__(name, value)

    def __delattr__(self, name: str) -> None:
        if name in {"_evidence", "identity", "child_id", "request_digest", "deadline_at"}:
            raise AttributeError("child suspension evidence is immutable")
        super().__delattr__(name)

    @property
    def identity(self) -> EffectIdentity:
        return self._evidence.identity

    @property
    def child_id(self) -> str:
        return self._evidence.child_id

    @property
    def request_digest(self) -> str:
        return self._evidence.request_digest

    @property
    def deadline_at(self) -> float | None:
        return self._evidence.deadline_at

    def as_value(self) -> dict[str, Any]:
        return {
            "profile": "owa/child-suspension/v1",
            "identity": json.loads(json.dumps(asdict(self.identity))),
            "child_id": self.child_id,
            "request_digest": self.request_digest,
            "deadline_at": self.deadline_at,
        }

    @classmethod
    def from_value(cls, value: Any) -> ChildSuspension:
        try:
            if (
                not isinstance(value, dict)
                or set(value)
                != {"profile", "identity", "child_id", "request_digest", "deadline_at"}
                or value["profile"] != "owa/child-suspension/v1"
            ):
                raise ValueError("invalid child suspension transport")
            return cls(
                EffectIdentity.from_value(value["identity"]),
                value["child_id"],
                value["request_digest"],
                deadline_at=value["deadline_at"],
            )
        except (TypeError, ValueError, KeyError):
            raise WorkflowExecutionError("child suspension evidence is inconsistent") from None
