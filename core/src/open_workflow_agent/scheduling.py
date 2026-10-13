"""Bounded embedded schedule dispatch over fenced durable occurrence slots."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ._schedule_storage import ScheduleClaim, slot_row
from ._schedule_store import SCHEDULE_STATES, ScheduleRecord, ScheduleStore
from .errors import ScheduleOperationConflict, ScheduleValidationError
from .persistence import ExecutionHandle
from .sandbox import resolve_and_compile_sandbox_workflow
from .schedule_profile import parse_schedule

if TYPE_CHECKING:
    from ._event_triggers import EventTriggerStore

__all__ = [
    "SCHEDULE_STATES",
    "ScheduleClaim",
    "ScheduleRecord",
    "ScheduleStore",
    "WorkflowScheduler",
    "schedule_period",
]


class WorkflowScheduler:
    """Single-runtime scheduler; rows survive restart and leases allow reclaim."""

    def __init__(
        self,
        services: Any,
        engine: Any,
        *,
        poll_seconds: float | None = None,
        max_concurrency: int | None = None,
        clock: Callable[[], float] = time.time,
        batch_size: int | None = None,
    ) -> None:
        self.services = services
        self.engine = engine
        timing = getattr(getattr(services, "config", None), "execution_timing", None)
        self.poll_seconds = (
            poll_seconds
            if poll_seconds is not None
            else getattr(timing, "poll_interval_seconds", 0.05)
        )
        self._task: asyncio.Task[None] | None = None
        self._active: dict[str, ExecutionHandle] = {}
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._owner = uuid.uuid4().hex
        self.max_concurrency = (
            max_concurrency
            if max_concurrency is not None
            else getattr(timing, "max_concurrency", 16)
        )
        self.clock = clock
        self.batch_size = (
            batch_size if batch_size is not None else getattr(timing, "schedule_batch_size", 128)
        )
        self._errors: dict[str, Exception] = {}
        self._trigger_cursor: str | None = None
        self._definition_cursor: tuple[float, str] | None = None
        self._failed_schedules: set[str] = set()

    def _trigger_store(self) -> EventTriggerStore | None:
        factory = getattr(self.services, "event_triggers", None)
        if not callable(factory):
            return None
        result: EventTriggerStore | None = factory(self.engine.engine_name, clock=self.clock)
        return result

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        workers = tuple(self._workers.values())
        for task in workers:
            task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        self._workers.clear()

    async def cancel(self, schedule_id: str, *, operation_id: str | None = None) -> ScheduleRecord:
        record: ScheduleRecord = self.services.schedules.cancel(schedule_id)
        triggers = self._trigger_store()
        if record.kind == "on" and triggers is not None:
            triggers.close(schedule_id)
        for invocation_id in self.services.schedules.active_invocations(schedule_id):
            handle = self.services.invocations.get(invocation_id)
            if handle is not None and handle.status not in {"completed", "faulted", "cancelled"}:
                await self.engine.cancel(handle, operation_id=operation_id)
        return record

    async def _run(self) -> None:
        while True:
            await self.tick()
            await asyncio.sleep(self.poll_seconds)

    async def tick(self, *, now: float | None = None) -> int:
        """Admit a bounded fair batch using the same clock as claim/disposition."""
        current = self.clock() if now is None else now
        definition_snapshots = await self._definition_preflight(current)

        def eligible(handle: ExecutionHandle) -> bool:
            if definition_snapshots is None:
                return True
            if definition_snapshots.get(handle.invocation_id) != handle._persisted_payload:
                return False
            archive = self.services.recovery_definitions
            if handle.status not in {"completed", "faulted", "cancelled"} and archive.has_binding(
                handle
            ):
                try:
                    archive.resolve(handle)
                except Exception as error:
                    self._errors["definition:" + handle.invocation_id] = error
                    return False
            return True

        triggers = self._trigger_store()
        if triggers is not None:
            records = self.services.schedules.active_triggers(
                limit=self.batch_size,
                after=self._trigger_cursor,
            )
            self._trigger_cursor = self.services.schedules.trigger_scan_cursor
            for schedule_id, error in self.services.schedules.trigger_scan_errors:
                self._errors["trigger:" + schedule_id] = error
                self._failed_schedules.add(schedule_id)
            for record in records:
                try:
                    triggers.poll(record.schedule_id)
                    self._failed_schedules.discard(record.schedule_id)
                    self._errors.pop("trigger:" + record.schedule_id, None)
                except Exception as exc:
                    self._errors["trigger:" + record.schedule_id] = exc
                    from ._event_triggers import EventTriggerBackpressure

                    if isinstance(exc, EventTriggerBackpressure):
                        self._failed_schedules.discard(record.schedule_id)
                    else:
                        self._failed_schedules.add(record.schedule_id)
        selected: set[str] = set()
        count = 0
        for _ in range(self.batch_size):
            if len(self._workers) >= self.max_concurrency:
                break
            record = self.services.schedules.claim_due(
                now=current,
                lease_seconds=30.0,
                owner=self._owner,
                exclude_schedule_ids=tuple(selected | self._failed_schedules),
                eligible_invocation=eligible,
            )
            if record is None:
                break
            claim = record._claim
            if claim is None:
                raise ScheduleOperationConflict("scheduler received an unclaimed occurrence")
            key = record.schedule_id + ":" + claim.occurrence_key

            async def execute(value: ScheduleRecord, worker_key: str) -> None:
                try:
                    await self._execute(value)
                except Exception as exc:
                    self._errors[worker_key] = exc
                finally:
                    self._workers.pop(worker_key, None)

            selected.add(record.schedule_id)
            count += 1
            self._workers[key] = asyncio.create_task(execute(record, key))
            await asyncio.sleep(0)
        return count

    async def _definition_preflight(self, now: float) -> dict[str, str | None] | None:
        archive = getattr(self.services, "recovery_definitions", None)
        if archive is None:
            # Independent common-store primitives may use a non-runtime fake.
            return None
        store = self.services.schedules
        statement = (
            "SELECT schedule_id,occurrence_key,nominal_at,slot_key FROM schedule_slots "
            "WHERE invocation_id IS NOT NULL AND state IN ('pending','running','waiting') "
            "AND (lease_until IS NULL OR lease_until<=?) "
            "AND (state!='waiting' OR NOT EXISTS (SELECT 1 FROM invocations "
            "WHERE invocations.invocation_id=schedule_slots.invocation_id "
            "AND invocations.status NOT IN ('completed','faulted','cancelled'))) "
        )
        parameters: tuple[Any, ...] = (now,)
        if self._definition_cursor is not None:
            nominal, key = self._definition_cursor
            statement += "AND (nominal_at>? OR (nominal_at=? AND slot_key>?)) "
            parameters += (nominal, nominal, key)
        rows = store.connection.execute(
            statement + "ORDER BY nominal_at,slot_key LIMIT 128", parameters
        ).fetchall()
        if not rows and self._definition_cursor is not None:
            self._definition_cursor = None
            return await self._definition_preflight(now)
        if rows:
            self._definition_cursor = (rows[-1][2], rows[-1][3])
        verified: dict[str, str | None] = {}
        for schedule_id, occurrence, _nominal, _key in rows:
            try:
                record = store._require(store.connection, schedule_id)
                row = slot_row(store.connection, schedule_id, occurrence)
                handle = store._binding(store.connection, row, record)
                if handle.status not in {"completed", "faulted", "cancelled"}:
                    if archive.has_binding(handle):
                        archive.resolve(handle)
                    else:
                        await self.engine._verify_initial_nohistory(handle)
                verified[handle.invocation_id] = handle._persisted_payload
                self._failed_schedules.discard(schedule_id)
                self._errors.pop("definition:" + schedule_id, None)
            except Exception as error:
                self._failed_schedules.add(schedule_id)
                self._errors["definition:" + schedule_id] = error
        return verified

    async def _execute(self, record: ScheduleRecord) -> None:
        claim = record._claim
        if claim is None:
            raise ScheduleOperationConflict("schedule occurrence has not been claimed")
        row = slot_row(self.services.schedules.connection, record.schedule_id, claim.occurrence_key)
        triggers = self._trigger_store()
        if record.kind == "on" and triggers is not None:
            if row is None or row[6] is None:
                raise ScheduleValidationError("event trigger invocation binding is missing")
            triggers.verify_invocation(row[6])
        archived_handle = (
            self.services.schedules._binding(self.services.schedules.connection, row, record)
            if row is not None and row[6] is not None
            else None
        )
        archive = getattr(self.services, "recovery_definitions", None)
        terminal = archived_handle is not None and archived_handle.status in {
            "completed",
            "faulted",
            "cancelled",
        }
        if terminal:
            assert archived_handle is not None
            self.services.schedules.finish(
                record.schedule_id,
                invocation_id=archived_handle.invocation_id,
                status=archived_handle.status,
                claim=claim,
                now=self.clock(),
            )
            return
        if (
            archived_handle is not None
            and archive is not None
            and archive.has_binding(archived_handle)
        ):
            plan = self.services.resolve_original_definition(archived_handle)
        else:
            if archived_handle is not None and archive is not None:
                await self.engine._verify_initial_nohistory(archived_handle)
            plan = await resolve_and_compile_sandbox_workflow(
                json.loads(record.workflow_source_json),
                sandbox=self.services.config.sandbox,
                trusted_catalogs=self.services.config.workflow.external_catalogs,
                resolver=self.services.external_catalogs,
                catalog=self.services.catalog,
            )
        if plan.fingerprint != record.workflow_fingerprint:
            raise ScheduleValidationError("scheduled workflow definition changed")
        handle = self.services.schedules.bind_invocation(
            record, engine=self.engine.engine_name, now=self.clock()
        )
        self._active[handle.invocation_id] = handle

        async def renew() -> None:
            nonlocal claim
            while True:
                await asyncio.sleep(10)
                claim = self.services.schedules.renew(claim, now=self.clock(), lease_seconds=30)

        heartbeat = asyncio.create_task(renew())
        try:
            result = await self.engine.dispatch_scheduled(handle, plan, record.input_data)
            if result.status == "waiting":
                self.services.schedules.mark_waiting(claim, now=self.clock())
                return
            self.services.schedules.finish(
                record.schedule_id,
                invocation_id=result.invocation_id,
                status=result.status,
                claim=claim,
                now=self.clock(),
            )
        except asyncio.CancelledError:
            raise
        except ScheduleOperationConflict:
            # Another generation/terminal cancel owns disposition now.
            return
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            self._active.pop(handle.invocation_id, None)


def schedule_period(schedule: Any) -> tuple[str, float]:
    profile = parse_schedule(schedule)
    if profile.interval_seconds is None:
        raise ScheduleValidationError("calendar and event schedules have no fixed period")
    return profile.kind, profile.interval_seconds
