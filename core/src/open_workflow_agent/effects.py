"""Private workflow action intents and outcomes, separate from native checkpoints."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from .errors import InvocationStateConflict, WorkflowExecutionError
from .expressions import validate_expression_data
from .ownership import ExecutionLease, owned_write_transaction
from .persistence import InvocationStore
from .storage import StorageConnection, open_storage

if TYPE_CHECKING:
    from .external_recovery import ExternalOutcomeDependency


@dataclass(frozen=True, slots=True)
class EffectIdentity:
    invocation_id: str
    workflow_fingerprint: str
    task_reference: str
    occurrence: tuple[tuple[str, int], ...]
    operation: str

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or not value
            for value in (
                self.invocation_id,
                self.workflow_fingerprint,
                self.task_reference,
                self.operation,
            )
        ):
            raise ValueError(
                "workflow effect identity requires invocation, workflow, task and kind"
            )
        if not self.occurrence or any(
            not isinstance(reference, str) or not reference or type(index) is not int or index < 1
            for reference, index in self.occurrence
        ):
            raise ValueError("workflow effect identity requires positive execution occurrences")

    @property
    def key(self) -> str:
        encoded = json.dumps(
            ["owa/workflow-effect/v1", asdict(self)], sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(encoded.encode()).hexdigest()

    @classmethod
    def from_value(cls, raw: Any) -> EffectIdentity:
        if not isinstance(raw, dict) or set(raw) != {
            "invocation_id",
            "workflow_fingerprint",
            "task_reference",
            "occurrence",
            "operation",
        }:
            raise ValueError("workflow receipt identity fields are invalid")
        path = raw["occurrence"]
        if not isinstance(path, list) or any(
            not isinstance(item, list) or len(item) != 2 for item in path
        ):
            raise ValueError("workflow receipt occurrence is invalid")
        return cls(**{**raw, "occurrence": tuple(tuple(item) for item in path)})


def effect_request_digest(operation: str, payload: Any) -> str:
    """Compare prepared requests without saving credentials or request bodies here."""

    validate_expression_data(payload)
    encoded = json.dumps(
        [operation, payload],
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ChildInvocationDependency:
    """Common terminal child record/input reference, never a native checkpoint."""

    invocation_id: str
    record_digest: str
    binding_digest: str | None = None
    budget_digest: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.invocation_id, str)
            or not self.invocation_id
            or not isinstance(self.record_digest, str)
            or not re.fullmatch(r"[a-f0-9]{64}", self.record_digest)
            or (self.binding_digest is None) != (self.budget_digest is None)
            or any(
                marker is not None
                and (not isinstance(marker, str) or re.fullmatch(r"[a-f0-9]{64}", marker) is None)
                for marker in (self.binding_digest, self.budget_digest)
            )
        ):
            raise ValueError("child invocation dependency is invalid")

    @classmethod
    def from_value(cls, value: Any) -> ChildInvocationDependency:
        legacy = {"invocation_id", "record_digest"}
        marked = legacy | {"binding_digest", "budget_digest"}
        if (
            not isinstance(value, dict)
            or set(value) not in (legacy, marked)
            or (
                set(value) == marked
                and (value["binding_digest"] is None or value["budget_digest"] is None)
            )
        ):
            raise ValueError("child invocation dependency fields are invalid")
        return cls(**value)

    @classmethod
    def _value(cls, store: InvocationStore, invocation_id: str) -> dict[str, Any]:
        child = store.get(invocation_id)
        if (
            child is None
            or child.status not in {"completed", "faulted", "cancelled"}
            or child._persisted_payload is None
        ):
            raise WorkflowExecutionError("child invocation outcome is unavailable")
        return {"record": json.loads(child._persisted_payload), "input": store.read_input(child)}

    @classmethod
    def capture(cls, store: InvocationStore, invocation_id: str) -> ChildInvocationDependency:
        value = cls._value(store, invocation_id)
        provenance = cls._provenance(store, invocation_id)
        return cls(
            invocation_id,
            effect_request_digest("owa/child-invocation/v1", value),
            binding_digest=provenance[1] if provenance is not None else None,
            budget_digest=provenance[2] if provenance is not None else None,
        )

    @staticmethod
    def _provenance(store: InvocationStore, invocation_id: str) -> tuple[str, str, str] | None:
        """Read existing reservation proof without opening or preparing a store."""
        from .children import ChildWorkflowStore

        connection = store.connection
        try:
            if connection.is_postgresql:
                exists = (
                    connection.execute("SELECT to_regclass('child_workflow_bindings')").fetchone()[
                        0
                    ]
                    is not None
                )
            else:
                exists = (
                    connection.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        ("child_workflow_bindings",),
                    ).fetchone()
                    is not None
                )
            if not exists:
                return None
            row = connection.execute(
                "SELECT binding_key,root_id,parent_id,child_id,depth,request_digest,source_json,"
                "digest,parent_task_reference FROM child_workflow_bindings WHERE child_id=?",
                (invocation_id,),
            ).fetchone()
            if row is None:
                return None
            # This pure reader has no policy, journal or constructor side effects.
            reader = ChildWorkflowStore.__new__(ChildWorkflowStore)
            reader.connection, reader.invocations = connection, store
            binding = reader._binding(row)
            parent = store.get(binding.parent_id)
            if parent is None:
                raise ValueError("child provenance parent is absent")
            child = reader.verify_control(binding, parent)
            if child.invocation_id != invocation_id:
                raise ValueError("child provenance refers to another invocation")
            policy = connection.execute(
                "SELECT policy_digest FROM child_workflow_budgets WHERE root_id=?",
                (binding.root_id,),
            ).fetchone()
            if policy is None:
                raise ValueError("child provenance root policy is absent")
            return (
                binding.key,
                effect_request_digest(
                    "owa/child-binding-dependency/v1",
                    {
                        "binding_key": binding.key,
                        "binding_digest": row[7],
                    },
                ),
                effect_request_digest(
                    "owa/child-budget-dependency/v1",
                    {
                        "root_id": binding.root_id,
                        "policy_digest": policy[0],
                    },
                ),
            )
        except Exception:
            raise WorkflowExecutionError("child invocation provenance is inconsistent") from None

    def verify(
        self, store: InvocationStore | None, identity: EffectIdentity, request_digest: str
    ) -> None:
        if store is None:
            raise WorkflowExecutionError("child invocation dependency is unavailable")
        value = self._value(store, self.invocation_id)
        if (
            effect_request_digest("owa/child-invocation/v1", value) != self.record_digest
            or value["record"]["parent_invocation_id"] != identity.invocation_id
            or effect_request_digest(
                "run:workflow",
                {
                    "workflow_fingerprint": value["record"]["workflow_fingerprint"],
                    "input": value["input"],
                },
            )
            != request_digest
        ):
            raise WorkflowExecutionError("child invocation dependency is inconsistent")
        if self.binding_digest is not None:
            provenance = self._provenance(store, self.invocation_id)
            if provenance != (
                replace(identity, operation="run:workflow").key,
                self.binding_digest,
                self.budget_digest,
            ):
                raise WorkflowExecutionError("child invocation provenance is inconsistent")


@dataclass(frozen=True, slots=True)
class EffectReceipt:
    request_digest: str
    owner: str
    status: Literal["pending", "completed", "faulted"] = "pending"
    output: Any = None
    error: dict[str, Any] | None = None
    error_status_code: int | None = None
    operation_id: str | None = None
    child_invocation: ChildInvocationDependency | None = None
    external_outcome: ExternalOutcomeDependency | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.request_digest, str) or not re.fullmatch(
            r"[a-f0-9]{64}", self.request_digest
        ):
            raise ValueError("workflow effect requires a SHA-256 request digest")
        if not isinstance(self.owner, str) or not self.owner:
            raise ValueError("workflow effect requires an execution owner")
        if self.status not in {"pending", "completed", "faulted"}:
            raise ValueError("workflow effect receipt status is invalid")
        validate_expression_data(self.output)
        validate_expression_data(self.error)
        if (self.status == "faulted") != (self.error is not None):
            raise ValueError("workflow effect fault receipts require an error")
        if self.status != "completed" and self.output is not None:
            raise ValueError("only completed workflow effects have output")
        if self.error is not None and (
            not isinstance(self.error, dict)
            or not isinstance(self.error.get("code"), str)
            or not isinstance(self.error.get("message"), str)
            or not isinstance(self.error.get("details"), dict)
        ):
            raise ValueError("workflow effect error must use the common error contract")
        if self.error_status_code is not None and (
            self.error is None
            or type(self.error_status_code) is not int
            or not 100 <= self.error_status_code <= 599
        ):
            raise ValueError("workflow effect common error status is invalid")
        if self.operation_id is not None and not isinstance(self.operation_id, str):
            raise ValueError("workflow effect operation identity is invalid")
        if self.child_invocation is not None and (
            not isinstance(self.child_invocation, ChildInvocationDependency)
            or self.status == "pending"
        ):
            raise ValueError("workflow effect child dependency is invalid")
        if self.external_outcome is not None:
            from .external_recovery import ExternalOutcomeDependency

            if (
                not isinstance(self.external_outcome, ExternalOutcomeDependency)
                or self.status == "pending"
                or self.child_invocation is not None
            ):
                raise ValueError("workflow effect external dependency is invalid")


def _receipt_payload(receipt: EffectReceipt) -> dict[str, Any]:
    value = asdict(receipt)
    if receipt.external_outcome is None:
        value.pop("external_outcome")
    # Existing non-child receipts and their native digests retain their exact
    # logical format. A child binding is present only on a new child outcome.
    if receipt.child_invocation is None:
        value.pop("child_invocation")
    elif receipt.child_invocation.binding_digest is None:
        # Pre-marker child receipts retain byte-for-byte logical payloads and
        # native receipt digests; absence is not rewritten as explicit nulls.
        value["child_invocation"].pop("binding_digest")
        value["child_invocation"].pop("budget_digest")
    return value


@dataclass(frozen=True, slots=True)
class ReceiptDependency:
    """An immutable reference to a common receipt, stored by a native adapter."""

    identity: EffectIdentity
    receipt_digest: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.identity, EffectIdentity)
            or not isinstance(self.receipt_digest, str)
            or not re.fullmatch(r"[a-f0-9]{64}", self.receipt_digest)
        ):
            raise ValueError("workflow receipt dependency is invalid")

    @classmethod
    def capture(cls, identity: EffectIdentity, receipt: EffectReceipt) -> ReceiptDependency:
        if receipt.status == "pending":
            raise ValueError("an unresolved intent is not a completed receipt dependency")
        return cls(identity, cls._digest(receipt))

    @staticmethod
    def _digest(receipt: EffectReceipt) -> str:
        encoded = json.dumps(
            _receipt_payload(receipt),
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(encoded.encode()).hexdigest()

    @classmethod
    def from_value(cls, value: Any) -> ReceiptDependency:
        if not isinstance(value, dict) or set(value) != {"identity", "receipt_digest"}:
            raise ValueError("workflow receipt dependency fields are invalid")
        identity = EffectIdentity.from_value(value["identity"])
        if not isinstance(value["receipt_digest"], str):
            raise ValueError("workflow receipt dependency digest is invalid")
        return cls(identity, value["receipt_digest"])

    def verify(self, journal: EffectJournal) -> None:
        receipt = journal.get(self.identity)
        if (
            receipt is None
            or receipt.status == "pending"
            or self._digest(receipt) != self.receipt_digest
        ):
            raise WorkflowExecutionError(
                "workflow action receipt dependency is unavailable or inconsistent"
            )


class EffectJournal:
    """First-owner intent and immutable outcome receipts on the common datasource.

    Conditional outcome writes prevent another owner from completing an intent.
    This is not a recovery lease, a native checkpoint, or proof of exactly-once
    external actions. A pending intent with no receipt remains unresolved.
    """

    def __init__(
        self, database: str | Path = ":memory:", *, invocations: InvocationStore | None = None
    ) -> None:
        self._invocations = invocations
        self.connection: StorageConnection = open_storage(database, "owa_runtime")
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS workflow_effects (
            identity TEXT PRIMARY KEY, owner TEXT NOT NULL,
            status TEXT NOT NULL, payload TEXT NOT NULL)"""
        )
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS workflow_effect_invocations (
            identity TEXT PRIMARY KEY, invocation_id TEXT NOT NULL,
            fingerprint TEXT NOT NULL, payload TEXT NOT NULL)"""
        )
        self.connection.execute(
            """CREATE INDEX IF NOT EXISTS workflow_effect_invocation_index
            ON workflow_effect_invocations(invocation_id)"""
        )
        self.connection.commit()

    def _rollback(self) -> None:
        try:
            self.connection.raw.rollback()
        except Exception:
            pass

    def get(self, identity: EffectIdentity) -> EffectReceipt | None:
        try:
            row = self.connection.execute(
                "SELECT owner, status, payload FROM workflow_effects WHERE identity = ?",
                (identity.key,),
            ).fetchone()
            if row is None:
                return None
            payload = json.loads(row[2])
            if payload.get("external_outcome") is not None:
                from .external_recovery import ExternalOutcomeDependency

                payload["external_outcome"] = ExternalOutcomeDependency.from_value(
                    payload["external_outcome"]
                )
            if payload.get("child_invocation") is not None:
                payload["child_invocation"] = ChildInvocationDependency.from_value(
                    payload["child_invocation"]
                )
            receipt = EffectReceipt(**payload)
            if receipt.owner != row[0] or receipt.status != row[1]:
                raise ValueError("workflow effect columns differ from the saved receipt")
            if receipt.external_outcome is not None or identity.operation in {
                "policy:external-action-admission:v1",
                "policy:external-action-recovery:v1",
            }:
                from .external_recovery import verify_external_receipt

                verify_external_receipt(self.connection, self._invocations, identity, receipt)
            if identity.operation == "policy:detached-child:v1":
                from .children import verify_detached_child_receipt

                verify_detached_child_receipt(self.connection, self._invocations, identity, receipt)
            if identity.operation in {"policy:child-await:v1", "policy:child-wake:v1"}:
                from .child_waits import verify_child_wait_receipt

                verify_child_wait_receipt(self.connection, self._invocations, identity, receipt)
            if identity.operation == "policy:definition-admission:v1":
                from .recovery_definitions import verify_definition_admission_receipt

                verify_definition_admission_receipt(
                    self.connection, self._invocations, identity, receipt
                )
            if identity.operation == "run:workflow" and receipt.status != "pending":
                if receipt.child_invocation is None:
                    raise ValueError("child invocation binding is absent")
                receipt.child_invocation.verify(self._invocations, identity, receipt.request_digest)
            elif (
                receipt.child_invocation is not None
                and identity.operation != "policy:child-wake:v1"
            ):
                raise ValueError("child invocation binding belongs to a different operation")
            if identity.operation == "policy:retry-schedule:v1":
                from .timing import RetrySchedule

                if receipt.status != "completed":
                    raise ValueError("retry scheduling requires an atomic decision")
                RetrySchedule.from_value(receipt.output, identity.occurrence[-1][1])
            return receipt
        except Exception:
            raise WorkflowExecutionError("workflow effect receipt could not be read") from None

    def begin(
        self,
        identity: EffectIdentity,
        request_digest: str,
        owner: str,
        *,
        operation_id: str | None = None,
        execution_lease: ExecutionLease | None = None,
    ) -> EffectReceipt:
        """Return the first owner; callers must not dispatch another owner's intent."""

        try:
            proposed = EffectReceipt(request_digest, owner, operation_id=operation_id)
            encoded = json.dumps(_receipt_payload(proposed), allow_nan=False)
            transaction = (
                self.connection.raw.transaction()
                if self.connection.is_postgresql and execution_lease is None
                else owned_write_transaction(
                    self.connection,
                    execution_lease,
                    invocation_id=identity.invocation_id,
                    workflow_fingerprint=identity.workflow_fingerprint,
                )
            )
            with transaction:
                self.connection.execute(
                    """INSERT INTO workflow_effect_invocations(
                    identity, invocation_id, fingerprint, payload)
                    VALUES (?, ?, ?, ?) ON CONFLICT(identity) DO NOTHING""",
                    (
                        identity.key,
                        identity.invocation_id,
                        identity.workflow_fingerprint,
                        json.dumps(asdict(identity), allow_nan=False),
                    ),
                )
                self.connection.execute(
                    """INSERT INTO workflow_effects(identity, owner, status, payload)
                    VALUES (?, ?, ?, ?) ON CONFLICT(identity) DO NOTHING""",
                    (identity.key, owner, proposed.status, encoded),
                )
        except InvocationStateConflict:
            self._rollback()
            raise
        except Exception:
            self._rollback()
            raise WorkflowExecutionError("workflow effect intent could not be stored") from None
        recorded = self.get(identity)
        if recorded is None:
            raise WorkflowExecutionError("workflow effect intent was not recorded")
        if recorded.request_digest != request_digest:
            raise WorkflowExecutionError("workflow effect request differs from the saved intent")
        return recorded

    def record_value(
        self,
        identity: EffectIdentity,
        request_digest: str,
        create: Callable[[], Any],
        *,
        execution_lease: ExecutionLease | None = None,
    ) -> EffectReceipt:
        """Atomically retain a private internal decision, without a pending intent.

        The factory must be synchronous and side-effect free. Clock/RNG choices
        are common policy decisions, not external dispatch or native checkpoints.
        """
        if not identity.operation.startswith("policy:"):
            raise WorkflowExecutionError("atomic decisions require an internal policy identity")
        try:
            transaction = (
                self.connection.raw.transaction()
                if self.connection.is_postgresql and execution_lease is None
                else owned_write_transaction(
                    self.connection,
                    execution_lease,
                    invocation_id=identity.invocation_id,
                    workflow_fingerprint=identity.workflow_fingerprint,
                )
            )
            with transaction:
                existing = self.get(identity)
                if existing is None:
                    output = create()
                    validate_expression_data(output)
                    proposed = EffectReceipt(
                        request_digest, "policy", status="completed", output=output
                    )
                    from ._effect_storage import insert_completed_receipt

                    insert_completed_receipt(self.connection, identity, proposed)
                recorded = self.get(identity)
                if (
                    recorded is None
                    or recorded.status != "completed"
                    or recorded.request_digest != request_digest
                ):
                    raise WorkflowExecutionError("workflow policy decision is inconsistent")
            return recorded
        except InvocationStateConflict:
            self._rollback()
            raise
        except Exception:
            self._rollback()
            raise WorkflowExecutionError("workflow policy decision could not be stored") from None

    def verify_invocation(self, invocation_id: str, fingerprint: str) -> None:
        """Reconcile known common effects even before a native task checkpoint.

        The identity index is committed atomically with admission, not inferred
        from native progress. Pending intents retain their existing resolution
        policy. No execution plan or native checkpoint is stored here.
        """
        try:
            pending: list[tuple[str, str, tuple[str, ...]]] = [(invocation_id, fingerprint, ())]
            visited: set[str] = set()
            while pending:
                current_id, current_fingerprint, ancestors = pending.pop()
                if current_id in ancestors:
                    raise ValueError("child invocation dependency cycle")
                if current_id in visited:
                    continue
                visited.add(current_id)
                rows = self.connection.execute(
                    """SELECT identity, fingerprint, payload
                    FROM workflow_effect_invocations WHERE invocation_id = ?""",
                    (current_id,),
                ).fetchall()
                for key, stored_fingerprint, payload in rows:
                    identity = EffectIdentity.from_value(json.loads(payload))
                    if (
                        identity.key != key
                        or identity.invocation_id != current_id
                        or identity.workflow_fingerprint != current_fingerprint
                        or stored_fingerprint != current_fingerprint
                    ):
                        raise ValueError("workflow effect invocation index disagrees")
                    receipt = self.get(identity)
                    if receipt is None:
                        raise ValueError("admitted workflow effect receipt is absent")
                    if receipt.child_invocation is not None:
                        if self._invocations is None:
                            raise ValueError("child invocation service is unavailable")
                        child = self._invocations.get(receipt.child_invocation.invocation_id)
                        if child is None:
                            raise ValueError("child invocation is absent")
                        pending.append(
                            (
                                child.invocation_id,
                                child.workflow_fingerprint,
                                (*ancestors, current_id),
                            )
                        )
        except Exception:
            raise WorkflowExecutionError(
                "workflow action invocation dependencies could not be reconciled"
            ) from None

    def finish(
        self,
        identity: EffectIdentity,
        owner: str,
        *,
        output: Any = None,
        error: dict[str, Any] | None = None,
        error_status_code: int | None = None,
        operation_id: str | None = None,
        child_invocation: ChildInvocationDependency | None = None,
    ) -> EffectReceipt:
        """Retain the first outcome from the intent's owner, including explicit null."""

        recorded = self.get(identity)
        if recorded is None or recorded.owner != owner:
            raise WorkflowExecutionError("workflow effect completion does not own the intent")
        try:
            proposed = replace(
                recorded,
                status="faulted" if error is not None else "completed",
                output=output,
                error=error,
                error_status_code=error_status_code,
                operation_id=operation_id if operation_id is not None else recorded.operation_id,
                child_invocation=child_invocation,
            )
            self.connection.execute(
                """UPDATE workflow_effects SET status = ?, payload = ?
                WHERE identity = ? AND owner = ? AND status = 'pending'""",
                (
                    proposed.status,
                    json.dumps(_receipt_payload(proposed), ensure_ascii=False, allow_nan=False),
                    identity.key,
                    owner,
                ),
            )
            self.connection.commit()
        except Exception:
            self._rollback()
            raise WorkflowExecutionError("workflow effect outcome could not be stored") from None
        completed = self.get(identity)
        if completed is None or completed.owner != owner or completed.status == "pending":
            raise WorkflowExecutionError("workflow effect outcome was not recorded")
        return completed

    def finish_child(
        self,
        identity: EffectIdentity,
        request_digest: str,
        child_id: str,
        *,
        execution_lease: ExecutionLease | None,
        output: Any = None,
        error: dict[str, Any] | None = None,
        error_status_code: int | None = None,
    ) -> EffectReceipt:
        """Reconcile an admitted internal child outcome under current parent authority."""
        if (
            identity.operation != "run:workflow"
            or execution_lease is None
            or self._invocations is None
        ):
            raise InvocationStateConflict("child reconciliation requires parent authority")
        with owned_write_transaction(
            self.connection,
            execution_lease,
            invocation_id=identity.invocation_id,
            workflow_fingerprint=identity.workflow_fingerprint,
        ):
            recorded = self.get(identity)
            if recorded is None or recorded.request_digest != request_digest:
                raise WorkflowExecutionError("recorded child workflow request differs")
            if recorded.status != "pending":
                return recorded
            dependency = ChildInvocationDependency.capture(self._invocations, child_id)
            dependency.verify(self._invocations, identity, request_digest)
            binding = self.connection.execute(
                "SELECT child_id FROM child_workflow_bindings WHERE binding_key=?", (identity.key,)
            ).fetchone()
            if binding is None or binding[0] != child_id:
                raise WorkflowExecutionError("child reconciliation binding differs")
            proposed = replace(
                recorded,
                status="faulted" if error else "completed",
                output=output,
                error=error,
                error_status_code=error_status_code,
                child_invocation=dependency,
            )
            self.connection.execute(
                "UPDATE workflow_effects SET status=?,payload=? "
                "WHERE identity=? AND owner=? AND status='pending'",
                (
                    proposed.status,
                    json.dumps(_receipt_payload(proposed), ensure_ascii=False, allow_nan=False),
                    identity.key,
                    recorded.owner,
                ),
            )
        completed = self.get(identity)
        if completed is None or completed.status == "pending":
            raise WorkflowExecutionError("child outcome reconciliation was not recorded")
        return completed

    def finish_external(
        self,
        identity: EffectIdentity,
        payload: Any,
        *,
        receiver: dict[str, Any],
        proof: dict[str, Any],
        execution_lease: ExecutionLease | None,
    ) -> tuple[EffectReceipt, EffectReceipt]:
        """Resolve only an originally admitted trusted HTTP receiver outcome."""
        from .external_recovery import finish_external

        return finish_external(
            self, identity, payload, receiver=receiver, proof=proof, execution_lease=execution_lease
        )

    def close(self) -> None:
        self.connection.close()
