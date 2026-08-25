from __future__ import annotations

import pytest
from open_workflow_agent.errors import ScheduleValidationError, UnsupportedWorkflowFeature
from open_workflow_agent.scheduling import ScheduleStore, schedule_period
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
