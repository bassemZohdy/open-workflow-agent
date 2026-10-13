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
    try:
        for plan in (bad_plan, good_plan):
            record = services.schedules.create(plan, {"original": True}, now=1000.0)
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
            records.append(record)
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
        await scheduler.stop()
    finally:
        await engine.shutdown()
        services.close()
