"""Private workflow action intents and outcomes, separate from native checkpoints."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Literal

from .errors import WorkflowExecutionError
from .expressions import validate_expression_data
from .storage import StorageConnection, open_storage


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
class EffectReceipt:
    request_digest: str
    owner: str
    status: Literal["pending", "completed", "faulted"] = "pending"
    output: Any = None
    error: dict[str, Any] | None = None
    error_status_code: int | None = None
    operation_id: str | None = None

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


class EffectJournal:
    """First-owner intent and immutable outcome receipts on the common datasource.

    Conditional outcome writes prevent another owner from completing an intent.
    This is not a recovery lease, a native checkpoint, or proof of exactly-once
    external actions. A pending intent with no receipt remains unresolved.
    """

    def __init__(self, database: str | Path = ":memory:") -> None:
        self.connection: StorageConnection = open_storage(database, "owa_runtime")
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS workflow_effects (
            identity TEXT PRIMARY KEY, owner TEXT NOT NULL,
            status TEXT NOT NULL, payload TEXT NOT NULL)"""
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
            receipt = EffectReceipt(**json.loads(row[2]))
            if receipt.owner != row[0] or receipt.status != row[1]:
                raise ValueError("workflow effect columns differ from the saved receipt")
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
    ) -> EffectReceipt:
        """Return the first owner; callers must not dispatch another owner's intent."""

        try:
            proposed = EffectReceipt(request_digest, owner, operation_id=operation_id)
            self.connection.execute(
                """INSERT INTO workflow_effects(identity, owner, status, payload)
                VALUES (?, ?, ?, ?) ON CONFLICT(identity) DO NOTHING""",
                (
                    identity.key,
                    owner,
                    proposed.status,
                    json.dumps(asdict(proposed), allow_nan=False),
                ),
            )
            self.connection.commit()
        except Exception:
            self._rollback()
            raise WorkflowExecutionError("workflow effect intent could not be stored") from None
        recorded = self.get(identity)
        if recorded is None:
            raise WorkflowExecutionError("workflow effect intent was not recorded")
        if recorded.request_digest != request_digest:
            raise WorkflowExecutionError("workflow effect request differs from the saved intent")
        return recorded

    def finish(
        self,
        identity: EffectIdentity,
        owner: str,
        *,
        output: Any = None,
        error: dict[str, Any] | None = None,
        error_status_code: int | None = None,
        operation_id: str | None = None,
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
            )
            self.connection.execute(
                """UPDATE workflow_effects SET status = ?, payload = ?
                WHERE identity = ? AND owner = ? AND status = 'pending'""",
                (
                    proposed.status,
                    json.dumps(asdict(proposed), ensure_ascii=False, allow_nan=False),
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

    def close(self) -> None:
        self.connection.close()
