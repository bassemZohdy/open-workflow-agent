"""Durable parent-owned child await decisions, never native checkpoints."""

from __future__ import annotations

import json
import math
import threading
from dataclasses import dataclass, replace
from typing import Any

from ._effect_storage import insert_completed_receipt
from .child_suspension import ChildSuspension
from .children import ChildWorkflowStore
from .effects import (
    ChildInvocationDependency,
    EffectIdentity,
    EffectJournal,
    EffectReceipt,
    effect_request_digest,
)
from .errors import InvocationStateConflict, WorkflowExecutionError
from .ownership import owned_write_transaction
from .persistence import ExecutionHandle, InvocationStore
from .storage import StorageConnection

_ARM = "policy:child-await:v1"
_WAKE = "policy:child-wake:v1"
_TERMINAL = {"completed", "faulted", "cancelled"}


def _json(value: Any) -> str:
    # Digest validation also rejects non-JSON values and nonfinite numbers.
    effect_request_digest("owa/child-wait-json/v1", value)
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


@dataclass(frozen=True, slots=True)
class ChildWaitRegistration:
    identity: EffectIdentity
    suspension: ChildSuspension
    receipt: EffectReceipt
    workflow_source_json: str


def _receipt(connection: StorageConnection, identity: EffectIdentity) -> EffectReceipt | None:
    """Read without calling EffectJournal.get's policy-verification callback."""
    row = connection.execute(
        "SELECT owner,status,payload FROM workflow_effects WHERE identity=?", (identity.key,)
    ).fetchone()
    if row is None:
        return None
    try:
        value = json.loads(row[2])
        if value.get("child_invocation") is not None:
            value["child_invocation"] = ChildInvocationDependency.from_value(
                value["child_invocation"]
            )
        receipt = EffectReceipt(**value)
        index = connection.execute(
            "SELECT invocation_id,fingerprint,payload FROM workflow_effect_invocations "
            "WHERE identity=?",
            (identity.key,),
        ).fetchone()
        if (
            (receipt.owner, receipt.status) != tuple(row[:2])
            or index is None
            or tuple(index[:2]) != (identity.invocation_id, identity.workflow_fingerprint)
            or EffectIdentity.from_value(json.loads(index[2])) != identity
        ):
            raise ValueError("invalid policy receipt index")
        return receipt
    except (ValueError, TypeError, KeyError):
        raise WorkflowExecutionError("child wait receipt is inconsistent") from None


def _registration(
    connection: StorageConnection, invocations: InvocationStore | None, key: str
) -> ChildWaitRegistration | None:
    row = connection.execute(
        "SELECT invocation_id,fingerprint,child_id,engine,deadline_at,payload,digest,state "
        "FROM child_workflow_waits WHERE run_key=?",
        (key,),
    ).fetchone()
    if row is None:
        return None
    try:
        value = json.loads(row[5])
        if set(value) != {"suspension", "source"} or not isinstance(value["source"], dict):
            raise ValueError("invalid admission payload")
        suspension = ChildSuspension.from_value(value["suspension"])
        identity = suspension.identity
        if (
            identity.key != key
            or tuple(row[:3])
            != (identity.invocation_id, identity.workflow_fingerprint, suspension.child_id)
            or row[4] != suspension.deadline_at
            or row[6] != effect_request_digest(_ARM, value)
            or row[7] not in {"pending", "woken", "consumed"}
            or invocations is None
        ):
            raise ValueError("invalid admission columns")
        arm = replace(identity, operation=_ARM)
        receipt = _receipt(connection, arm)
        if (
            receipt is None
            or receipt.status != "completed"
            or receipt.owner != "child-wait"
            or receipt.output != value
            or receipt.request_digest != row[6]
            or receipt.child_invocation is not None
        ):
            raise ValueError("invalid admission receipt")
        wake_receipt = _receipt(connection, replace(identity, operation=_WAKE))
        if (row[7] in {"woken", "consumed"}) != (wake_receipt is not None):
            raise ValueError("child wait state differs from its immutable wake")
        _verify_child(connection, invocations, suspension, row[3])
        return ChildWaitRegistration(identity, suspension, receipt, _json(value["source"]))
    except (ValueError, TypeError, KeyError):
        raise WorkflowExecutionError("child wait admission evidence is inconsistent") from None


def _verify_child(
    connection: StorageConnection,
    invocations: InvocationStore,
    suspension: ChildSuspension,
    engine: str,
) -> None:
    """Verify original common identity, input, ancestry and pinned root policy."""
    row = connection.execute(
        "SELECT binding_key,root_id,parent_id,child_id,depth,request_digest,source_json,digest,"
        "parent_task_reference FROM child_workflow_bindings WHERE binding_key=?",
        (suspension.identity.key,),
    ).fetchone()
    # The decoder is a pure evidence check, not a new store/connection constructor.
    decoder = ChildWorkflowStore.__new__(ChildWorkflowStore)
    binding = decoder._binding(row)
    budget = connection.execute(
        "SELECT fingerprint,max_depth,max_children,used,policy_digest,"
        "(SELECT COUNT(*) FROM child_workflow_bindings WHERE root_id=?) "
        "FROM child_workflow_budgets WHERE root_id=?",
        (binding.root_id, binding.root_id),
    ).fetchone()
    parent = invocations.get(suspension.identity.invocation_id)
    child = invocations.get(suspension.child_id)
    root = invocations.get(binding.root_id)
    try:
        child_document = json.loads(binding.workflow_source_json)["document"]
        if not isinstance(child_document, dict):
            raise ValueError("child source document is invalid")
    except (TypeError, ValueError, KeyError):
        raise WorkflowExecutionError("child wait source evidence is inconsistent") from None
    if (
        budget is None
        or root is None
        or root.workflow_fingerprint != budget[0]
        or any(type(item) is not int for item in (*budget[1:4], budget[5]))
        or not 1 <= budget[1] <= 64
        or not 1 <= budget[2] <= 100_000
        or not 1 <= budget[3] <= budget[2]
        or budget[3] != budget[5]
        or budget[4] != decoder._digest((binding.root_id, *budget[:3]))
        or not 1 <= binding.depth <= budget[1]
        or parent is None
        or child is None
        or binding.parent_id != parent.invocation_id
        or binding.child_id != child.invocation_id
        or parent.workflow_fingerprint != suspension.identity.workflow_fingerprint
        or parent.engine != engine
        or child.engine != parent.engine
        or child.parent_invocation_id != parent.invocation_id
        or child.parent_task_reference != binding.parent_task_reference
        or child.owner_principal != parent.owner_principal
        or child.user_id != parent.user_id
        or child.workflow_name != child_document.get("name")
        or child.workflow_version != child_document.get("version")
        or binding.request_digest != suspension.request_digest
        or effect_request_digest(
            "run:workflow",
            {
                "workflow_fingerprint": child.workflow_fingerprint,
                "input": invocations.read_input(child),
            },
        )
        != binding.request_digest
    ):
        raise WorkflowExecutionError("child wait binding, identity or budget is inconsistent")


def _wake_value(
    registration: ChildWaitRegistration,
    invocations: InvocationStore | None,
    receipt: EffectReceipt,
) -> dict[str, Any]:
    value = receipt.output
    suspension = registration.suspension
    identity = replace(registration.identity, operation=_WAKE)
    if (
        receipt.status != "completed"
        or receipt.owner != "child-wait"
        or not isinstance(value, dict)
        or set(value) != {"child_key", "child_id", "request_digest", "deadline_at", "reason"}
        or value["child_key"] != registration.identity.key
        or value["child_id"] != suspension.child_id
        or value["request_digest"] != suspension.request_digest
        or value["deadline_at"] != suspension.deadline_at
        or value["reason"] not in {"child_terminal", "deadline"}
        or receipt.request_digest
        != effect_request_digest(
            identity.operation,
            {"admission_digest": registration.receipt.request_digest, "wake": value},
        )
    ):
        raise WorkflowExecutionError("child wake evidence is inconsistent")
    if value["reason"] == "child_terminal":
        if (
            receipt.child_invocation is None
            or receipt.child_invocation.invocation_id != suspension.child_id
        ):
            raise WorkflowExecutionError("child wake outcome dependency is absent")
        receipt.child_invocation.verify(
            invocations, registration.identity, suspension.request_digest
        )
    elif suspension.deadline_at is None or receipt.child_invocation is not None:
        raise WorkflowExecutionError("child wake deadline evidence is inconsistent")
    return dict(value)


def verify_child_wait_receipt(
    connection: StorageConnection,
    invocations: InvocationStore | None,
    identity: EffectIdentity,
    receipt: EffectReceipt,
) -> None:
    """Fail closed when native receipts refer to altered common wait evidence."""
    run = replace(identity, operation="run:workflow")
    registration = _registration(connection, invocations, run.key)
    if registration is None:
        raise WorkflowExecutionError("child wait admission is absent")
    if identity.operation == _ARM:
        if receipt != registration.receipt:
            raise WorkflowExecutionError("child wait receipt differs from admission")
    elif identity.operation == _WAKE:
        _wake_value(registration, invocations, receipt)
    else:
        raise WorkflowExecutionError("child wait receipt operation is invalid")


class ChildWaitStore:
    def __init__(self, journal: EffectJournal, invocations: InvocationStore) -> None:
        self.journal = journal
        self.connection = journal.connection
        self.invocations = invocations
        self._lock = threading.RLock()
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS child_workflow_waits (run_key TEXT PRIMARY KEY,"
            "invocation_id TEXT NOT NULL,fingerprint TEXT NOT NULL,child_id TEXT NOT NULL,"
            "engine TEXT NOT NULL,deadline_at DOUBLE PRECISION,payload TEXT NOT NULL,"
            "digest TEXT NOT NULL,state TEXT NOT NULL)"
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS child_wait_pending "
            "ON child_workflow_waits(engine,state,run_key)"
        )
        self.connection.commit()

    @staticmethod
    def _authority(identity: EffectIdentity, parent: ExecutionHandle) -> None:
        if (
            identity.operation != "run:workflow"
            or identity.invocation_id != parent.invocation_id
            or identity.workflow_fingerprint != parent.workflow_fingerprint
            or parent._execution_lease is None
            or parent._execution_lease.engine != parent.engine
        ):
            raise InvocationStateConflict("child wait requires current parent authority")

    def registration(self, identity: EffectIdentity) -> ChildWaitRegistration | None:
        if identity.operation != "run:workflow":
            raise WorkflowExecutionError("child wait requires a workflow run identity")
        return self.registration_for_key(identity.key)

    def registration_for_key(self, run_key: str) -> ChildWaitRegistration | None:
        return _registration(self.connection, self.invocations, run_key)

    def for_invocation(
        self, invocation_id: str, fingerprint: str, *, pending_only: bool = True
    ) -> list[ChildWaitRegistration]:
        query = "SELECT run_key FROM child_workflow_waits WHERE invocation_id=? AND fingerprint=?"
        if pending_only:
            query += " AND state IN ('pending','woken')"
        result: list[ChildWaitRegistration] = []
        for row in self.connection.execute(
            query + " ORDER BY run_key", (invocation_id, fingerprint)
        ).fetchall():
            registration = self.registration_for_key(row[0])
            if registration is None:
                raise WorkflowExecutionError("child wait admission is absent")
            result.append(registration)
        return result

    def verify_invocation(self, invocation_id: str, fingerprint: str) -> None:
        self.for_invocation(invocation_id, fingerprint, pending_only=False)
        # A policy receipt without its row must never become an unrecorded new wait.
        rows = self.connection.execute(
            "SELECT payload FROM workflow_effect_invocations "
            "WHERE invocation_id=? AND fingerprint=?",
            (invocation_id, fingerprint),
        ).fetchall()
        for row in rows:
            identity = EffectIdentity.from_value(json.loads(row[0]))
            if identity.operation in {_ARM, _WAKE}:
                receipt = _receipt(self.connection, identity)
                if receipt is None:
                    raise WorkflowExecutionError("child wait policy receipt is absent")
                verify_child_wait_receipt(self.connection, self.invocations, identity, receipt)

    def arm(
        self,
        identity: EffectIdentity,
        parent: ExecutionHandle,
        child: ExecutionHandle,
        *,
        workflow_source: dict[str, Any],
        deadline_at: float | None,
    ) -> ChildWaitRegistration:
        self._authority(identity, parent)
        binding_row = self.connection.execute(
            "SELECT request_digest FROM child_workflow_bindings WHERE binding_key=?",
            (identity.key,),
        ).fetchone()
        if binding_row is None:
            raise WorkflowExecutionError("child wait requires an admitted child")
        suspension = ChildSuspension(
            identity, child.invocation_id, binding_row[0], deadline_at=deadline_at
        )
        value = {"suspension": suspension.as_value(), "source": workflow_source}
        digest = effect_request_digest(_ARM, value)
        with (
            self._lock,
            owned_write_transaction(
                self.connection,
                parent._execution_lease,
                invocation_id=parent.invocation_id,
                workflow_fingerprint=parent.workflow_fingerprint,
            ),
        ):
            _verify_child(self.connection, self.invocations, suspension, parent.engine)
            existing = self.registration(identity)
            if existing is not None:
                if existing.receipt.request_digest != digest:
                    raise WorkflowExecutionError("child wait original request or deadline changed")
                return existing
            arm_identity = replace(identity, operation=_ARM)
            if _receipt(self.connection, arm_identity) is not None:
                raise WorkflowExecutionError("child wait row is absent for its admission receipt")
            self.connection.execute(
                "INSERT INTO child_workflow_waits(run_key,invocation_id,fingerprint,child_id,"
                "engine,deadline_at,payload,digest,state) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    identity.key,
                    parent.invocation_id,
                    parent.workflow_fingerprint,
                    child.invocation_id,
                    parent.engine,
                    deadline_at,
                    _json(value),
                    digest,
                    "pending",
                ),
            )
            insert_completed_receipt(
                self.connection,
                arm_identity,
                EffectReceipt(digest, "child-wait", status="completed", output=value),
            )
            registration = self.registration(identity)
            if registration is None:
                raise WorkflowExecutionError("child wait admission was not recorded")
            return registration

    def pending_keys(
        self, engine: str, *, now: float, after_key: str | None = None, limit: int = 128
    ) -> list[str]:
        if (
            type(limit) is not int
            or not 1 <= limit <= 1000
            or isinstance(now, bool)
            or not isinstance(now, (int, float))
            or not math.isfinite(now)
            or now <= 0
        ):
            raise WorkflowExecutionError("child wait scan bounds are invalid")
        query = (
            "SELECT w.run_key FROM child_workflow_waits w "
            "JOIN invocations p ON p.invocation_id=w.invocation_id "
            "LEFT JOIN invocations c ON c.invocation_id=w.child_id "
            "WHERE w.engine=? AND w.state IN ('pending','woken') "
            "AND p.status IN ('running','waiting') "
            "AND (w.state='woken' OR c.invocation_id IS NULL "
            "OR c.status IN ('completed','faulted','cancelled') "
            "OR w.deadline_at<=?)"
        )
        parameters: tuple[Any, ...] = (engine, now)
        if after_key is not None:
            query += " AND w.run_key>?"
            parameters += (after_key,)
        query += " ORDER BY w.run_key LIMIT ?"
        return [row[0] for row in self.connection.execute(query, (*parameters, limit)).fetchall()]

    def wake(
        self, registration: ChildWaitRegistration, parent: ExecutionHandle, *, now: float
    ) -> dict[str, Any] | None:
        identity = registration.identity
        self._authority(identity, parent)
        if (
            isinstance(now, bool)
            or not isinstance(now, (int, float))
            or not math.isfinite(now)
            or now <= 0
        ):
            raise WorkflowExecutionError("child wait wake clock is invalid")
        with (
            self._lock,
            owned_write_transaction(
                self.connection,
                parent._execution_lease,
                invocation_id=parent.invocation_id,
                workflow_fingerprint=parent.workflow_fingerprint,
            ),
        ):
            known = self.registration(identity)
            if (
                known is None
                or known.receipt != registration.receipt
                or known.workflow_source_json != registration.workflow_source_json
            ):
                raise WorkflowExecutionError("child wait admission changed before wake")
            wake_identity = replace(identity, operation=_WAKE)
            existing = _receipt(self.connection, wake_identity)
            if existing is not None:
                return _wake_value(known, self.invocations, existing)
            child = self.invocations.get(known.suspension.child_id)
            if child is None:
                raise WorkflowExecutionError("awaited child is absent")
            terminal = child.status in _TERMINAL
            deadline = known.suspension.deadline_at
            if not terminal and (deadline is None or now < deadline):
                return None
            value = {
                "child_key": identity.key,
                "child_id": child.invocation_id,
                "request_digest": known.suspension.request_digest,
                "deadline_at": deadline,
                "reason": "child_terminal" if terminal else "deadline",
            }
            receipt = EffectReceipt(
                effect_request_digest(
                    _WAKE, {"admission_digest": known.receipt.request_digest, "wake": value}
                ),
                "child-wait",
                status="completed",
                output=value,
                child_invocation=ChildInvocationDependency.capture(
                    self.invocations, child.invocation_id
                )
                if terminal
                else None,
            )
            insert_completed_receipt(self.connection, wake_identity, receipt)
            self.connection.execute(
                "UPDATE child_workflow_waits SET state='woken' WHERE run_key=? AND state='pending'",
                (identity.key,),
            )
            return _wake_value(known, self.invocations, receipt)

    def known_wakes(self, invocation_id: str, fingerprint: str) -> dict[str, dict[str, Any]]:
        self.verify_invocation(invocation_id, fingerprint)
        result: dict[str, dict[str, Any]] = {}
        rows = self.connection.execute(
            "SELECT run_key FROM child_workflow_waits WHERE invocation_id=? AND fingerprint=?",
            (invocation_id, fingerprint),
        ).fetchall()
        for row in rows:
            registration = self.registration_for_key(row[0])
            if registration is None:
                raise WorkflowExecutionError("child wait admission is absent")
            receipt = _receipt(self.connection, replace(registration.identity, operation=_WAKE))
            if receipt is not None:
                result[row[0]] = _wake_value(registration, self.invocations, receipt)
        return result

    def consume(self, registration: ChildWaitRegistration, parent: ExecutionHandle) -> None:
        self._authority(registration.identity, parent)
        with (
            self._lock,
            owned_write_transaction(
                self.connection,
                parent._execution_lease,
                invocation_id=parent.invocation_id,
                workflow_fingerprint=parent.workflow_fingerprint,
            ),
        ):
            known = self.registration(registration.identity)
            receipt = _receipt(self.connection, replace(registration.identity, operation=_WAKE))
            if (
                known is None
                or known.receipt != registration.receipt
                or known.workflow_source_json != registration.workflow_source_json
                or receipt is None
            ):
                raise WorkflowExecutionError("child wait cannot be consumed without a wake")
            _wake_value(known, self.invocations, receipt)
            self.connection.execute(
                "UPDATE child_workflow_waits SET state='consumed' WHERE run_key=?",
                (registration.identity.key,),
            )
