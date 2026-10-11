"""Private execution ownership records, separate from native checkpoints."""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .errors import InvocationStateConflict, WorkflowExecutionError
from .persistence import ExecutionHandle
from .storage import StorageConnection, open_storage


@dataclass(frozen=True, slots=True)
class ExecutionLease:
    invocation_id: str
    engine: str
    workflow_fingerprint: str
    owner: str
    generation: int
    expires_at: float

    def __post_init__(self) -> None:
        if (
            any(
                not isinstance(value, str) or not value
                for value in (
                    self.invocation_id,
                    self.engine,
                    self.workflow_fingerprint,
                    self.owner,
                )
            )
            or type(self.generation) is not int
            or self.generation < 1
            or isinstance(self.expires_at, bool)
            or not isinstance(self.expires_at, (int, float))
            or not math.isfinite(self.expires_at)
            or self.expires_at <= 0
            or uuid.UUID(self.owner).hex != self.owner
        ):
            raise ValueError("invalid execution ownership record")


class ExecutionOwnershipStore:
    """Atomic expiring ownership on the common datasource's clock.

    These records alone do not fence native writes or exclude runtime execution.
    Callers must retain ownership through execution and guard every durable write.
    """

    def __init__(self, database: str | Path = ":memory:") -> None:
        self.connection: StorageConnection = open_storage(database, "owa_runtime")
        try:
            self.connection.execute(
                """CREATE TABLE IF NOT EXISTS invocation_ownership (
                invocation_id TEXT PRIMARY KEY, engine TEXT NOT NULL,
                fingerprint TEXT NOT NULL, owner TEXT NOT NULL,
                generation BIGINT NOT NULL, expires_at DOUBLE PRECISION NOT NULL)"""
            )
            self.connection.commit()
        except Exception:
            self.connection.close()
            raise WorkflowExecutionError("execution ownership storage is unavailable") from None

    @property
    def _clock(self) -> str:
        # Workers must not decide expiry from independently skewed host clocks.
        return (
            "EXTRACT(EPOCH FROM clock_timestamp())"
            if self.connection.is_postgresql
            else "((julianday('now') - 2440587.5) * 86400.0)"
        )

    @property
    def _live_expiry(self) -> str:
        # Reject non-finite/corrupt expiry, including PostgreSQL's NaN ordering.
        return f"expires_at > {self._clock} AND expires_at <= {self._clock} + 3600"

    @staticmethod
    def _duration(seconds: float) -> float:
        if (
            isinstance(seconds, bool)
            or not isinstance(seconds, (int, float))
            or not math.isfinite(seconds)
            or not 0 < seconds <= 3600
        ):
            raise ValueError("execution ownership duration must be finite and bounded")
        return float(seconds)

    def _rollback(self) -> None:
        try:
            self.connection.raw.rollback()
        except Exception:
            pass

    @staticmethod
    def _lease(row: Any) -> ExecutionLease:
        return ExecutionLease(*row)

    def acquire(self, handle: ExecutionHandle, *, seconds: float = 30) -> ExecutionLease:
        duration = self._duration(seconds)
        if any(
            not isinstance(value, str) or not value
            for value in (handle.invocation_id, handle.engine, handle.workflow_fingerprint)
        ):
            raise ValueError("execution ownership requires invocation identity")
        try:
            row = self.connection.execute(
                f"""INSERT INTO invocation_ownership
                (invocation_id, engine, fingerprint, owner, generation, expires_at)
                VALUES (?, ?, ?, ?, 1, {self._clock} + ?)
                ON CONFLICT(invocation_id) DO UPDATE SET
                owner = excluded.owner, generation = invocation_ownership.generation + 1,
                expires_at = excluded.expires_at
                WHERE invocation_ownership.expires_at <= {self._clock}
                AND invocation_ownership.engine = excluded.engine
                AND invocation_ownership.fingerprint = excluded.fingerprint
                AND invocation_ownership.generation > 0
                RETURNING invocation_id, engine, fingerprint, owner, generation, expires_at""",
                (
                    handle.invocation_id,
                    handle.engine,
                    handle.workflow_fingerprint,
                    uuid.uuid4().hex,
                    duration,
                ),
            ).fetchone()
            lease = self._lease(row) if row is not None else None
            self.connection.commit()
        except Exception:
            self._rollback()
            raise WorkflowExecutionError("execution ownership could not be acquired") from None
        if lease is None:
            raise InvocationStateConflict("invocation execution ownership is unavailable")
        return lease

    def renew(self, lease: ExecutionLease, *, seconds: float = 30) -> ExecutionLease:
        duration = self._duration(seconds)
        try:
            row = self.connection.execute(
                f"""UPDATE invocation_ownership SET expires_at = {self._clock} + ?
                WHERE invocation_id = ? AND engine = ? AND fingerprint = ?
                AND owner = ? AND generation = ? AND {self._live_expiry}
                RETURNING invocation_id, engine, fingerprint, owner, generation, expires_at""",
                (duration, *self._identity(lease)),
            ).fetchone()
            renewed = self._lease(row) if row is not None else None
            self.connection.commit()
        except Exception:
            self._rollback()
            raise WorkflowExecutionError("execution ownership could not be renewed") from None
        if renewed is None:
            raise InvocationStateConflict("invocation execution ownership has expired or changed")
        return renewed

    @staticmethod
    def _identity(lease: ExecutionLease) -> tuple[str, str, str, str, int]:
        return (
            lease.invocation_id,
            lease.engine,
            lease.workflow_fingerprint,
            lease.owner,
            lease.generation,
        )

    def verify(self, lease: ExecutionLease) -> None:
        try:
            row = self.connection.execute(
                f"""SELECT invocation_id, engine, fingerprint, owner, generation, expires_at
                FROM invocation_ownership WHERE invocation_id = ? AND engine = ?
                AND fingerprint = ? AND owner = ? AND generation = ?
                AND {self._live_expiry}""",
                self._identity(lease),
            ).fetchone()
            current = self._lease(row) if row is not None else None
            # End a read transaction too; PostgreSQL uses autocommit today.
            self.connection.commit()
        except Exception:
            self._rollback()
            raise WorkflowExecutionError("execution ownership could not be verified") from None
        if current is None or replace(current, expires_at=lease.expires_at) != lease:
            raise InvocationStateConflict("invocation execution ownership has expired or changed")

    def release(self, lease: ExecutionLease) -> None:
        try:
            # Retain the generation across release/reacquisition to prevent ABA.
            row = self.connection.execute(
                f"""UPDATE invocation_ownership SET expires_at = 0
                WHERE invocation_id = ? AND engine = ? AND fingerprint = ?
                AND owner = ? AND generation = ? AND {self._live_expiry}
                RETURNING generation""",
                self._identity(lease),
            ).fetchone()
            self.connection.commit()
        except Exception:
            self._rollback()
            raise WorkflowExecutionError("execution ownership could not be released") from None
        if row is None:
            raise InvocationStateConflict("invocation execution ownership has expired or changed")

    def close(self) -> None:
        self.connection.close()
