from __future__ import annotations

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.engine import PortableWorkflowEngine
from open_workflow_agent.errors import (
    ScheduleOperationConflict,
    ScheduleValidationError,
    UnsupportedWorkflowFeature,
)
from open_workflow_agent.scheduling import ScheduleStore, WorkflowScheduler, schedule_period
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow


def _workflow(schedule):
    return {
        "document": {
            "dsl": "1.0.3",
            "namespace": "schedule-tests",
            "name": "scheduled-workflow",
            "version": "1.0.0",
        },
        "schedule": schedule,
        "do": [{"finish": {"set": {"done": True}}}],
    }


def test_schedule_store_survives_restart_and_claims_due_work(tmp_path):
    plan = compile_workflow(_workflow({"after": {"milliseconds": 10}}))
    database = tmp_path / "runtime.sqlite3"
    first = ScheduleStore(database)
    created = first.create(plan, {"value": 1}, operation_key="schedule-create-1", now=100.0)
    duplicate = first.create(plan, {"value": 2}, operation_key="schedule-create-1", now=200.0)
    assert duplicate.schedule_id == created.schedule_id
    assert duplicate.input_data == {"value": 1}
    first.close()

    reopened = ScheduleStore(database)
    persisted = reopened.get(created.schedule_id)
    assert persisted is not None
    assert persisted.status == "active"
    claimed = reopened.claim_due(now=101.0)
    assert claimed is not None
    assert claimed.schedule_id == created.schedule_id
    reopened.close()


def test_schedule_profile_rejects_cron_and_requires_one_supported_mode():
    with pytest.raises(UnsupportedWorkflowFeature):
        compile_workflow(_workflow({"cron": "* * * * *"}))
    with pytest.raises(ScheduleValidationError):
        schedule_period({"after": 1, "every": 1})


def test_schedule_store_lifecycle_and_rejection_paths(tmp_path):
    plan = compile_workflow(_workflow({"every": {"seconds": 2}}))
    other_plan = compile_workflow(
        {
            **_workflow({"after": {"seconds": 1}}),
            "document": {
                "dsl": "1.0.3",
                "namespace": "schedule-tests",
                "name": "other-workflow",
                "version": "1.0.0",
            },
        }
    )
    store = ScheduleStore(tmp_path / "schedules.sqlite3")
    try:
        record = store.create(plan, {"value": 1}, operation_key="key", now=10)
        assert record.kind == "every"
        assert record.interval_seconds == 2
        assert record.next_run_at == 12
        assert record.as_dict()["status"] == "active"
        assert store.get("missing") is None
        assert store.claim_due(now=11) is None
        claimed = store.claim_due(now=12, lease_seconds=1)
        assert claimed is not None
        assert claimed.lease_until == 13
        store.set_invocation(record.schedule_id, "inv-1")
        finished = store.finish(
            record.schedule_id, invocation_id="inv-1", status="completed", now=20
        )
        assert finished.status == "active"
        assert finished.next_run_at == 22
        assert finished.last_status == "completed"
        assert store.status_counts() == {"active": 1}
        assert store.cancel(record.schedule_id).status == "cancelled"
        assert store.cancel(record.schedule_id).status == "cancelled"
        assert (
            store.finish(record.schedule_id, invocation_id="inv-2", status="faulted").status
            == "cancelled"
        )
        assert store.status_counts() == {"cancelled": 1}

        with pytest.raises(ScheduleValidationError, match="unsupported"):
            store.finish(record.schedule_id, invocation_id="inv-3", status="unknown")
        with pytest.raises(KeyError):
            store.set_invocation("missing", "inv")
        with pytest.raises(ScheduleOperationConflict, match="idempotency"):
            store.create(other_plan, {}, operation_key="key", now=1)
        with pytest.raises(ScheduleValidationError, match="JSON serializable"):
            store.create(plan, {"bad": object()}, now=1)
    finally:
        store.close()


@pytest.mark.asyncio
async def test_workflow_scheduler_executes_and_stops_cleanly(tmp_path):
    services = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
    engine = PortableWorkflowEngine()
    await engine.initialize(services)
    plan = compile_workflow(_workflow({"after": {"seconds": 1}}))
    record = services.schedules.create(plan, {"value": 1}, now=0)
    scheduler = WorkflowScheduler(services, engine, poll_seconds=0.001)
    await scheduler._execute(record)
    assert services.schedules.get(record.schedule_id).status == "completed"
    await scheduler.start()
    await scheduler.start()
    await scheduler.stop()
    await scheduler.stop()
    await scheduler.cancel(record.schedule_id, operation_id="cancel-after-complete")
    services.close()
