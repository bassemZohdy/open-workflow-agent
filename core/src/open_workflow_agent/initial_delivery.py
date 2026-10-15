"""Private durable initial delivery, over common identities and engine ownership.

This queue never stores a plan or an engine checkpoint. An acknowledgement is
allowed only after the original definition/input admission and this decision
have committed. A worker may recover native progress, never guess a new input.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from ._effect_storage import insert_completed_receipt
from .effects import (
    EffectIdentity,
    EffectJournal,
    EffectReceipt,
    ReceiptDependency,
    effect_request_digest,
)
from .errors import InvocationStateConflict, WorkflowExecutionError
from .expressions import validate_expression_data
from .ownership import owned_write_transaction
from .persistence import ExecutionHandle, InvocationStore
from .timing import clock_started_at, root_clock_identity

_OPERATION = "policy:initial-delivery:v1"
_MAX_BYTES = 65_536


def delivery_identity(handle: ExecutionHandle) -> EffectIdentity:
    return EffectIdentity(
        handle.invocation_id, handle.workflow_fingerprint, "/", (("/", 1),), _OPERATION
    )


def _encode(value: Any) -> str:
    validate_expression_data(value)
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
    if len(encoded.encode("utf-8")) > _MAX_BYTES:
        raise WorkflowExecutionError("initial delivery binding exceeds its limit")
    return encoded


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for name, item in pairs:
        if name in value:
            raise ValueError("duplicate delivery field")
        value[name] = item
    return value


def _identity(handle: ExecutionHandle) -> dict[str, Any]:
    return {
        name: getattr(handle, name)
        for name in (
            "invocation_id",
            "workflow_fingerprint",
            "workflow_name",
            "workflow_version",
            "engine",
            "engine_execution_reference",
            "session_id",
            "user_id",
            "owner_principal",
            "parent_invocation_id",
            "parent_task_reference",
        )
    }


@dataclass(frozen=True, slots=True)
class InitialDelivery:
    invocation_id: str
    skill_id: str | None
    input_digest: str
    status: str


class InitialDeliveryStore:
    def __init__(
        self, journal: EffectJournal, invocations: InvocationStore, *, max_pending: int = 1000
    ) -> None:
        self.journal = journal
        self.invocations = invocations
        self.connection = journal.connection
        self.max_pending = max_pending
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS initial_invocation_deliveries "
            "(invocation_id TEXT PRIMARY KEY, engine TEXT NOT NULL, "
            "fingerprint TEXT NOT NULL, payload TEXT NOT NULL, digest TEXT NOT NULL, "
            "status TEXT NOT NULL)"
        )
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS initial_delivery_admission_guard "
            "(id TEXT PRIMARY KEY, marker INTEGER NOT NULL)"
        )
        self.connection.execute(
            "INSERT INTO initial_delivery_admission_guard(id,marker) VALUES ('global',1) "
            "ON CONFLICT(id) DO NOTHING"
        )
        self.connection.commit()

    def pending_ids(self, engine: str, *, after_id: str | None = None) -> list[str]:
        suffix = " AND invocation_id>?" if after_id else ""
        parameters = (engine, after_id) if after_id else (engine,)
        rows = self.connection.execute(
            "SELECT invocation_id FROM initial_invocation_deliveries "
            "WHERE engine=? AND status='ready'" + suffix + " ORDER BY invocation_id LIMIT 128",
            parameters,
        ).fetchall()
        return [row[0] for row in rows]

    def get(self, handle: ExecutionHandle) -> InitialDelivery | None:
        try:
            row = self.connection.execute(
                "SELECT engine,fingerprint,CASE WHEN length(payload)<=65536 "
                "THEN payload ELSE NULL END,digest,status FROM initial_invocation_deliveries "
                "WHERE invocation_id=?",
                (handle.invocation_id,),
            ).fetchone()
            receipt = self.journal.get(delivery_identity(handle))
            identity = delivery_identity(handle)
            index = self.connection.execute(
                "SELECT invocation_id,fingerprint,payload FROM workflow_effect_invocations "
                "WHERE identity=?",
                (identity.key,),
            ).fetchone()
            if row is None and receipt is None and index is None:
                return None
            if row is None or receipt is None or index is None or row[2] is None:
                raise ValueError("delivery evidence is incomplete")
            if (
                index[0] != handle.invocation_id
                or index[1] != handle.workflow_fingerprint
                or EffectIdentity.from_value(json.loads(index[2], object_pairs_hook=_unique))
                != identity
            ):
                raise ValueError("delivery receipt index changed")
            payload = json.loads(row[2], object_pairs_hook=_unique)
            if (
                row[0] != handle.engine
                or row[1] != handle.workflow_fingerprint
                or row[4] not in {"ready", "settled"}
                or set(payload)
                != {"profile", "identity", "skill_id", "input_digest", "clock_digest"}
                or payload["profile"] != _OPERATION
                or payload["identity"] != _identity(handle)
                or payload["skill_id"] is not None
                and (
                    not isinstance(payload["skill_id"], str)
                    or not 1 <= len(payload["skill_id"]) <= 256
                )
                or hashlib.sha256(_encode(payload).encode("utf-8")).hexdigest() != row[3]
                or receipt != self._receipt(payload)
                or payload["input_digest"]
                != effect_request_digest("initial-input", self.invocations.read_input(handle))
            ):
                raise ValueError("delivery binding changed")
            if payload["clock_digest"] is not None:
                clock_identity = root_clock_identity(
                    handle.invocation_id, handle.workflow_fingerprint
                )
                clock = self.journal.get(clock_identity)
                if clock is None:
                    raise ValueError("admitted clock is absent")
                clock_started_at(clock.output)
                ReceiptDependency(clock_identity, payload["clock_digest"]).verify(self.journal)
            return InitialDelivery(
                handle.invocation_id, payload["skill_id"], payload["input_digest"], row[4]
            )
        except InvocationStateConflict:
            raise
        except Exception:
            raise WorkflowExecutionError(
                "initial delivery evidence is unavailable or invalid"
            ) from None

    @staticmethod
    def _receipt(payload: dict[str, Any]) -> EffectReceipt:
        return EffectReceipt(
            request_digest=effect_request_digest(_OPERATION, payload),
            owner="initial-delivery-v1",
            status="completed",
            output=payload,
        )

    def admit(self, handle: ExecutionHandle, *, skill_id: str | None) -> None:
        if handle._execution_lease is None:
            raise InvocationStateConflict("initial delivery admission requires ownership")
        if skill_id is not None and (
            not isinstance(skill_id, str) or not 1 <= len(skill_id) <= 256
        ):
            raise WorkflowExecutionError("initial delivery skill binding is invalid")
        payload = {
            "profile": _OPERATION,
            "identity": _identity(handle),
            "skill_id": skill_id,
            "input_digest": effect_request_digest(
                "initial-input", self.invocations.read_input(handle)
            ),
            "clock_digest": None,
        }
        clock_identity = root_clock_identity(handle.invocation_id, handle.workflow_fingerprint)
        clock = self.journal.get(clock_identity)
        if clock is not None:
            clock_started_at(clock.output)
            payload["clock_digest"] = ReceiptDependency.capture(
                clock_identity, clock
            ).receipt_digest
        encoded = _encode(payload)
        with owned_write_transaction(
            self.connection,
            handle._execution_lease,
            invocation_id=handle.invocation_id,
            workflow_fingerprint=handle.workflow_fingerprint,
        ):
            locked = self.connection.execute(
                "UPDATE initial_delivery_admission_guard SET marker=marker WHERE id='global'"
            )
            if locked.rowcount != 1:
                raise WorkflowExecutionError("initial delivery admission guard is unavailable")
            existing = self.get(handle)
            if existing is not None:
                if self.journal.get(delivery_identity(handle)) != self._receipt(payload):
                    raise WorkflowExecutionError("initial delivery cannot be rebound")
                return
            row = self.connection.execute(
                "SELECT payload,status FROM invocations WHERE invocation_id=?",
                (handle.invocation_id,),
            ).fetchone()
            if row is None or row[0] != handle._persisted_payload or row[1] != "running":
                raise InvocationStateConflict("initial delivery invocation has changed")
            count = self.connection.execute(
                "SELECT COUNT(*) FROM initial_invocation_deliveries WHERE status='ready'"
            ).fetchone()[0]
            if count >= self.max_pending:
                raise WorkflowExecutionError("initial delivery capacity is exhausted")
            self.connection.execute(
                "INSERT INTO initial_invocation_deliveries "
                "(invocation_id,engine,fingerprint,payload,digest,status) "
                "VALUES (?,?,?,?,?,'ready')",
                (
                    handle.invocation_id,
                    handle.engine,
                    handle.workflow_fingerprint,
                    encoded,
                    hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
                ),
            )
            insert_completed_receipt(
                self.connection, delivery_identity(handle), self._receipt(payload)
            )
            self.get(handle)

    def settle(self, handle: ExecutionHandle) -> None:
        binding = self.get(handle)
        if binding is None or binding.status == "settled":
            return
        if handle._execution_lease is None:
            raise InvocationStateConflict("initial delivery settlement requires ownership")
        with owned_write_transaction(
            self.connection,
            handle._execution_lease,
            invocation_id=handle.invocation_id,
            workflow_fingerprint=handle.workflow_fingerprint,
        ):
            self.get(handle)
            self.connection.execute(
                "UPDATE initial_invocation_deliveries SET status='settled' "
                "WHERE invocation_id=? AND status='ready'",
                (handle.invocation_id,),
            )
