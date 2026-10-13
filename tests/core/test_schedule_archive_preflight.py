"""Version rejection leaves occurrence ownership untouched and permits ready work."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.engine import PortableWorkflowEngine
from open_workflow_agent.scheduling import WorkflowScheduler
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.timer_dispatcher import TimerDispatcher
from open_workflow_agent.workflow import compile_workflow


def _plan() -> Any:
    return compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "recovery",
                "name": "schedule-" + uuid4().hex,
                "version": "1.0.0",
            },
            "schedule": {"every": {"seconds": 1}},
            "do": [{"done": {"set": {"original": True}}}],
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("damage", ["binding", "definition"])
async def test_damaged_archived_schedule_is_not_claimed_and_later_ready_work_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str, damage: str
) -> None:
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")

    async def explicit(_dispatcher: TimerDispatcher) -> None:
        return None

    monkeypatch.setattr(TimerDispatcher, "start", explicit)
    services = RuntimeServices(
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}}),
        model=FakeModel(),
        database_root=None if datasource else tmp_path,
    )
    engine = PortableWorkflowEngine()
    await engine.initialize(services)
    bad_plan, good_plan = _plan(), _plan()
    handles = []
    records = []
    scheduler = None
    try:
        for plan in (bad_plan, good_plan):
            record = services.schedules.create(plan, {"original": True}, now=1000.0)
            records.append(record)
            claimed = services.schedules.claim_due(
                now=1001.0, schedule_id=record.schedule_id, lease_seconds=1
            )
            assert claimed is not None
            handle = services.schedules.bind_invocation(claimed, engine="base", now=1001.0)
            lease = services.execution_ownership.acquire(handle, seconds=60)
            try:
                services.recovery_definitions.admit(
                    plan, handle, {"original": True}, execution_lease=lease
                )
            finally:
                services.execution_ownership.release(lease)
            handles.append(handle)
        archive = services.recovery_definitions
        if damage == "binding":
            archive.connection.execute(
                "DELETE FROM recovery_invocation_definitions WHERE invocation_id=?",
                (handles[0].invocation_id,),
            )
        else:
            archive.connection.execute(
                "DELETE FROM recovery_definitions WHERE fingerprint=?", (bad_plan.fingerprint,)
            )
        archive.connection.commit()
        # A missing binding with an admitted policy record is history, not a
        # fresh queued invocation that current source may reinterpret.
        before = services.schedules.connection.execute(
            "SELECT * FROM schedule_slots WHERE schedule_id=?", (records[0].schedule_id,)
        ).fetchall()
        parent_before = services.invocations.get(handles[0].invocation_id)._persisted_payload
        scheduler = WorkflowScheduler(services, engine, clock=lambda: 1002.0, batch_size=1)
        assert await scheduler.tick(now=1002.0) == 1
        if scheduler._workers:
            await asyncio.gather(*tuple(scheduler._workers.values()))
        assert (
            services.schedules.connection.execute(
                "SELECT * FROM schedule_slots WHERE schedule_id=?", (records[0].schedule_id,)
            ).fetchall()
            == before
        )
        assert (
            services.invocations.get(handles[0].invocation_id)._persisted_payload == parent_before
        )
        good = services.invocations.get(handles[1].invocation_id)
        assert good is not None and good.status == "completed"
        assert good.output == {"original": True}
        assert scheduler._errors and not services.model.calls
    finally:
        try:
            if scheduler is not None:
                await scheduler.stop()
            await engine.shutdown()
            # The rejected occurrence stays unchanged through every assertion.
            # Retire only our schedules afterwards so later shared-PG polls
            # cannot execute this fixture's still-due recurring work.
            for record in records:
                services.schedules.cancel(record.schedule_id)
        finally:
            services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_archive_binding_removed_between_preflight_and_claim_keeps_delivery_alive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")

    async def explicit(_dispatcher: TimerDispatcher) -> None:
        return None

    monkeypatch.setattr(TimerDispatcher, "start", explicit)
    services = RuntimeServices(
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}}),
        model=FakeModel(),
        database_root=None if datasource else tmp_path,
    )
    engine = PortableWorkflowEngine()
    await engine.initialize(services)
    handles = []
    records = []
    scheduler = WorkflowScheduler(services, engine, clock=lambda: 1002.0, batch_size=1)
    try:
        for index in range(2):
            plan = _plan()
            admitted_at = 1001.0 + index * 0.1
            record = services.schedules.create(plan, {"original": True}, now=admitted_at - 1)
            records.append(record)
            claimed = services.schedules.claim_due(
                now=admitted_at, schedule_id=record.schedule_id, lease_seconds=0.5
            )
            assert claimed is not None
            handle = services.schedules.bind_invocation(claimed, engine="base", now=admitted_at)
            lease = services.execution_ownership.acquire(handle, seconds=60)
            try:
                services.recovery_definitions.admit(
                    plan, handle, {"original": True}, execution_lease=lease
                )
            finally:
                services.execution_ownership.release(lease)
            handles.append(handle)
        bad, good = handles
        before = services.schedules.connection.execute(
            "SELECT * FROM schedule_slots WHERE schedule_id=?", (records[0].schedule_id,)
        ).fetchall()
        parent_before = services.invocations.get(bad.invocation_id)._persisted_payload
        original_preflight = scheduler._definition_preflight
        raced = False

        async def remove_after_preflight(now: float) -> dict[str, str | None] | None:
            nonlocal raced
            snapshots = await original_preflight(now)
            if not raced:
                assert snapshots is not None and bad.invocation_id in snapshots
                services.recovery_definitions.connection.execute(
                    "DELETE FROM recovery_invocation_definitions WHERE invocation_id=?",
                    (bad.invocation_id,),
                )
                services.recovery_definitions.connection.commit()
                raced = True
            return snapshots

        monkeypatch.setattr(scheduler, "_definition_preflight", remove_after_preflight)
        assert await scheduler.tick(now=1002.0) == 1
        if scheduler._workers:
            await asyncio.wait_for(asyncio.gather(*tuple(scheduler._workers.values())), timeout=10)
        assert raced and "definition:" + bad.invocation_id in scheduler._errors
        assert (
            services.schedules.connection.execute(
                "SELECT * FROM schedule_slots WHERE schedule_id=?", (records[0].schedule_id,)
            ).fetchall()
            == before
        )
        assert services.invocations.get(bad.invocation_id)._persisted_payload == parent_before
        completed = services.invocations.get(good.invocation_id)
        assert completed is not None and completed.status == "completed"
        assert completed.output == {"original": True}
        # Another poll remains usable after the rejected occurrence; the earlier
        # uncaught has_binding error would have terminated the poll loop.
        assert await scheduler.tick(now=1002.0) == 0
        assert not services.model.calls
    finally:
        try:
            await scheduler.stop()
            await engine.shutdown()
            for record in records:
                services.schedules.cancel(record.schedule_id)
        finally:
            services.close()
