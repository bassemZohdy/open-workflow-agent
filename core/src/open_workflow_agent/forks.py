"""Private common decisions for competitive forks; native checkpoints stay separate."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from .errors import WorkflowExecutionError
from .expressions import validate_expression_data
from .storage import StorageConnection, open_storage


@dataclass(frozen=True, slots=True)
class ForkIdentity:
    invocation_id: str
    workflow_fingerprint: str
    task_reference: str
    occurrence: tuple[tuple[str, int], ...]

    def __post_init__(self) -> None:
        if not all((self.invocation_id, self.workflow_fingerprint, self.task_reference)):
            raise ValueError("competitive fork identity requires invocation, workflow and task")
        if not self.occurrence or any(
            not reference or type(index) is not int or index < 1
            for reference, index in self.occurrence
        ):
            raise ValueError("competitive fork identity requires positive execution occurrences")

    @property
    def key(self) -> str:
        content = json.dumps(
            ["owa/competitive-fork/v1", asdict(self)], sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(content.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ForkDecision:
    winner: int
    status: Literal["completed", "faulted", "ended"]
    output: Any
    context: Any
    error: dict[str, Any] | None
    operation_id: str | None
    cancellation_intents: tuple[int, ...]
    drained: bool = False
    error_status_code: int | None = None

    def __post_init__(self) -> None:
        if type(self.winner) is not int or self.winner < 0:
            raise ValueError("competitive fork winner must be a branch index")
        if self.status not in {"completed", "faulted", "ended"}:
            raise ValueError("competitive fork decision status is invalid")
        if (self.status == "faulted") != (self.error is not None):
            raise ValueError("competitive fork fault decisions require an error")
        if self.error_status_code is not None and (
            type(self.error_status_code) is not int or not 100 <= self.error_status_code <= 599
        ):
            raise ValueError("competitive fork common error status is invalid")
        if len(set(self.cancellation_intents)) != len(self.cancellation_intents) or any(
            type(index) is not int or index < 0 or index == self.winner
            for index in self.cancellation_intents
        ):
            raise ValueError("competitive fork cancellation intents must name distinct losers")


class ForkDecisionStore:
    """An atomic winner claim and cleanup receipt on the configured common datasource."""

    def __init__(self, database: str | Path = ":memory:") -> None:
        self.connection: StorageConnection = open_storage(database, "owa_runtime")
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS competitive_fork_decisions (
            identity TEXT PRIMARY KEY, payload TEXT NOT NULL, drained INTEGER NOT NULL)"""
        )
        self.connection.commit()

    def get(self, identity: ForkIdentity) -> ForkDecision | None:
        try:
            row = self.connection.execute(
                "SELECT payload, drained FROM competitive_fork_decisions WHERE identity = ?",
                (identity.key,),
            ).fetchone()
            if row is None:
                return None
            if row[1] not in (0, 1):
                raise ValueError("competitive fork cleanup receipt is invalid")
            payload = json.loads(row[0])
            for name in ("output", "context", "error"):
                validate_expression_data(payload[name])
            payload["cancellation_intents"] = tuple(payload["cancellation_intents"])
            payload["drained"] = bool(row[1])
            return ForkDecision(**payload)
        except Exception:
            raise WorkflowExecutionError("competitive fork decision could not be read") from None

    def claim(self, identity: ForkIdentity, decision: ForkDecision) -> ForkDecision:
        """Return the committed winner, retaining the first claim across connections."""

        try:
            payload = asdict(decision)
            payload.pop("drained")
            payload["cancellation_intents"] = list(decision.cancellation_intents)
            for name in ("output", "context", "error"):
                validate_expression_data(payload[name])
            encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True)
            self.connection.execute(
                """INSERT INTO competitive_fork_decisions(identity, payload, drained)
                VALUES (?, ?, 0) ON CONFLICT(identity) DO NOTHING""",
                (identity.key, encoded),
            )
            self.connection.commit()
        except Exception:
            try:
                self.connection.raw.rollback()
            except Exception:
                pass
            raise WorkflowExecutionError("competitive fork decision could not be stored") from None
        recorded = self.get(identity)
        if recorded is None:
            raise WorkflowExecutionError("competitive fork winner was not recorded")
        return recorded

    def mark_drained(self, identity: ForkIdentity) -> None:
        """Record in-process loser draining; this does not undo external effects."""

        try:
            updated = self.connection.execute(
                "UPDATE competitive_fork_decisions SET drained = 1 WHERE identity = ?",
                (identity.key,),
            )
            self.connection.commit()
            if updated.rowcount != 1:
                raise ValueError("competitive fork decision is absent")
        except Exception:
            raise WorkflowExecutionError("competitive fork cleanup could not be recorded") from None

    def close(self) -> None:
        self.connection.close()
