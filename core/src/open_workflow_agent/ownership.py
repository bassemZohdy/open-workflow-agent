"""Private execution ownership records, separate from native checkpoints."""

from __future__ import annotations

import asyncio
import math
import sqlite3
import uuid
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from .errors import InvocationStateConflict, InvocationStateError, WorkflowExecutionError
from .persistence import ExecutionHandle
from .storage import StorageConnection, is_postgresql_datasource, open_storage, resolve_datasource


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


def execution_write_guard(
    connection: StorageConnection, lease: ExecutionLease
) -> tuple[str, tuple[str, str, str, str, int]]:
    """Guard a common write inside its own storage transaction."""
    return _write_guard(lease, postgresql=connection.is_postgresql)


@contextmanager
def owned_write_transaction(
    connection: StorageConnection,
    lease: ExecutionLease | None,
    *,
    invocation_id: str,
    workflow_fingerprint: str,
) -> Iterator[None]:
    """Keep authority locked through a common intent/progress write and commit.

    PostgreSQL common connections use autocommit, so the row lock needs an
    explicit transaction. SQLite requires a writer lock before checking the
    owner; a read-only preflight would permit a takeover before the write.
    This guard deliberately does not govern first-dispatch outcome receipts.
    """

    if lease is None:
        yield
        connection.commit()
        return
    if lease.invocation_id != invocation_id or lease.workflow_fingerprint != workflow_fingerprint:
        raise InvocationStateConflict("invocation execution ownership is inconsistent")
    guard, parameters = execution_write_guard(connection, lease)
    if connection.is_postgresql:
        with connection.raw.transaction():
            if connection.execute(guard, parameters).fetchone() is None:
                raise InvocationStateConflict("invocation execution ownership was lost")
            yield
        return
    try:
        locked = connection.execute(
            "UPDATE invocation_ownership SET owner = owner "
            f"WHERE invocation_id = ? AND EXISTS ({guard})",
            (invocation_id, *parameters),
        )
        if locked.rowcount != 1:
            raise InvocationStateConflict("invocation execution ownership was lost")
        yield
        connection.commit()
    except BaseException:
        connection.raw.rollback()
        raise


def _write_guard(
    lease: ExecutionLease, *, postgresql: bool, table: str = "invocation_ownership"
) -> tuple[str, tuple[str, str, str, str, int]]:
    clock = (
        "EXTRACT(EPOCH FROM clock_timestamp())"
        if postgresql
        else "((julianday('now') - 2440587.5) * 86400.0)"
    )
    statement = f"""SELECT 1 FROM {table} WHERE invocation_id = ?
        AND engine = ? AND fingerprint = ? AND owner = ? AND generation = ?
        AND expires_at > {clock} AND expires_at <= {clock} + 3600"""
    if postgresql:
        statement += " FOR SHARE"
    return statement, (
        lease.invocation_id,
        lease.engine,
        lease.workflow_fingerprint,
        lease.owner,
        lease.generation,
    )


@dataclass(frozen=True, slots=True, repr=False)
class NativeWriteFence:
    """Private authority used by engine-owned checkpoint transactions.

    The SDK retains its checkpoint format, SQL and commit boundaries. Native
    writers must lock the common ownership row through their native commit.
    """

    database: str
    lease: ExecutionLease

    def postgresql_guard(self) -> tuple[str, tuple[str, str, str, str, int]]:
        return _write_guard(self.lease, postgresql=True, table="owa_runtime.invocation_ownership")


class _AsyncFenceCursor(Protocol):
    async def fetchone(self) -> Any: ...

    async def close(self) -> None: ...


class _AsyncFenceConnection(Protocol):
    def execute(
        self, statement: str, parameters: tuple[Any, ...] = ()
    ) -> Awaitable[_AsyncFenceCursor]: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


async def install_sqlite_native_fence(
    connection: _AsyncFenceConnection,
    fence: NativeWriteFence,
    *,
    native_database: str,
    tables: tuple[str, ...],
) -> None:
    """Bind connection-local triggers without changing SDK-owned schemas.

    A no-op common ownership UPDATE acquires SQLite's write lock in the same
    transaction as each native mutation. A SELECT alone would allow takeover
    during a WAL-mode native transaction. Values are bound in a temporary table,
    never interpolated into trigger SQL or saved in a native checkpoint.
    """
    if is_postgresql_datasource(fence.database) or ":memory:" in {
        fence.database,
        native_database,
    }:
        raise InvocationStateError("native execution ownership requires shared durable storage")
    if any(not name.isascii() or not name.isidentifier() for name in tables):
        raise ValueError("invalid native storage table")

    async def execute(statement: str, parameters: tuple[Any, ...] = ()) -> None:
        cursor = await connection.execute(statement, parameters)
        await cursor.close()

    try:
        if Path(native_database).resolve() != Path(fence.database).resolve():
            cursor = await connection.execute(
                "SELECT 1 FROM main.sqlite_master WHERE name = 'invocation_ownership'"
            )
            try:
                if await cursor.fetchone() is not None:
                    raise InvocationStateError("native execution ownership storage is inconsistent")
            finally:
                await cursor.close()
            await execute("ATTACH DATABASE ? AS owa_owner", (fence.database,))
        await execute(
            """CREATE TEMP TABLE owa_execution_token (
            invocation_id TEXT, engine TEXT, fingerprint TEXT, owner TEXT, generation BIGINT)"""
        )
        statement, values = _write_guard(fence.lease, postgresql=False)
        await execute("INSERT INTO owa_execution_token VALUES (?, ?, ?, ?, ?)", values)
        predicate = statement.partition("WHERE ")[2]
        for column in ("invocation_id", "engine", "fingerprint", "owner", "generation"):
            predicate = predicate.replace(
                "?", f"(SELECT {column} FROM temp.owa_execution_token)", 1
            )
        for table in tables:
            for operation in ("INSERT", "UPDATE", "DELETE"):
                await execute(
                    f"""CREATE TEMP TRIGGER owa_fence_{table}_{operation}
                    BEFORE {operation} ON main.\"{table}\" BEGIN
                    UPDATE invocation_ownership SET expires_at = expires_at WHERE {predicate};
                    SELECT CASE WHEN changes() != 1 THEN
                        RAISE(ABORT, 'OWA_EXECUTION_OWNERSHIP_LOST') END;
                    END"""
                )
        await connection.commit()
    except BaseException:
        await connection.rollback()
        raise


def translate_native_fence_error(error: BaseException) -> None:
    """Translate only the connection-local SQLite ownership rejection."""
    if isinstance(error, sqlite3.IntegrityError) and str(error) == "OWA_EXECUTION_OWNERSHIP_LOST":
        raise InvocationStateConflict(
            "invocation execution ownership has expired or changed"
        ) from None


async def run_native_storage_write[T](operation: Callable[[], Awaitable[T]]) -> T:
    """Drain one SDK database transaction on an independent event loop.

    Common persistence has a synchronous API. Holding its ownership lock while
    an SDK transaction needs the main loop to commit could deadlock that API.
    Only the native storage operation runs here; workflow execution stays on
    its engine. Cancellation drains the transaction before closing connections
    or releasing execution ownership.
    """

    async def invoke() -> T:
        return await operation()

    task = asyncio.create_task(asyncio.to_thread(lambda: asyncio.run(invoke())))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled():
            task.exception()
        raise


class ExecutionOwnershipStore:
    """Atomic expiring ownership on the common datasource's clock.

    These records alone do not fence native writes or exclude runtime execution.
    Callers must retain ownership through execution and guard every durable write.
    """

    def __init__(self, database: str | Path = ":memory:") -> None:
        self._database = resolve_datasource(str(database)) or str(database)
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

    def native_fence(self, lease: ExecutionLease) -> NativeWriteFence:
        return NativeWriteFence(self._database, lease)

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
