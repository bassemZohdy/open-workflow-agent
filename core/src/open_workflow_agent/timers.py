"""Private common timer decisions; native checkpoint state stays in adapters."""

from __future__ import annotations

import json
import math
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, replace
from typing import Any, Literal

from ._effect_storage import insert_completed_receipt
from .effects import EffectIdentity, EffectJournal, EffectReceipt, effect_request_digest
from .errors import WorkflowExecutionError
from .expressions import validate_expression_data
from .ownership import ExecutionLease, owned_write_transaction


class TimerSuspension(BaseException):
    """Release an execution after acknowledged timer and native waiting writes.

    This is private control flow, not a workflow error or public resume token.
    A BaseException deliberately escapes ordinary task fault/retry translation.
    """

    def __init__(self, identity: EffectIdentity, due_at: float) -> None:
        if (
            identity.operation != "wait:arm:v1"
            or isinstance(due_at, bool)
            or not isinstance(due_at, (int, float))
            or not math.isfinite(due_at)
            or due_at <= 0
        ):
            raise WorkflowExecutionError("timer suspension evidence is inconsistent")
        self.identity = identity
        self.due_at = float(due_at)
        super().__init__(identity.key)

    @property
    def timer_key(self) -> str:
        return self.identity.key

    def as_value(self) -> dict[str, Any]:
        return {
            "profile": "owa/timer-suspension/v1",
            "identity": json.loads(json.dumps(asdict(self.identity))),
            "due_at": self.due_at,
        }

    @classmethod
    def from_value(cls, value: Any) -> TimerSuspension:
        try:
            if (
                not isinstance(value, dict)
                or set(value) != {"profile", "identity", "due_at"}
                or value["profile"] != "owa/timer-suspension/v1"
            ):
                raise ValueError("invalid timer suspension fields")
            return cls(EffectIdentity.from_value(value["identity"]), value["due_at"])
        except (TypeError, ValueError, KeyError):
            raise WorkflowExecutionError("timer suspension evidence is inconsistent") from None


@dataclass(frozen=True, slots=True)
class TimerRegistration:
    identity: EffectIdentity
    armed_at: float
    due_at: float
    duration: float
    receipt: EffectReceipt
    workflow_source_json: str
    wake_at: float
    deadline_at: float | None

    @property
    def timer_key(self) -> str:
        return self.identity.key


@dataclass(frozen=True, slots=True)
class TimerWake:
    timer_key: str
    due_at: float
    reason: Literal["timer_due", "resume_requested"]
    resume_input: Any
    receipt: EffectReceipt

    def as_value(self) -> dict[str, Any]:
        return {"timer_key": self.timer_key, "due_at": self.due_at, "reason": self.reason}


def _json(value: Any) -> str:
    validate_expression_data(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


@dataclass(frozen=True, slots=True)
class TimerClaim:
    registration: TimerRegistration
    owner: str
    generation: int
    lease_until: float


_CURRENT_TIMER_CLAIM: ContextVar[TimerClaim | None] = ContextVar(
    "owa_timer_dispatch_claim", default=None
)


class TimerStore:
    """Atomic due/wake metadata and common receipts, never SDK checkpoint data."""

    def __init__(self, journal: EffectJournal, *, max_pending: int = 1000) -> None:
        if type(max_pending) is not int or max_pending < 1:
            raise ValueError("timer capacity must be positive")
        self.journal = journal
        self.connection = journal.connection
        self.max_pending = max_pending
        self._lock = threading.RLock()
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS workflow_timers (
            identity TEXT PRIMARY KEY, invocation_id TEXT NOT NULL,
            fingerprint TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL,
            due_at DOUBLE PRECISION NOT NULL, wake_at DOUBLE PRECISION NOT NULL,
            claim_owner TEXT, generation BIGINT NOT NULL,
            lease_until DOUBLE PRECISION)"""
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS workflow_timer_due ON workflow_timers(state, wake_at)"
        )
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS timer_admission (singleton INTEGER PRIMARY KEY)"
        )
        self.connection.execute(
            "INSERT INTO timer_admission(singleton) VALUES (1) ON CONFLICT(singleton) DO NOTHING"
        )
        self.connection.commit()

    @contextmanager
    def _transaction(
        self, identity: EffectIdentity | None, lease: ExecutionLease | None
    ) -> Iterator[None]:
        with self._lock:
            try:
                if lease is not None:
                    if identity is None:
                        raise WorkflowExecutionError("owned timer transaction requires identity")
                    with owned_write_transaction(
                        self.connection,
                        lease,
                        invocation_id=identity.invocation_id,
                        workflow_fingerprint=identity.workflow_fingerprint,
                    ):
                        self._serialize()
                        yield
                elif self.connection.is_postgresql:
                    with self.connection.raw.transaction():
                        self._serialize()
                        yield
                else:
                    self.connection.execute("BEGIN IMMEDIATE")
                    self._serialize()
                    yield
                    self.connection.commit()
            except BaseException:
                if not self.connection.is_postgresql:
                    self.connection.raw.rollback()
                raise

    def _serialize(self) -> None:
        query = "SELECT singleton FROM timer_admission WHERE singleton = 1"
        if self.connection.is_postgresql:
            query += " FOR UPDATE"
        if self.connection.execute(query).fetchone() != (1,):
            raise WorkflowExecutionError("timer admission evidence is inconsistent")

    def arm(
        self,
        identity: EffectIdentity,
        duration: float,
        *,
        now: float | None = None,
        workflow_source: Mapping[str, Any],
        execution_lease: ExecutionLease | None = None,
        deadline_at: float | None = None,
    ) -> TimerRegistration:
        current = time.time() if now is None else now
        if (
            identity.operation != "wait:arm:v1"
            or any(
                isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                for v in (duration, current)
            )
            or duration <= 0
            or current <= 0
            or not math.isfinite(current + duration)
            or (
                deadline_at is not None
                and (
                    isinstance(deadline_at, bool)
                    or not isinstance(deadline_at, (int, float))
                    or not math.isfinite(deadline_at)
                    or deadline_at <= current
                )
            )
        ):
            raise WorkflowExecutionError("timer arming request is invalid")
        source = _json(dict(workflow_source))
        digest = effect_request_digest(
            identity.operation,
            {
                "duration": float(duration),
                "workflow_fingerprint": identity.workflow_fingerprint,
                "source": json.loads(source),
                "deadline_at": deadline_at,
            },
        )
        with self._transaction(identity, execution_lease):
            existing = self.get(identity)
            if existing is not None:
                if existing.receipt.request_digest != digest:
                    raise WorkflowExecutionError("timer request differs from saved arming")
                return existing
            if self.journal.get(identity) is not None:
                raise WorkflowExecutionError("timer row is missing for its immutable receipt")
            count = self.connection.execute(
                "SELECT COUNT(*) FROM workflow_timers WHERE state IN ('pending', 'woken')"
            ).fetchone()[0]
            if count >= self.max_pending:
                raise WorkflowExecutionError("pending timer capacity is exhausted")
            output = {
                "profile": "owa/wait-arm/v1",
                "armed_at": float(current),
                "duration": float(duration),
                "due_at": float(current + duration),
                "workflow_fingerprint": identity.workflow_fingerprint,
                "deadline_at": deadline_at,
                "wake_at": min(current + duration, deadline_at)
                if deadline_at is not None
                else current + duration,
            }
            receipt = EffectReceipt(digest, "timer", status="completed", output=output)
            insert_completed_receipt(self.connection, identity, receipt)
            payload = {
                "identity": json.loads(json.dumps(asdict(identity))),
                "source": json.loads(source),
                "arming": output,
            }
            self.connection.execute(
                """INSERT INTO workflow_timers(identity,invocation_id,fingerprint,payload,state,
                due_at,wake_at,claim_owner,generation,lease_until)
                VALUES (?,?,?,?,?,?,?,NULL,0,NULL)""",
                (
                    identity.key,
                    identity.invocation_id,
                    identity.workflow_fingerprint,
                    _json(payload),
                    "pending",
                    current + duration,
                    output["wake_at"],
                ),
            )
            registration = self.get(identity)
            if registration is None:
                raise WorkflowExecutionError("timer arming was not recorded")
            return registration

    def get(self, identity: EffectIdentity) -> TimerRegistration | None:
        row = self.connection.execute(
            "SELECT invocation_id,fingerprint,payload,state,due_at,wake_at FROM workflow_timers "
            "WHERE identity = ?",
            (identity.key,),
        ).fetchone()
        if row is None:
            return None
        try:
            value = json.loads(row[2])
            arming = value["arming"]
            receipt = self.journal.get(identity)
            if (
                set(value) != {"identity", "source", "arming"}
                or EffectIdentity.from_value(value["identity"]) != identity
                or (row[0], row[1]) != (identity.invocation_id, identity.workflow_fingerprint)
                or row[3] not in {"pending", "woken", "consumed", "cancelled"}
                or identity.operation != "wait:arm:v1"
                or set(arming)
                != {
                    "profile",
                    "armed_at",
                    "duration",
                    "due_at",
                    "workflow_fingerprint",
                    "wake_at",
                    "deadline_at",
                }
                or arming["profile"] != "owa/wait-arm/v1"
                or arming["workflow_fingerprint"] != identity.workflow_fingerprint
                or any(
                    isinstance(arming[k], bool)
                    or not isinstance(arming[k], (int, float))
                    or not math.isfinite(arming[k])
                    or arming[k] <= 0
                    for k in ("armed_at", "duration", "due_at")
                )
                or arming["due_at"] != arming["armed_at"] + arming["duration"]
                or arming["due_at"] != row[4]
                or arming["wake_at"] != row[5]
                or isinstance(arming["wake_at"], bool)
                or arming["wake_at"]
                != min(
                    arming["due_at"],
                    arming["deadline_at"]
                    if arming["deadline_at"] is not None
                    else arming["due_at"],
                )
                or (
                    arming["deadline_at"] is not None
                    and (
                        isinstance(arming["deadline_at"], bool)
                        or not isinstance(arming["deadline_at"], (int, float))
                        or not math.isfinite(arming["deadline_at"])
                        or arming["deadline_at"] <= arming["armed_at"]
                    )
                )
                or receipt is None
                or receipt.status != "completed"
                or receipt.output != arming
                or receipt.request_digest
                != effect_request_digest(
                    identity.operation,
                    {
                        "duration": arming["duration"],
                        "workflow_fingerprint": identity.workflow_fingerprint,
                        "source": value["source"],
                        "deadline_at": arming["deadline_at"],
                    },
                )
            ):
                raise ValueError("invalid timer evidence")
            return TimerRegistration(
                identity,
                float(arming["armed_at"]),
                float(arming["due_at"]),
                float(arming["duration"]),
                receipt,
                _json(value["source"]),
                float(arming["wake_at"]),
                arming["deadline_at"],
            )
        except (TypeError, ValueError, KeyError):
            raise WorkflowExecutionError("timer arming evidence is inconsistent") from None

    def pending_for_invocation(
        self, invocation_id: str, fingerprint: str
    ) -> list[TimerRegistration]:
        self.verify_invocation(invocation_id, fingerprint)
        rows = self.connection.execute(
            "SELECT payload FROM workflow_timers WHERE invocation_id = ? AND fingerprint = ? "
            "AND state = 'pending' ORDER BY due_at,identity",
            (invocation_id, fingerprint),
        ).fetchall()
        result = []
        for row in rows:
            identity = EffectIdentity.from_value(json.loads(row[0])["identity"])
            registration = self.get(identity)
            if registration is None:
                raise WorkflowExecutionError("pending timer is unavailable")
            result.append(registration)
        return result

    def wake(
        self,
        registration: TimerRegistration,
        reason: Literal["timer_due", "resume_requested"],
        resume_input: Any = None,
        *,
        now: float | None = None,
        execution_lease: ExecutionLease | None = None,
    ) -> TimerWake:
        current = time.time() if now is None else now
        if reason not in {"timer_due", "resume_requested"} or not math.isfinite(current):
            raise WorkflowExecutionError("timer wake request is invalid")
        identity = registration.identity
        wake_identity = replace(identity, operation="wait:wake:v1")
        with self._transaction(identity, execution_lease):
            known = self.get(identity)
            if known is None or known.receipt != registration.receipt:
                raise WorkflowExecutionError("timer wake arming evidence is inconsistent")
            existing = self.journal.get(wake_identity)
            if existing is not None:
                return self._wake_value(known, existing)
            row = self.connection.execute(
                "SELECT state FROM workflow_timers WHERE identity = ?",
                (identity.key,),
            ).fetchone()
            if (
                row is None
                or row[0] != "pending"
                or (reason == "timer_due" and current < known.wake_at)
            ):
                raise WorkflowExecutionError("timer is not eligible for this wake")
            output = {
                "profile": "owa/wait-wake/v1",
                "timer_key": identity.key,
                "due_at": known.due_at,
                "reason": reason,
                "resume_input": resume_input if reason == "resume_requested" else None,
            }
            digest = effect_request_digest(
                wake_identity.operation,
                {
                    "arm_digest": known.receipt.request_digest,
                    "due_at": known.due_at,
                },
            )
            receipt = EffectReceipt(digest, "timer", status="completed", output=output)
            insert_completed_receipt(self.connection, wake_identity, receipt)
            self.connection.execute(
                "UPDATE workflow_timers SET state='woken',claim_owner=NULL,lease_until=NULL "
                "WHERE identity = ? AND state='pending'",
                (identity.key,),
            )
            return self._wake_value(known, receipt)

    @staticmethod
    def _wake_value(registration: TimerRegistration, receipt: EffectReceipt) -> TimerWake:
        value = receipt.output
        identity = replace(registration.identity, operation="wait:wake:v1")
        if (
            receipt.status != "completed"
            or not isinstance(value, dict)
            or set(value) != {"profile", "timer_key", "due_at", "reason", "resume_input"}
            or value["profile"] != "owa/wait-wake/v1"
            or value["timer_key"] != registration.timer_key
            or isinstance(value["due_at"], bool)
            or value["due_at"] != registration.due_at
            or value["reason"] not in {"timer_due", "resume_requested"}
            or (value["reason"] == "timer_due" and value["resume_input"] is not None)
            or receipt.request_digest
            != effect_request_digest(
                identity.operation,
                {
                    "arm_digest": registration.receipt.request_digest,
                    "due_at": registration.due_at,
                },
            )
        ):
            raise WorkflowExecutionError("timer wake evidence is inconsistent")
        return TimerWake(
            registration.timer_key,
            registration.due_at,
            value["reason"],
            value["resume_input"],
            receipt,
        )

    def verify_invocation(self, invocation_id: str, fingerprint: str) -> None:
        rows = self.connection.execute(
            "SELECT identity,payload,state FROM workflow_timers WHERE invocation_id = ?",
            (invocation_id,),
        ).fetchall()
        indexed = self.connection.execute(
            "SELECT identity,payload FROM workflow_effect_invocations WHERE invocation_id = ?",
            (invocation_id,),
        ).fetchall()
        arms: set[str] = set()
        wakes: set[str] = set()
        for key, payload in indexed:
            identity = EffectIdentity.from_value(json.loads(payload))
            if identity.operation in {"wait:arm:v1", "wait:wake:v1"}:
                if identity.workflow_fingerprint != fingerprint or key != identity.key:
                    raise WorkflowExecutionError("timer identity index is inconsistent")
                (arms if identity.operation == "wait:arm:v1" else wakes).add(key)
        if arms != {r[0] for r in rows}:
            raise WorkflowExecutionError("timer row and arming identity index differ")
        expected_wakes: set[str] = set()
        for key, payload, state in rows:
            identity = EffectIdentity.from_value(json.loads(payload)["identity"])
            if key != identity.key:
                raise WorkflowExecutionError("timer row identity is inconsistent")
            registration = self.get(identity)
            if registration is None or registration.identity.workflow_fingerprint != fingerprint:
                raise WorkflowExecutionError("timer invocation evidence is inconsistent")
            wake_identity = replace(identity, operation="wait:wake:v1")
            receipt = self.journal.get(wake_identity)
            if state in {"woken", "consumed"} or receipt is not None:
                if receipt is None or state == "pending":
                    raise WorkflowExecutionError("timer wake and disposition differ")
                self._wake_value(registration, receipt)
                expected_wakes.add(wake_identity.key)
        if wakes != expected_wakes:
            raise WorkflowExecutionError("timer wake identity index is inconsistent")

    def wake_for(self, registration: TimerRegistration) -> TimerWake | None:
        receipt = self.journal.get(replace(registration.identity, operation="wait:wake:v1"))
        return self._wake_value(registration, receipt) if receipt is not None else None

    def known_wakes(self, invocation_id: str, fingerprint: str) -> dict[str, TimerWake]:
        self.verify_invocation(invocation_id, fingerprint)
        rows = self.connection.execute(
            "SELECT payload FROM workflow_timers WHERE invocation_id=? AND fingerprint=?",
            (invocation_id, fingerprint),
        ).fetchall()
        result: dict[str, TimerWake] = {}
        for row in rows:
            registration = self.get(EffectIdentity.from_value(json.loads(row[0])["identity"]))
            if registration is None:
                raise WorkflowExecutionError("timer wake registration is missing")
            wake = self.wake_for(registration)
            if wake is not None:
                result[registration.timer_key] = wake
        return result

    def resumable_for_invocation(
        self, invocation_id: str, fingerprint: str
    ) -> list[TimerRegistration]:
        self.verify_invocation(invocation_id, fingerprint)
        rows = self.connection.execute(
            "SELECT payload FROM workflow_timers WHERE invocation_id=? AND fingerprint=? "
            "AND state IN ('pending','woken') ORDER BY wake_at,identity",
            (invocation_id, fingerprint),
        ).fetchall()
        result = []
        for row in rows:
            registration = self.get(EffectIdentity.from_value(json.loads(row[0])["identity"]))
            if registration is None:
                raise WorkflowExecutionError("resumable timer evidence is missing")
            result.append(registration)
        return result

    def consume(
        self, registration: TimerRegistration, *, execution_lease: ExecutionLease | None
    ) -> None:
        with self._transaction(registration.identity, execution_lease):
            if (
                self.get(registration.identity) != registration
                or self.wake_for(registration) is None
            ):
                raise WorkflowExecutionError("timer continuation lacks wake evidence")
            updated = self.connection.execute(
                "UPDATE workflow_timers SET state='consumed',claim_owner=NULL,lease_until=NULL "
                "WHERE identity=? AND state IN ('woken','consumed')",
                (registration.timer_key,),
            )
            if updated.rowcount != 1:
                raise WorkflowExecutionError("timer continuation has incompatible disposition")

    def close_invocation(self, invocation_id: str, fingerprint: str) -> None:
        with self._transaction(None, None):
            self.verify_invocation(invocation_id, fingerprint)
            self.connection.execute(
                "UPDATE workflow_timers SET state='cancelled',claim_owner=NULL,lease_until=NULL "
                "WHERE invocation_id=? AND fingerprint=? AND state IN ('pending','woken')",
                (invocation_id, fingerprint),
            )

    def due_keys(
        self, *, now: float, limit: int = 128, invocation_id: str | None = None
    ) -> list[str]:
        if not math.isfinite(now) or type(limit) is not int or not 1 <= limit <= 1000:
            raise WorkflowExecutionError("timer dispatch bounds are invalid")
        rows = self.connection.execute(
            "SELECT identity FROM workflow_timers WHERE "
            "(state='woken' OR (state='pending' AND wake_at<=?)) "
            "AND (lease_until IS NULL OR lease_until<=?) "
            + ("AND invocation_id=? " if invocation_id is not None else "")
            + "ORDER BY wake_at,identity LIMIT ?",
            (now, now, invocation_id, limit) if invocation_id is not None else (now, now, limit),
        ).fetchall()
        return [str(row[0]) for row in rows]

    def registration_for_key(self, key: str) -> TimerRegistration:
        row = self.connection.execute(
            "SELECT payload FROM workflow_timers WHERE identity=?", (key,)
        ).fetchone()
        if row is None:
            raise WorkflowExecutionError("timer dispatch registration is missing")
        try:
            identity = EffectIdentity.from_value(json.loads(row[0])["identity"])
        except (KeyError, ValueError, TypeError):
            raise WorkflowExecutionError("timer dispatch identity is inconsistent") from None
        registration = self.get(identity)
        if registration is None or registration.timer_key != key:
            raise WorkflowExecutionError("timer dispatch identity is inconsistent")
        return registration

    def claim_due(
        self, *, owner: str, now: float, timer_key: str, lease_seconds: float = 30
    ) -> TimerClaim | None:
        if (
            not owner
            or len(owner) > 128
            or not math.isfinite(now)
            or not math.isfinite(lease_seconds)
            or lease_seconds <= 0
        ):
            raise WorkflowExecutionError("timer claim bounds are invalid")
        with self._transaction(None, None):
            registration = self.registration_for_key(timer_key)
            row = self.connection.execute(
                "SELECT generation FROM workflow_timers WHERE identity=? "
                "AND (state='woken' OR (state='pending' AND wake_at<=?)) "
                "AND (lease_until IS NULL OR lease_until<=?)",
                (timer_key, now, now),
            ).fetchone()
            if row is None:
                return None
            generation = int(row[0]) + 1
            self.connection.execute(
                "UPDATE workflow_timers SET claim_owner=?,generation=?,lease_until=? "
                "WHERE identity=? AND generation=?",
                (owner, generation, now + lease_seconds, timer_key, generation - 1),
            )
            return TimerClaim(registration, owner, generation, now + lease_seconds)

    def verify_claim(self, claim: TimerClaim, *, now: float) -> None:
        row = self.connection.execute(
            "SELECT claim_owner,generation,lease_until,state FROM workflow_timers WHERE identity=?",
            (claim.registration.timer_key,),
        ).fetchone()
        if (
            row is None
            or tuple(row[:3]) != (claim.owner, claim.generation, claim.lease_until)
            or row[2] <= now
            or row[3] not in {"pending", "woken"}
        ):
            raise WorkflowExecutionError("timer dispatch ownership is stale")

    def release_claim(self, claim: TimerClaim) -> None:
        with self._transaction(None, None):
            self.connection.execute(
                "UPDATE workflow_timers SET claim_owner=NULL,lease_until=NULL WHERE identity=? "
                "AND claim_owner=? AND generation=?",
                (claim.registration.timer_key, claim.owner, claim.generation),
            )
