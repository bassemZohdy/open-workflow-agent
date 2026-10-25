"""Bounded common diagnostic history, separate from execution evidence."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from .config import LifecycleHistoryConfig
from .errors import OwaError
from .observability import (
    CLOUD_EVENTS_SPECVERSION,
    LIFECYCLE_CLOUD_EVENT_SCHEMA,
    CloudEvent,
    LifecycleSubscription,
    _cloud_event_type,
)
from .storage import open_storage


class LifecycleHistoryError(OwaError):
    code = "lifecycle_history_error"


class LifecycleHistory:
    """Commit-ordered, count/byte-bounded history on the selected common DB.

    Records contain the existing sanitized lifecycle projection. They do not
    authorize an action or prove that a native checkpoint can resume. History
    retention and workflow retention are independent.
    """

    def __init__(
        self,
        database: str | Path,
        config: LifecycleHistoryConfig,
        *,
        namespace: str = "owa_observability",
    ) -> None:
        self.config = config
        self.connection = open_storage(database, namespace)
        self._lock = threading.RLock()
        self._closed = False
        with self._transaction():
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS lifecycle_history ("
                "sequence BIGINT PRIMARY KEY, event_id TEXT UNIQUE NOT NULL, "
                "payload TEXT NOT NULL, payload_bytes BIGINT NOT NULL)"
            )
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS lifecycle_history_counter ("
                "singleton BIGINT PRIMARY KEY CHECK (singleton=1), value BIGINT NOT NULL)"
            )
            self.connection.execute(
                "INSERT INTO lifecycle_history_counter (singleton,value) VALUES (1,0) "
                "ON CONFLICT(singleton) DO NOTHING"
            )

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with self._lock:
            if self._closed:
                raise LifecycleHistoryError("lifecycle history is closed")
            try:
                if self.connection.is_postgresql:
                    with self.connection.raw.transaction():
                        yield
                else:
                    self.connection.execute("BEGIN IMMEDIATE")
                    try:
                        yield
                        self.connection.commit()
                    except BaseException:
                        self.connection.raw.rollback()
                        raise
            except (LifecycleHistoryError, LookupError, BufferError):
                raise
            except Exception:
                raise LifecycleHistoryError("lifecycle history storage is unavailable") from None

    def _counter(self, *, lock: bool = True) -> int:
        query = "SELECT value FROM lifecycle_history_counter WHERE singleton=1"
        if lock and self.connection.is_postgresql:
            query += " FOR UPDATE"
        row = self.connection.execute(query).fetchone()
        if row is None or type(row[0]) is not int or not 0 <= row[0] < 2**63 - 1:
            raise LifecycleHistoryError("lifecycle history counter is invalid")
        maximum = self.connection.execute(
            "SELECT COALESCE(MAX(sequence),0) FROM lifecycle_history"
        ).fetchone()[0]
        if maximum != row[0]:
            raise LifecycleHistoryError("lifecycle history counter is inconsistent")
        return int(row[0])

    def _decode(self, payload: str, size: int) -> CloudEvent:
        try:
            if type(size) is not int or not 0 < size <= self.config.max_event_bytes:
                raise ValueError
            if len(payload.encode("utf-8")) != size:
                raise ValueError
            value = json.loads(payload)
            required = {
                "specversion",
                "id",
                "source",
                "type",
                "time",
                "datacontenttype",
                "dataschema",
                "data",
            }
            if not isinstance(value, dict) or set(value) not in (required, required | {"subject"}):
                raise ValueError
            if (
                value["specversion"] != CLOUD_EVENTS_SPECVERSION
                or value["dataschema"] != LIFECYCLE_CLOUD_EVENT_SCHEMA
                or value["source"] != "urn:open-workflow-agent:lifecycle"
                or value["datacontenttype"] != "application/json"
                or not isinstance(value["id"], str)
                or not 0 < len(value["id"].encode("utf-8")) <= 256
                or any(ord(char) < 32 or ord(char) == 127 for char in value["id"])
                or not isinstance(value["type"], str)
                or not value["type"].startswith("com.openworkflow.agent.lifecycle.")
                or not isinstance(value["time"], str)
                or not isinstance(value["data"], dict)
                or value["data"].get("event_id") != value["id"]
            ):
                raise ValueError
            data = value["data"]
            allowed = {
                "event_id",
                "event_type",
                "invocation_id",
                "session_id",
                "workflow_name",
                "workflow_version",
                "task_name",
                "task_reference",
                "engine",
                "operation_id",
                "execution_id",
                "parent_invocation_id",
                "parent_task_reference",
                "event_name",
                "duration",
                "status",
                "attempt",
                "reason",
                "progress",
                "error",
            }
            if set(data) - allowed or not isinstance(data.get("event_type"), str):
                raise ValueError
            if value["type"] != (
                f"com.openworkflow.agent.lifecycle.{_cloud_event_type(data['event_type'])}.v1"
            ):
                raise ValueError
            if value.get("subject") is not None and not isinstance(value["subject"], str):
                raise ValueError
            structured = {"duration", "attempt", "progress", "error"}
            if any(not isinstance(data[key], str) for key in set(data) - structured):
                raise ValueError
            if "duration" in data and type(data["duration"]) not in (int, float):
                raise ValueError
            if "attempt" in data and type(data["attempt"]) is not int:
                raise ValueError
            error = data.get("error")
            if error is not None and (
                not isinstance(error, dict)
                or set(error) != {"code"}
                or not isinstance(error["code"], str)
            ):
                raise ValueError
            progress = data.get("progress")
            if progress is not None and (
                not isinstance(progress, dict)
                or set(progress) - {"phase", "completed", "total"}
                or any(not isinstance(item, (bool, int, float, str)) for item in progress.values())
            ):
                raise ValueError
            # Re-encoding rejects non-finite numbers even on persisted input.
            json.dumps(value, allow_nan=False)
            value.setdefault("subject", None)
            return CloudEvent(**value)
        except Exception:
            raise LifecycleHistoryError("lifecycle history record is invalid") from None

    def append(self, event: CloudEvent) -> None:
        try:
            payload = json.dumps(
                event.as_dict(),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            size = len(payload.encode("utf-8"))
        except Exception:
            raise LifecycleHistoryError("lifecycle history event is invalid") from None
        self._decode(payload, size)
        with self._transaction():
            counter = self._counter(lock=True)
            previous = self.connection.execute(
                "SELECT payload FROM lifecycle_history WHERE event_id=?", (event.id,)
            ).fetchone()
            if previous is not None:
                if previous[0] != payload:
                    raise LifecycleHistoryError("lifecycle history event identity conflicts")
                return
            sequence = counter + 1
            self.connection.execute(
                "INSERT INTO lifecycle_history (sequence,event_id,payload,payload_bytes) "
                "VALUES (?,?,?,?)",
                (sequence, event.id, payload, size),
            )
            self.connection.execute(
                "UPDATE lifecycle_history_counter SET value=? WHERE singleton=1", (sequence,)
            )
            newest = self.connection.execute(
                "SELECT sequence,payload_bytes FROM lifecycle_history "
                "ORDER BY sequence DESC LIMIT ?",
                (self.config.max_events,),
            ).fetchall()
            retained, first = 0, sequence
            for candidate, byte_count in newest:
                if type(byte_count) is not int or byte_count < 1:
                    raise LifecycleHistoryError("lifecycle history byte count is invalid")
                if retained + byte_count > self.config.max_retained_bytes:
                    break
                retained += byte_count
                first = candidate
            self.connection.execute("DELETE FROM lifecycle_history WHERE sequence<?", (first,))

    def snapshot(self, limit: int) -> list[CloudEvent]:
        if type(limit) is not int or not 1 <= limit <= self.config.max_events:
            raise ValueError("history snapshot limit is outside the selected bound")
        with self._transaction():
            self._counter()
            rows = self.connection.execute(
                "SELECT payload,payload_bytes FROM lifecycle_history "
                "ORDER BY sequence DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [self._decode(payload, size) for payload, size in reversed(rows)]

    def replay(self, event_id: str | None) -> tuple[list[CloudEvent], int]:
        with self._transaction():
            counter = self._counter()
            if event_id is None:
                return [], counter
            row = self.connection.execute(
                "SELECT sequence FROM lifecycle_history WHERE event_id=?", (event_id,)
            ).fetchone()
            if row is None:
                raise LookupError("last lifecycle event id is outside the bounded replay window")
            return self._after(int(row[0]), self.config.max_events), counter

    def _after(self, cursor: int, limit: int) -> list[CloudEvent]:
        rows = self.connection.execute(
            "SELECT sequence,payload,payload_bytes FROM lifecycle_history "
            "WHERE sequence>? ORDER BY sequence LIMIT ?",
            (cursor, limit),
        ).fetchall()
        if rows and rows[0][0] != cursor + 1:
            raise BufferError("lifecycle history retention overtook the observer")
        return [self._decode(payload, size) for _, payload, size in rows]

    def after(self, cursor: int, limit: int) -> list[CloudEvent]:
        with self._transaction():
            self._counter()
            return self._after(cursor, limit)

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                self.connection.close()


class HistorySubscription(LifecycleSubscription):
    """Poll commit order across processes without an unbounded memory queue."""

    def __init__(
        self,
        history: LifecycleHistory,
        cursor: int,
        capacity: int,
        close_callback: Callable[[], None],
    ) -> None:
        self.history = history
        self.cursor = cursor
        self.capacity = capacity
        self.pending: list[CloudEvent] = []
        self._close_callback = close_callback
        self._closed = False

    async def receive(self, *, timeout: float) -> CloudEvent:
        deadline = time.monotonic() + timeout
        while not self._closed:
            if not self.pending:
                self.pending = self.history.after(self.cursor, self.capacity + 1)
                if len(self.pending) > self.capacity:
                    raise BufferError("lifecycle history observer exceeded its bounded queue")
            if self.pending:
                self.cursor += 1
                return self.pending.pop(0)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            await asyncio.sleep(min(remaining, self.history.config.poll_interval_seconds))
        raise RuntimeError("lifecycle history subscription is closed")
