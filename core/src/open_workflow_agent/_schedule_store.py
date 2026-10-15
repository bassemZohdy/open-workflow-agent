"""Fenced embedded workflow-start slots; no framework checkpoint data."""

from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ._schedule_storage import (
    ScheduleClaim,
    ScheduleTransactions,
    initialize_schedule_tables,
    slot_row,
)
from .errors import ScheduleOperationConflict, ScheduleValidationError
from .expressions import validate_expression_data
from .persistence import ExecutionHandle, InvocationStore
from .schedule_profile import next_due, parse_schedule
from .storage import StorageConnection

if TYPE_CHECKING:
    from .workflow import WorkflowPlan

SCHEDULE_STATES = frozenset({"active", "completed", "cancelled", "faulted"})
_TERMINAL = frozenset({"completed", "cancelled", "faulted"})


def _json(value: Any) -> str:
    validate_expression_data(value)
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


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
    owner_principal: str | None = None
    event_scope: str = "anonymous"
    timezone: str = "UTC"
    next_sequence: int = 1
    execution_authority_digest: str | None = None
    _claim: ScheduleClaim | None = field(default=None, init=False, repr=False, compare=False)

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "schedule_id": self.schedule_id,
            "workflow_name": self.workflow_name,
            "workflow_version": self.workflow_version,
            "workflow_fingerprint": self.workflow_fingerprint,
            "kind": self.kind,
            "status": self.status,
        }
        if self.kind != "on" and self.next_run_at:
            value["next_run_at"] = (
                datetime.fromtimestamp(self.next_run_at, UTC).isoformat().replace("+00:00", "Z")
            )
        if self.interval_seconds is not None:
            value["interval_seconds"] = self.interval_seconds
        if self.last_invocation_id is not None:
            value["last_invocation_id"] = self.last_invocation_id
        if self.last_status is not None:
            value["last_status"] = self.last_status
        return value


def _payload(record: ScheduleRecord) -> dict[str, Any]:
    return {
        key: getattr(record, key)
        for key in record.__dataclass_fields__
        if key not in {"_claim", "lease_until"}
        and not (key == "execution_authority_digest" and getattr(record, key) is None)
    }


def _definition(record: ScheduleRecord) -> str:
    value = _payload(record)
    for key in ("status", "last_invocation_id", "last_status", "next_run_at", "next_sequence"):
        value.pop(key)
    return _json(value)


class ScheduleStore:
    """One durable slot/invocation binding, with transactional owner fencing."""

    def __init__(
        self, database: str | Path = ":memory:", *, invocations: InvocationStore | None = None
    ) -> None:
        self._owns_invocations = invocations is None
        self.invocations = invocations or InvocationStore(database)
        self.connection = self.invocations.connection
        initialize_schedule_tables(self.connection)
        self.transactions = ScheduleTransactions(self.connection)
        self._failpoint: Callable[[str], None] = lambda _name: None
        self.trigger_scan_cursor: str | None = None
        self.trigger_scan_errors: tuple[tuple[str, Exception], ...] = ()
        self.authority_expectation: Callable[[ScheduleRecord], str | None] | None = None
        self.authority_verifier: Callable[[StorageConnection, ScheduleRecord], None] | None = None
        self.authority_participant: Callable[[StorageConnection, ScheduleRecord], None] | None = (
            None
        )
        self.invocation_authority_participant: (
            Callable[[StorageConnection, ScheduleRecord, ExecutionHandle, ScheduleClaim], None]
            | None
        ) = None

    def _get(self, connection: StorageConnection, schedule_id: str) -> ScheduleRecord | None:
        row = connection.execute(
            "SELECT payload, status, next_run_at, lease_until FROM schedules WHERE schedule_id = ?",
            (schedule_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(row[0])
            if payload["status"] != row[1] or payload["next_run_at"] != row[2]:
                raise ValueError("schedule columns disagree")
            record = ScheduleRecord(**payload, lease_until=row[3])
            saved = connection.execute(
                "SELECT payload FROM schedule_definitions WHERE schedule_id = ?", (schedule_id,)
            ).fetchone()
            if saved is None or saved[0] != _definition(record):
                raise ValueError("schedule definition evidence disagrees")
            if record.status not in SCHEDULE_STATES or type(record.next_sequence) is not int:
                raise ValueError("schedule state is invalid")
            if record.kind != "on":
                occurrences = connection.execute(
                    "SELECT occurrence_key FROM schedule_slots WHERE schedule_id=?",
                    (schedule_id,),
                ).fetchall()
                if {item[0] for item in occurrences} != {
                    "slot:" + str(index) for index in range(1, record.next_sequence)
                }:
                    raise ValueError("schedule occurrence evidence is missing")
            profile = parse_schedule(json.loads(record.workflow_source_json).get("schedule"))
            if profile.kind != record.kind or profile.interval_seconds != record.interval_seconds:
                raise ValueError("schedule profile disagrees")
            if self.authority_verifier is not None:
                self.authority_verifier(connection, record)
            return record
        except Exception:
            raise ScheduleValidationError(
                "schedule evidence is unavailable or inconsistent"
            ) from None

    def _require(self, connection: StorageConnection, schedule_id: str) -> ScheduleRecord:
        record = self._get(connection, schedule_id)
        if record is None:
            raise KeyError(schedule_id)
        return record

    @staticmethod
    def _save(connection: StorageConnection, record: ScheduleRecord) -> None:
        connection.execute(
            "UPDATE schedules SET payload = ?, status = ?, next_run_at = ?, lease_until = ? "
            "WHERE schedule_id = ?",
            (
                _json(_payload(record)),
                record.status,
                record.next_run_at,
                record.lease_until,
                record.schedule_id,
            ),
        )

    def create(
        self,
        plan: WorkflowPlan,
        input_data: Any,
        *,
        operation_key: str | None = None,
        now: float | None = None,
        owner_principal: str | None = None,
        event_scope: str = "anonymous",
        timezone: str = "UTC",
    ) -> ScheduleRecord:
        profile = parse_schedule(plan.source.get("schedule"))
        current = time.time() if now is None else now
        if not math.isfinite(current) or not event_scope:
            raise ScheduleValidationError("schedule clock or trusted scope is invalid")
        try:
            original_input = json.loads(_json(input_data))
        except Exception:
            raise ScheduleValidationError("schedule input must be JSON serializable") from None
        with self.transactions.transaction() as connection:
            if operation_key:
                row = connection.execute(
                    "SELECT schedule_id FROM schedules WHERE operation_key = ?", (operation_key,)
                ).fetchone()
                if row:
                    existing = self._require(connection, row[0])
                    if (
                        existing.workflow_fingerprint != plan.fingerprint
                        or existing.owner_principal != owner_principal
                        or existing.event_scope != event_scope
                    ):
                        raise ScheduleOperationConflict(
                            "idempotency key belongs to another schedule"
                        )
                    return existing
            record = ScheduleRecord(
                str(uuid.uuid4()),
                plan.name,
                plan.version,
                plan.fingerprint,
                plan.source_json,
                original_input,
                profile.kind,
                profile.interval_seconds,
                next_due(profile, current, timezone) if profile.kind != "on" else 0.0,
                owner_principal=owner_principal,
                event_scope=event_scope,
                timezone=timezone,
            )
            if self.authority_expectation is not None:
                record.execution_authority_digest = self.authority_expectation(record)
            connection.execute(
                "INSERT INTO schedules(schedule_id,payload,status,next_run_at,"
                "lease_until,operation_key)"
                " VALUES (?, ?, ?, ?, NULL, ?)",
                (
                    record.schedule_id,
                    _json(_payload(record)),
                    record.status,
                    record.next_run_at,
                    operation_key,
                ),
            )
            connection.execute(
                "INSERT INTO schedule_definitions(schedule_id,payload) VALUES (?, ?)",
                (record.schedule_id, _definition(record)),
            )
            if self.authority_participant is not None:
                self.authority_participant(connection, record)
            return record

    def get(self, schedule_id: str) -> ScheduleRecord | None:
        return self._get(self.connection, schedule_id)

    def status_counts(self) -> dict[str, int]:
        return dict(
            self.connection.execute(
                "SELECT status, COUNT(*) FROM schedules GROUP BY status"
            ).fetchall()
        )

    def active_triggers(
        self,
        *,
        limit: int = 128,
        after: str | None = None,
    ) -> tuple[ScheduleRecord, ...]:
        if type(limit) is not int or limit < 1:
            raise ScheduleValidationError("schedule trigger scan bound is invalid")
        rows = self.connection.execute(
            "SELECT schedule_id FROM schedules WHERE status='active' "
            + ("AND schedule_id>? " if after is not None else "")
            + "ORDER BY schedule_id LIMIT ?",
            (after, limit) if after is not None else (limit,),
        ).fetchall()
        self.trigger_scan_cursor = rows[-1][0] if rows else None
        records: list[ScheduleRecord] = []
        errors: list[tuple[str, Exception]] = []
        for (schedule_id,) in rows:
            try:
                record = self._require(self.connection, schedule_id)
                if record.kind == "on":
                    records.append(record)
            except Exception as exc:
                errors.append((schedule_id, exc))
        self.trigger_scan_errors = tuple(errors)
        return tuple(records)

    @staticmethod
    def _slot_key(schedule_id: str, occurrence_key: str) -> str:
        return hashlib.sha256(_json([schedule_id, occurrence_key]).encode()).hexdigest()

    @staticmethod
    def _fence(row: Any, claim: ScheduleClaim, now: float) -> None:
        if (
            row is None
            or row[3] != claim.owner
            or row[4] != claim.generation
            or row[5] != claim.lease_until
            or row[5] <= now
            or row[2] in _TERMINAL
        ):
            raise ScheduleOperationConflict("schedule occurrence claim is stale")

    def _binding(
        self, connection: StorageConnection, row: Any, record: ScheduleRecord
    ) -> ExecutionHandle:
        saved = connection.execute(
            "SELECT payload FROM schedule_bindings WHERE slot_key = ?", (row[0],)
        ).fetchone()
        invocation = connection.execute(
            "SELECT payload,status FROM invocations WHERE invocation_id = ?", (row[6],)
        ).fetchone()
        original = connection.execute(
            "SELECT fingerprint,payload FROM invocation_inputs WHERE invocation_id = ?", (row[6],)
        ).fetchone()
        if saved is None or invocation is None or original is None:
            raise ScheduleValidationError("scheduled invocation evidence is missing")
        try:
            payload = json.loads(invocation[0])
            payload.pop("_revision", None)
            handle = ExecutionHandle(**payload)
            immutable = {
                key: getattr(handle, key)
                for key in (
                    "invocation_id",
                    "engine",
                    "engine_execution_reference",
                    "session_id",
                    "workflow_name",
                    "workflow_version",
                    "workflow_fingerprint",
                    "owner_principal",
                )
            }
            if (
                saved[0] != _json(immutable)
                or handle.status != invocation[1]
                or handle.workflow_fingerprint != record.workflow_fingerprint
                or handle.owner_principal != record.owner_principal
                or original[0] != record.workflow_fingerprint
                or _json(json.loads(original[1])) != row[7]
            ):
                raise ValueError("scheduled invocation binding disagrees")
            handle._persisted_payload = invocation[0]
            if handle.status in _TERMINAL:
                self.invocations.terminal_completed_at(handle)
            return handle
        except Exception:
            raise ScheduleValidationError("scheduled invocation evidence is inconsistent") from None

    def claim_due(
        self,
        *,
        now: float | None = None,
        lease_seconds: float = 30.0,
        owner: str | None = None,
        schedule_id: str | None = None,
        exclude_schedule_ids: tuple[str, ...] = (),
        eligible_invocation: Callable[[ExecutionHandle], bool] | None = None,
    ) -> ScheduleRecord | None:
        current = time.time() if now is None else now
        if not math.isfinite(current) or not math.isfinite(lease_seconds) or lease_seconds <= 0:
            raise ScheduleValidationError("schedule lease is invalid")
        owner = owner or uuid.uuid4().hex
        restricted = "AND schedule_id=? " if schedule_id is not None else ""
        if exclude_schedule_ids:
            restricted += (
                "AND schedule_id NOT IN (" + ",".join("?" for _ in exclude_schedule_ids) + ") "
            )
        parameters: tuple[Any, ...] = (current,)
        if schedule_id is not None:
            parameters += (schedule_id,)
        parameters += exclude_schedule_ids
        with self.transactions.transaction() as connection:
            recoverable = connection.execute(
                "SELECT schedule_id,occurrence_key FROM schedule_slots WHERE "
                "state IN ('pending','running','waiting') "
                "AND (lease_until IS NULL OR lease_until <= ?) "
                "AND (state != 'waiting' OR NOT EXISTS (SELECT 1 FROM invocations "
                "WHERE invocations.invocation_id=schedule_slots.invocation_id "
                "AND invocations.status NOT IN ('completed','faulted','cancelled'))) "
                + restricted
                + "ORDER BY nominal_at,slot_key LIMIT 128",
                parameters,
            ).fetchall()
            for recovered_schedule_id, occurrence in recoverable:
                record = self._require(connection, recovered_schedule_id)
                if record.status != "active":
                    continue
                row = slot_row(connection, recovered_schedule_id, occurrence)
                if row[6] is not None:
                    handle = self._binding(connection, row, record)
                    if row[2] == "waiting" and handle.status not in _TERMINAL:
                        continue
                    if eligible_invocation is not None and not eligible_invocation(handle):
                        continue
                return self._claim(
                    connection, record, occurrence, row, owner, current, lease_seconds
                )
            due = connection.execute(
                "SELECT schedule_id FROM schedules WHERE status='active' AND next_run_at > 0 "
                "AND next_run_at <= ? " + restricted + "ORDER BY next_run_at,schedule_id LIMIT 1",
                parameters,
            ).fetchone()
            if due is None:
                return None
            record = self._require(connection, due[0])
            occurrence = "slot:" + str(record.next_sequence)
            key = self._slot_key(record.schedule_id, occurrence)
            connection.execute(
                "INSERT INTO schedule_slots(slot_key,schedule_id,occurrence_key,nominal_at,state,"
                "owner,generation,lease_until,invocation_id,input_payload) "
                "VALUES (?, ?, ?, ?, 'pending', '', 0, NULL, NULL, ?)",
                (key, record.schedule_id, occurrence, record.next_run_at, _json(record.input_data)),
            )
            record.next_sequence += 1
            profile = parse_schedule(json.loads(record.workflow_source_json)["schedule"])
            record.next_run_at = (
                next_due(profile, record.next_run_at, record.timezone)
                if record.kind in {"every", "cron"}
                else 0.0
            )
            self._save(connection, record)
            row = slot_row(connection, record.schedule_id, occurrence)
            return self._claim(connection, record, occurrence, row, owner, current, lease_seconds)

    def _claim(
        self,
        connection: StorageConnection,
        record: ScheduleRecord,
        occurrence: str,
        row: Any,
        owner: str,
        now: float,
        duration: float,
    ) -> ScheduleRecord:
        generation, until = row[4] + 1, now + duration
        connection.execute(
            "UPDATE schedule_slots SET owner=?,generation=?,lease_until=? WHERE slot_key=?",
            (owner, generation, until, row[0]),
        )
        record = replace(record, input_data=json.loads(row[7]), lease_until=until)
        record._claim = ScheduleClaim(record.schedule_id, occurrence, owner, generation, until)
        self._failpoint("claim-before-commit")
        return record

    def bind_invocation(
        self,
        record: ScheduleRecord,
        *,
        engine: str,
        now: float | None = None,
    ) -> ExecutionHandle:
        claim = record._claim
        if claim is None:
            raise ScheduleOperationConflict("schedule occurrence has not been claimed")
        current = time.time() if now is None else now
        with self.transactions.transaction() as connection:
            saved = self._require(connection, record.schedule_id)
            if saved.status != "active":
                raise ScheduleOperationConflict("schedule is no longer active")
            row = slot_row(connection, claim.schedule_id, claim.occurrence_key)
            self._fence(row, claim, current)
            return self._bind(connection, saved, row, engine)

    def _bind(
        self,
        connection: StorageConnection,
        record: ScheduleRecord,
        row: Any,
        engine: str,
    ) -> ExecutionHandle:
        if row[6] is not None:
            handle = self._binding(connection, row, record)
            if handle.engine != engine:
                raise ScheduleOperationConflict("schedule belongs to a different engine")
            self._bind_authority(connection, record, row, handle)
            return handle
        handle = self.invocations.create_in_transaction(
            connection,
            engine=engine,
            session_id=None,
            user_id=None,
            workflow_name=record.workflow_name,
            workflow_version=record.workflow_version,
            workflow_fingerprint=record.workflow_fingerprint,
            owner_principal=record.owner_principal,
            input_data=json.loads(row[7]),
        )
        self._failpoint("invocation-inserted-before-binding")
        immutable = {
            key: getattr(handle, key)
            for key in (
                "invocation_id",
                "engine",
                "engine_execution_reference",
                "session_id",
                "workflow_name",
                "workflow_version",
                "workflow_fingerprint",
                "owner_principal",
            )
        }
        connection.execute(
            "INSERT INTO schedule_bindings(slot_key,payload) VALUES (?, ?)",
            (row[0], _json(immutable)),
        )
        connection.execute(
            "UPDATE schedule_slots SET invocation_id=?,state='running' WHERE slot_key=?",
            (handle.invocation_id, row[0]),
        )
        record.last_invocation_id = handle.invocation_id
        self._save(connection, record)
        self._failpoint("binding-before-commit")
        self._bind_authority(connection, record, row, handle)
        return handle

    def _bind_authority(
        self,
        connection: StorageConnection,
        record: ScheduleRecord,
        row: Any,
        handle: ExecutionHandle,
    ) -> None:
        if self.invocation_authority_participant is None or not row[3] or row[5] is None:
            return
        occurrence = connection.execute(
            "SELECT occurrence_key FROM schedule_slots WHERE slot_key=?", (row[0],)
        ).fetchone()
        if occurrence is None:
            raise ScheduleValidationError("schedule authority slot is unavailable")
        claim = ScheduleClaim(record.schedule_id, occurrence[0], row[3], row[4], row[5])
        self.invocation_authority_participant(connection, record, handle, claim)

    def bind_trigger_in_transaction(
        self,
        connection: StorageConnection,
        schedule_id: str,
        cycle_key: str,
        input_data: Any,
        *,
        engine: str,
        workflow_fingerprint: str | None = None,
        event_scope: str | None = None,
    ) -> ExecutionHandle:
        ScheduleTransactions.serialize(connection)
        record = self._require(connection, schedule_id)
        if (
            record.status != "active"
            or record.kind != "on"
            or not cycle_key
            or (
                workflow_fingerprint is not None
                and record.workflow_fingerprint != workflow_fingerprint
            )
            or (event_scope is not None and record.event_scope != event_scope)
        ):
            raise ScheduleOperationConflict("event trigger schedule identity disagrees")
        occurrence = "trigger:" + cycle_key
        row = slot_row(connection, schedule_id, occurrence)
        encoded = _json(input_data)
        if row is not None and row[6] is not None:
            if row[7] != encoded:
                raise ScheduleOperationConflict("event trigger input changed")
            existing = self._binding(connection, row, record)
            if existing.engine != engine:
                raise ScheduleOperationConflict("event trigger belongs to a different engine")
            return existing
        if row is None:
            connection.execute(
                "INSERT INTO schedule_slots(slot_key,schedule_id,occurrence_key,nominal_at,state,"
                "owner,generation,lease_until,invocation_id,input_payload) "
                "VALUES (?, ?, ?, ?, 'pending', '', 0, NULL, NULL, ?)",
                (
                    self._slot_key(schedule_id, occurrence),
                    schedule_id,
                    occurrence,
                    time.time(),
                    encoded,
                ),
            )
            row = slot_row(connection, schedule_id, occurrence)
        elif row[7] != encoded:
            raise ScheduleOperationConflict("event trigger input changed")
        handle = self._bind(connection, record, row, engine)
        connection.execute("UPDATE schedule_slots SET state='pending' WHERE slot_key=?", (row[0],))
        return handle

    def set_invocation(
        self,
        schedule_id: str,
        invocation_id: str,
        *,
        claim: ScheduleClaim | None = None,
        now: float | None = None,
    ) -> ScheduleRecord:
        """Compatibility entry point verifies an already atomic slot binding."""
        if claim is None or claim.schedule_id != schedule_id:
            raise ScheduleOperationConflict("invocation binding requires its occurrence claim")
        with self.transactions.transaction() as connection:
            record = self._require(connection, schedule_id)
            row = slot_row(connection, schedule_id, claim.occurrence_key)
            self._fence(row, claim, time.time() if now is None else now)
            if self._binding(connection, row, record).invocation_id != invocation_id:
                raise ScheduleOperationConflict("schedule invocation binding disagrees")
            return record

    def verify_trigger_binding_in_transaction(
        self,
        connection: StorageConnection,
        schedule_id: str,
        cycle_key: str,
        *,
        engine: str,
        invocation_id: str,
        input_data: Any,
        event_scope: str | None = None,
    ) -> ExecutionHandle:
        """Verify an existing trigger binding without repairing missing evidence."""
        record = self._require(connection, schedule_id)
        row = slot_row(connection, schedule_id, "trigger:" + cycle_key)
        if (
            record.kind != "on"
            or row is None
            or row[6] != invocation_id
            or row[7] != _json(input_data)
            or (event_scope is not None and record.event_scope != event_scope)
        ):
            raise ScheduleValidationError("event trigger binding evidence disagrees")
        handle = self._binding(connection, row, record)
        if handle.engine != engine:
            raise ScheduleValidationError("event trigger engine binding disagrees")
        return handle

    def renew(
        self,
        claim: ScheduleClaim,
        *,
        now: float | None = None,
        lease_seconds: float = 30.0,
    ) -> ScheduleClaim:
        current = time.time() if now is None else now
        if not math.isfinite(lease_seconds) or lease_seconds <= 0:
            raise ScheduleValidationError("schedule lease is invalid")
        with self.transactions.transaction() as connection:
            self._fence(
                slot_row(connection, claim.schedule_id, claim.occurrence_key), claim, current
            )
            until = current + lease_seconds
            connection.execute(
                "UPDATE schedule_slots SET lease_until=? WHERE schedule_id=? AND occurrence_key=?",
                (until, claim.schedule_id, claim.occurrence_key),
            )
            return replace(claim, lease_until=until)

    def mark_waiting(self, claim: ScheduleClaim, *, now: float | None = None) -> None:
        with self.transactions.transaction() as connection:
            self._fence(
                slot_row(connection, claim.schedule_id, claim.occurrence_key),
                claim,
                time.time() if now is None else now,
            )
            connection.execute(
                "UPDATE schedule_slots SET state='waiting',lease_until=NULL WHERE schedule_id=? "
                "AND occurrence_key=?",
                (claim.schedule_id, claim.occurrence_key),
            )

    def finish(
        self,
        schedule_id: str,
        *,
        invocation_id: str,
        status: str,
        now: float | None = None,
        claim: ScheduleClaim | None = None,
    ) -> ScheduleRecord:
        if status not in _TERMINAL:
            raise ScheduleValidationError("unsupported scheduled invocation status")
        if claim is None or claim.schedule_id != schedule_id:
            raise ScheduleOperationConflict("schedule completion requires its occurrence claim")
        current = time.time() if now is None else now
        with self.transactions.transaction() as connection:
            record = self._require(connection, schedule_id)
            row = slot_row(connection, schedule_id, claim.occurrence_key)
            self._fence(row, claim, current)
            handle = self._binding(connection, row, record)
            if handle.invocation_id != invocation_id or handle.status != status:
                raise ScheduleOperationConflict("schedule completion disagrees with invocation")
            connection.execute(
                "UPDATE schedule_slots SET state=?,lease_until=NULL WHERE slot_key=?",
                (status, row[0]),
            )
            record.last_invocation_id, record.last_status = invocation_id, status
            if record.status == "active":
                if status == "completed" and record.kind == "after":
                    profile = parse_schedule(json.loads(record.workflow_source_json)["schedule"])
                    completed_at = self.invocations.terminal_completed_at(handle)
                    record.next_run_at = next_due(profile, completed_at, record.timezone)
                elif status != "completed" and record.kind == "after":
                    record.status = status
            self._save(connection, record)
            return record

    def cancel(self, schedule_id: str) -> ScheduleRecord:
        with self.transactions.transaction() as connection:
            record = self._require(connection, schedule_id)
            if record.status == "active":
                record.status, record.lease_until = "cancelled", None
                self._save(connection, record)
                connection.execute(
                    "UPDATE schedule_slots SET state='cancelled',generation=generation+1,"
                    "lease_until=NULL WHERE schedule_id=? AND state NOT IN ('completed','faulted')",
                    (schedule_id,),
                )
            return record

    def active_invocations(self, schedule_id: str) -> tuple[str, ...]:
        return tuple(
            row[0]
            for row in self.connection.execute(
                "SELECT invocation_id FROM schedule_slots WHERE schedule_id=? "
                "AND invocation_id IS NOT NULL"
                " AND state NOT IN ('completed','faulted')",
                (schedule_id,),
            ).fetchall()
        )

    def close(self) -> None:
        if self._owns_invocations:
            self.invocations.close()
