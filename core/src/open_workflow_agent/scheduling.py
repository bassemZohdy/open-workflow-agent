"""Bounded, durable workflow scheduling owned by one runtime process."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from .errors import ScheduleOperationConflict, ScheduleValidationError
from .persistence import ExecutionHandle
from .sandbox import resolve_and_compile_sandbox_workflow
from .storage import StorageConnection, open_storage
from .workflow import WorkflowPlan, _duration_seconds

SCHEDULE_STATES = frozenset({"active", "completed", "cancelled", "faulted"})


@dataclass(slots=True)
class ScheduleRecord:
    schedule_id: str
    workflow_name: str
    workflow_version: str
    workflow_fingerprint: str
    workflow_source_json: str
    input_data: Any
    kind: str
    interval_seconds: float | None
    next_run_at: float
    status: str = "active"
    last_invocation_id: str | None = None
    last_status: str | None = None
    lease_until: float | None = None

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "schedule_id": self.schedule_id,
            "workflow_name": self.workflow_name,
            "workflow_version": self.workflow_version,
            "workflow_fingerprint": self.workflow_fingerprint,
            "kind": self.kind,
            "status": self.status,
            "next_run_at": _timestamp(self.next_run_at),
        }
        if self.interval_seconds is not None:
            value["interval_seconds"] = self.interval_seconds
        if self.last_invocation_id is not None:
            value["last_invocation_id"] = self.last_invocation_id
        if self.last_status is not None:
            value["last_status"] = self.last_status
        return value


class ScheduleStore:
    """Durable schedule metadata with no engine-native state."""

    def __init__(self, database: str | Path = ":memory:") -> None:
        self.connection: StorageConnection = open_storage(database, "owa_runtime")
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS schedules (
            schedule_id TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            status TEXT NOT NULL,
            next_run_at REAL NOT NULL,
            lease_until REAL,
            operation_key TEXT UNIQUE)"""
        )
        self.connection.commit()

    def create(
        self,
        plan: WorkflowPlan,
        input_data: Any,
        *,
        operation_key: str | None = None,
        now: float | None = None,
    ) -> ScheduleRecord:
        kind, interval_seconds = schedule_period(plan.source.get("schedule"))
        if operation_key:
            existing = self.connection.execute(
                "SELECT payload, status, next_run_at, lease_until FROM schedules "
                "WHERE operation_key = ?",
                (operation_key,),
            ).fetchone()
            if existing:
                record = self._decode(existing)
                if record.workflow_fingerprint != plan.fingerprint:
                    raise ScheduleOperationConflict(
                        "idempotency key belongs to another workflow schedule",
                        details={"operation_key": operation_key},
                    )
                return record
        try:
            input_json = json.dumps(input_data, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise ScheduleValidationError("schedule input must be JSON serializable") from exc
        record = ScheduleRecord(
            schedule_id=str(uuid.uuid4()),
            workflow_name=plan.name,
            workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
            workflow_source_json=plan.source_json,
            input_data=json.loads(input_json),
            kind=kind,
            interval_seconds=interval_seconds,
            next_run_at=(now if now is not None else time.time()) + interval_seconds,
        )
        payload = json.dumps(_payload(record), ensure_ascii=False, sort_keys=True)
        try:
            self.connection.execute(
                "INSERT INTO schedules("
                "schedule_id, payload, status, next_run_at, lease_until, operation_key) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    record.schedule_id,
                    payload,
                    record.status,
                    record.next_run_at,
                    None,
                    operation_key,
                ),
            )
            self.connection.commit()
        except Exception:
            if operation_key:
                existing = self.connection.execute(
                    "SELECT payload, status, next_run_at, lease_until FROM schedules "
                    "WHERE operation_key = ?",
                    (operation_key,),
                ).fetchone()
                if existing:
                    return self._decode(existing)
            raise
        return record

    def get(self, schedule_id: str) -> ScheduleRecord | None:
        row = self.connection.execute(
            "SELECT payload, status, next_run_at, lease_until FROM schedules WHERE schedule_id = ?",
            (schedule_id,),
        ).fetchone()
        return self._decode(row) if row else None

    def claim_due(
        self, *, now: float | None = None, lease_seconds: float = 30.0
    ) -> ScheduleRecord | None:
        current = time.time() if now is None else now
        row = self.connection.execute(
            "SELECT payload, status, next_run_at, lease_until FROM schedules "
            "WHERE status = 'active' AND next_run_at <= ? "
            "AND (lease_until IS NULL OR lease_until < ?) "
            "ORDER BY next_run_at, schedule_id LIMIT 1",
            (current, current),
        ).fetchone()
        if not row:
            return None
        record = self._decode(row)
        record.lease_until = current + lease_seconds
        self._save(record)
        return record

    def set_invocation(self, schedule_id: str, invocation_id: str) -> ScheduleRecord:
        record = self._require(schedule_id)
        record.last_invocation_id = invocation_id
        self._save(record)
        return record

    def finish(
        self,
        schedule_id: str,
        *,
        invocation_id: str,
        status: str,
        now: float | None = None,
    ) -> ScheduleRecord:
        if status not in {"completed", "faulted", "cancelled"}:
            raise ScheduleValidationError(f"unsupported scheduled invocation status: {status}")
        record = self._require(schedule_id)
        record.last_invocation_id = invocation_id
        record.last_status = status
        record.lease_until = None
        if record.status == "cancelled":
            self._save(record)
            return record
        if status == "completed" and record.kind == "every":
            current = time.time() if now is None else now
            record.next_run_at = current + float(record.interval_seconds or 0)
            record.status = "active"
        elif status == "completed":
            record.status = "completed"
        else:
            record.status = status
        self._save(record)
        return record

    def cancel(self, schedule_id: str) -> ScheduleRecord:
        record = self._require(schedule_id)
        if record.status not in SCHEDULE_STATES:
            raise ScheduleValidationError(f"unsupported schedule status: {record.status}")
        if record.status == "active":
            record.status = "cancelled"
            record.lease_until = None
            self._save(record)
        return record

    def _require(self, schedule_id: str) -> ScheduleRecord:
        record = self.get(schedule_id)
        if record is None:
            raise KeyError(schedule_id)
        return record

    def _save(self, record: ScheduleRecord) -> None:
        self.connection.execute(
            "UPDATE schedules SET payload = ?, status = ?, next_run_at = ?, lease_until = ? "
            "WHERE schedule_id = ?",
            (
                json.dumps(_payload(record), ensure_ascii=False, sort_keys=True),
                record.status,
                record.next_run_at,
                record.lease_until,
                record.schedule_id,
            ),
        )
        self.connection.commit()

    @staticmethod
    def _decode(row: Any) -> ScheduleRecord:
        payload = json.loads(row[0])
        payload["status"] = row[1]
        payload["next_run_at"] = row[2]
        payload["lease_until"] = row[3]
        return ScheduleRecord(**payload)

    def close(self) -> None:
        self.connection.close()


class WorkflowScheduler:
    """Single-runtime scheduler; rows survive restart and leases allow reclaim."""

    def __init__(self, services: Any, engine: Any, *, poll_seconds: float = 0.05) -> None:
        self.services = services
        self.engine = engine
        self.poll_seconds = poll_seconds
        self._task: asyncio.Task[None] | None = None
        self._active: dict[str, ExecutionHandle] = {}

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    async def cancel(self, schedule_id: str, *, operation_id: str | None = None) -> ScheduleRecord:
        record = self.services.schedules.cancel(schedule_id)
        handle = self._active.get(schedule_id)
        if handle is not None and handle.status not in {"completed", "faulted", "cancelled"}:
            await self.engine.cancel(handle, operation_id=operation_id)
        return cast(ScheduleRecord, record)

    async def _run(self) -> None:
        while True:
            record = self.services.schedules.claim_due(lease_seconds=30.0)
            if record is None:
                await asyncio.sleep(self.poll_seconds)
                continue
            await self._execute(record)

    async def _execute(self, record: ScheduleRecord) -> None:
        plan = await resolve_and_compile_sandbox_workflow(
            json.loads(record.workflow_source_json),
            sandbox=self.services.config.sandbox,
            trusted_catalogs=self.services.config.workflow.external_catalogs,
            resolver=self.services.external_catalogs,
            catalog=self.services.catalog,
        )
        handle = self.services.invocations.create(
            engine=self.engine.engine_name,
            session_id=None,
            user_id=None,
            workflow_name=plan.name,
            workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
        )
        self.services.schedules.set_invocation(record.schedule_id, handle.invocation_id)
        self._active[record.schedule_id] = handle
        try:
            result = await self.engine.invoke(plan, handle, record.input_data)
            self.services.schedules.finish(
                record.schedule_id,
                invocation_id=result.invocation_id,
                status=result.status,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            self.services.schedules.finish(
                record.schedule_id,
                invocation_id=handle.invocation_id,
                status="faulted",
            )
        finally:
            self._active.pop(record.schedule_id, None)


def schedule_period(schedule: Any) -> tuple[str, float]:
    if not isinstance(schedule, Mapping):
        raise ScheduleValidationError("workflow schedule must be an object")
    unsupported = sorted(set(schedule) & {"cron", "on", "read"})
    if unsupported:
        raise ScheduleValidationError(
            "schedule features are unsupported in the bounded profile",
            details={"unsupported": unsupported},
        )
    configured = [(key, schedule.get(key)) for key in ("after", "every") if key in schedule]
    if len(configured) != 1:
        raise ScheduleValidationError("schedule requires exactly one of after or every")
    kind, value = configured[0]
    seconds = _duration_seconds(value)
    if seconds is None or seconds <= 0:
        raise ScheduleValidationError("schedule duration must be greater than zero")
    return kind, float(seconds)


def _payload(record: ScheduleRecord) -> dict[str, Any]:
    return {
        "schedule_id": record.schedule_id,
        "workflow_name": record.workflow_name,
        "workflow_version": record.workflow_version,
        "workflow_fingerprint": record.workflow_fingerprint,
        "workflow_source_json": record.workflow_source_json,
        "input_data": record.input_data,
        "kind": record.kind,
        "interval_seconds": record.interval_seconds,
        "next_run_at": record.next_run_at,
        "last_invocation_id": record.last_invocation_id,
        "last_status": record.last_status,
    }


def _timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, tz=UTC).isoformat().replace("+00:00", "Z")
