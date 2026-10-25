"""Private schedule transaction boundary; native execution state stays elsewhere."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from threading import RLock
from typing import Any

from .errors import ScheduleValidationError
from .storage import StorageConnection


@dataclass(frozen=True, slots=True)
class ScheduleClaim:
    schedule_id: str
    occurrence_key: str
    owner: str
    generation: int
    lease_until: float


def initialize_schedule_tables(connection: StorageConnection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schedules (schedule_id TEXT PRIMARY KEY, "
        "payload TEXT NOT NULL, status TEXT NOT NULL, next_run_at DOUBLE PRECISION NOT NULL, "
        "lease_until DOUBLE PRECISION, operation_key TEXT UNIQUE)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schedule_definitions "
        "(schedule_id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schedule_slots ("
        "slot_key TEXT PRIMARY KEY, schedule_id TEXT NOT NULL, occurrence_key TEXT NOT NULL, "
        "nominal_at DOUBLE PRECISION NOT NULL, state TEXT NOT NULL, owner TEXT NOT NULL, "
        "generation BIGINT NOT NULL, lease_until DOUBLE PRECISION, invocation_id TEXT UNIQUE, "
        "input_payload TEXT NOT NULL, UNIQUE(schedule_id, occurrence_key))"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS schedule_due_slots ON schedule_slots(state, lease_until)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schedule_bindings "
        "(slot_key TEXT PRIMARY KEY, payload TEXT NOT NULL)"
    )
    if connection.is_postgresql:
        _widen_postgresql_schedule_clocks(connection)
    connection.commit()


def _widen_postgresql_schedule_clocks(connection: StorageConnection) -> None:
    """Widen private legacy float4 columns without repairing stored evidence."""
    columns = (
        ("schedules", "next_run_at"),
        ("schedules", "lease_until"),
        ("schedule_slots", "nominal_at"),
        ("schedule_slots", "lease_until"),
    )
    with connection.raw.transaction():
        rows = connection.execute(
            "SELECT table_name,column_name,udt_name FROM information_schema.columns "
            "WHERE table_schema=current_schema() AND "
            "((table_name='schedules' AND column_name IN ('next_run_at','lease_until')) "
            "OR (table_name='schedule_slots' AND column_name IN ('nominal_at','lease_until')))"
        ).fetchall()
        types = {(table, column): kind for table, column, kind in rows}
        if set(types) != set(columns) or any(
            kind not in {"float4", "float8"} for kind in types.values()
        ):
            raise ScheduleValidationError("schedule clock storage types are inconsistent")
        for table, column in columns:
            if types[table, column] == "float4":
                # Identifiers come only from the fixed private table/column list.
                # The existing SQL value is authoritative: JSON cannot restore
                # precision already lost in a legacy float4 record.
                connection.execute(
                    f"ALTER TABLE {table} ALTER COLUMN {column} TYPE DOUBLE PRECISION "
                    f"USING {column}::double precision"
                )


def slot_row(connection: StorageConnection, schedule_id: str, occurrence_key: str) -> Any:
    return connection.execute(
        "SELECT slot_key, nominal_at, state, owner, generation, lease_until, invocation_id, "
        "input_payload FROM schedule_slots WHERE schedule_id = ? AND occurrence_key = ?",
        (schedule_id, occurrence_key),
    ).fetchone()


class ScheduleTransactions:
    """Serialize admission and fencing across independent database connections."""

    def __init__(self, connection: StorageConnection) -> None:
        self.connection = connection
        self.lock = RLock()
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schedule_admission_clock "
            "(clock TEXT PRIMARY KEY, value BIGINT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schedule_admission_clock(clock, value) VALUES ('admission', 0) "
            "ON CONFLICT(clock) DO NOTHING"
        )
        connection.commit()

    @staticmethod
    def serialize(connection: StorageConnection) -> None:
        query = "SELECT value FROM schedule_admission_clock WHERE clock = 'admission'"
        if connection.is_postgresql:
            query += " FOR UPDATE"
        if connection.execute(query).fetchone() is None:
            raise ValueError("schedule admission clock is absent")

    @contextmanager
    def transaction(self) -> Iterator[StorageConnection]:
        with self.lock:
            if self.connection.is_postgresql:
                with self.connection.raw.transaction():
                    self.serialize(self.connection)
                    yield self.connection
            else:
                self.connection.execute("BEGIN IMMEDIATE")
                try:
                    self.serialize(self.connection)
                    yield self.connection
                    self.connection.commit()
                except BaseException:
                    self.connection.raw.rollback()
                    raise
