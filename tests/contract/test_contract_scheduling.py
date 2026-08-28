from __future__ import annotations

import asyncio

import pytest
from engine_cases import engine_cases
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.scheduling import WorkflowScheduler
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow

ENGINE_CASES = engine_cases()


def _workflow():
    return {
        "document": {
            "dsl": "1.0.3",
            "namespace": "schedule-contracts",
            "name": "scheduled-workflow",
            "version": "1.0.0",
        },
        "schedule": {"after": {"milliseconds": 1}},
        "do": [{"finish": {"set": {"done": True}}}],
    }


async def _wait_for(predicate):
    for _ in range(200):
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("scheduled invocation did not complete")


@pytest.mark.asyncio
async def test_scheduled_invocation_has_equivalent_results_on_both_engines(tmp_path):
    signatures = []
    for engine_name, engine_type in ENGINE_CASES:
        services = RuntimeServices(
            RuntimeConfig(), model=FakeModel(), database_root=tmp_path / engine_name
        )
        engine = engine_type()
        await engine.initialize(services)
        scheduler = WorkflowScheduler(services, engine, poll_seconds=0.001)
        await scheduler.start()
        plan = compile_workflow(_workflow())
        schedule = services.schedules.create(plan, {"source": engine_name})

        def completed(
            store=services.schedules,
            schedule_id=schedule.schedule_id,
            fallback=schedule,
        ):
            return (store.get(schedule_id) or fallback).status == "completed"

        await _wait_for(completed)
        completed = services.schedules.get(schedule.schedule_id)
        assert completed is not None
        assert completed.last_invocation_id is not None
        invocation = services.invocations.get(completed.last_invocation_id)
        assert invocation is not None
        signatures.append(
            (completed.status, completed.last_status, invocation.status, invocation.output)
        )
        await scheduler.stop()
        services.close()

    assert signatures[0] == signatures[1] == ("completed", "completed", "completed", {"done": True})
