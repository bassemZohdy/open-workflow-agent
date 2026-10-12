"""Private schedule transaction boundary; native execution state stays elsewhere."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from threading import RLock
from typing import Any

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
        "payload TEXT NOT NULL, status TEXT NOT NULL, next_run_at REAL NOT NULL, "
        "lease_until REAL, operation_key TEXT UNIQUE)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schedule_definitions "
        "(schedule_id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schedule_slots ("
        "slot_key TEXT PRIMARY KEY, schedule_id TEXT NOT NULL, occurrence_key TEXT NOT NULL, "
        "nominal_at REAL NOT NULL, state TEXT NOT NULL, owner TEXT NOT NULL, "
        "generation BIGINT NOT NULL, lease_until REAL, invocation_id TEXT UNIQUE, "
        "input_payload TEXT NOT NULL, UNIQUE(schedule_id, occurrence_key))"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS schedule_due_slots ON schedule_slots(state, lease_until)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schedule_bindings "
        "(slot_key TEXT PRIMARY KEY, payload TEXT NOT NULL)"
    )
    connection.commit()


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
