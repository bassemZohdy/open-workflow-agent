"""Real scheduled invocation ownership and portable relative/calendar cadence."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from engine_cases import engine_cases
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.scheduling import WorkflowScheduler
from open_workflow_agent.workflow import compile_workflow
from test_durable_timers import _services, _workflow


def _plan(schedule: dict[str, Any], name: str) -> Any:
    source = _workflow(
        [{"model": {"call": "llm:1.0.0@default", "with": {"prompt": "${ . }"}}}], name
    )
    source["schedule"] = schedule
    return compile_workflow(source)


def _claim(services: Any, schedule_id: str, now: float, owner: str) -> Any:
    record = services.schedules.claim_due(
        now=now, owner=owner, lease_seconds=30, schedule_id=schedule_id
    )
    assert record is not None and record._claim is not None
    return record


async def _calls(model: FakeModel, count: int) -> None:
    async with asyncio.timeout(30):
        while len(model.calls) < count:
            await asyncio.sleep(0.01)


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_every_retains_nominal_cadence_and_overlaps_running_invocations(
    tmp_path: Path, engine_name: str, engine_type: Any, backend: str
) -> None:
    release = asyncio.Event()

    async def response(prompt: Any) -> Any:
        await release.wait()
        return prompt

    model = FakeModel(response)
    services = _services(tmp_path, backend, model)
    engine = engine_type()
    now = 1010.0
    scheduler = WorkflowScheduler(services, engine, clock=lambda: now)
    tasks: list[asyncio.Task[None]] = []
    plan = _plan({"every": {"seconds": 10}}, "overlapping-every")
    original = {"original": "must-survive"}
    schedule = services.schedules.create(
        plan, original, now=1000, owner_principal="cadence-owner", event_scope="cadence-owner"
    )
    try:
        await engine.initialize(services)
        first = _claim(services, schedule.schedule_id, now, "first-owner")
        tasks.append(asyncio.create_task(scheduler._execute(first)))
        await _calls(model, 1)
        first_ids = services.schedules.active_invocations(schedule.schedule_id)
        assert len(first_ids) == 1
        first_handle = services.invocations.get(first_ids[0])
        assert first_handle is not None and first_handle.status == "running"
        now = 1020.0
        second = _claim(services, schedule.schedule_id, now, "second-owner")
        assert first._claim.occurrence_key != second._claim.occurrence_key
        tasks.append(asyncio.create_task(scheduler._execute(second)))
        await _calls(model, 2)
        active_ids = services.schedules.active_invocations(schedule.schedule_id)
        assert len(active_ids) == 2 and len(set(active_ids)) == 2
        for invocation_id in active_ids:
            handle = services.invocations.get(invocation_id)
            assert handle is not None and handle.status == "running"
            assert handle.workflow_fingerprint == plan.fingerprint
            assert handle.owner_principal == "cadence-owner"
            assert services.invocations.read_input(handle) == original
        persisted = services.schedules.get(schedule.schedule_id)
        assert persisted is not None and persisted.next_run_at == 1030
        release.set()
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=30)
        assert model.calls == [{"prompt": original}, {"prompt": original}]
        for value in active_ids:
            completed = services.invocations.get(value)
            assert completed is not None and completed.status == "completed"
        persisted = services.schedules.get(schedule.schedule_id)
        assert persisted is not None and persisted.next_run_at == 1030
    finally:
        release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        services.schedules.cancel(schedule.schedule_id)
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("outcome", ["success", "fault", "cancel"])
async def test_after_repeats_from_saved_successful_completion_and_stops_after_fault_or_cancel(
    tmp_path: Path, engine_name: str, engine_type: Any, backend: str, outcome: str
) -> None:
    async def blocked(prompt: Any) -> Any:
        await asyncio.Event().wait()
        return prompt

    model = FakeModel(
        blocked if outcome == "cancel" else lambda prompt: prompt,
        failures=int(outcome == "fault"),
    )
    services = _services(tmp_path, backend, model)
    engine = engine_type()
    now = 1010.0
    scheduler = WorkflowScheduler(services, engine, clock=lambda: now)
    plan = _plan({"after": {"seconds": 10}}, "repeated-after")
    original = {"source": "unchanged"}
    schedule = services.schedules.create(plan, original, now=1000)
    try:
        await engine.initialize(services)
        assert schedule.next_run_at == 1010
        selected = _claim(services, schedule.schedule_id, now, "first-owner")
        if outcome == "cancel":
            executing = asyncio.create_task(scheduler._execute(selected))
            try:
                await _calls(model, 1)
                running = services.schedules.get(schedule.schedule_id)
                assert running is not None and running.last_invocation_id is not None
                running_handle = services.invocations.get(running.last_invocation_id)
                assert running_handle is not None
                await engine.cancel(running_handle, operation_id="cancel-after-predecessor")
                await asyncio.wait_for(executing, timeout=30)
            finally:
                if not executing.done():
                    executing.cancel()
                await asyncio.gather(executing, return_exceptions=True)
        else:
            await scheduler._execute(selected)
        saved = services.schedules.get(schedule.schedule_id)
        assert saved is not None and saved.last_invocation_id is not None
        first = services.invocations.get(saved.last_invocation_id)
        assert first is not None
        assert services.invocations.read_input(first) == original
        if outcome != "success":
            expected = "faulted" if outcome == "fault" else "cancelled"
            assert first.status == saved.status == saved.last_status == expected
            assert (
                services.schedules.claim_due(now=now + 1000, schedule_id=schedule.schedule_id)
                is None
            )
            assert len(model.calls) == 1
        else:
            assert first.status == saved.last_status == "completed" and saved.status == "active"
            completion = services.invocations.terminal_completed_at(first)
            assert saved.next_run_at == completion + 10
            assert (
                services.schedules.claim_due(now=completion + 9, schedule_id=schedule.schedule_id)
                is None
            )
            now = completion + 10
            await scheduler._execute(_claim(services, schedule.schedule_id, now, "second-owner"))
            successor = services.schedules.get(schedule.schedule_id)
            assert successor is not None and successor.last_invocation_id is not None
            assert successor.last_invocation_id != first.invocation_id
            second = services.invocations.get(successor.last_invocation_id)
            assert second is not None and second.status == "completed"
            assert services.invocations.read_input(second) == original
            assert successor.next_run_at == services.invocations.terminal_completed_at(second) + 10
            assert model.calls == [{"prompt": original}, {"prompt": original}]
    finally:
        services.schedules.cancel(schedule.schedule_id)
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize(
    "zone,base,expected",
    [
        ("UTC", "2026-10-12T00:00:00+00:00", "2026-10-13T00:00:00+00:00"),
        ("Asia/Dubai", "2026-10-12T00:00:00+00:00", "2026-10-12T20:00:00+00:00"),
        ("Europe/Berlin", "2026-03-28T23:00:00+00:00", "2026-03-29T22:00:00+00:00"),
        ("Europe/Berlin", "2026-10-24T22:00:00+00:00", "2026-10-25T23:00:00+00:00"),
    ],
)
async def test_cron_persists_zone_and_replays_each_missed_nominal_slot(
    tmp_path: Path,
    engine_name: str,
    engine_type: Any,
    backend: str,
    zone: str,
    base: str,
    expected: str,
) -> None:
    model = FakeModel(lambda prompt: prompt)
    services = _services(tmp_path, backend, model)
    engine = engine_type()
    plan = _plan({"cron": "0 0 * * *"}, "calendar-catchup")
    schedule = services.schedules.create(
        plan, {"calendar": zone}, now=datetime.fromisoformat(base).timestamp(), timezone=zone
    )
    expected_due = datetime.fromisoformat(expected).timestamp()
    assert schedule.next_run_at == expected_due
    schedule_id = schedule.schedule_id
    services.close()
    services = _services(tmp_path, backend, model)
    now = expected_due + 3 * 86400 + 3600
    scheduler = WorkflowScheduler(services, engine, clock=lambda: now)
    ids: list[str] = []
    due: list[float] = []
    try:
        await engine.initialize(services)
        reopened = services.schedules.get(schedule_id)
        assert reopened is not None and reopened.timezone == zone
        assert reopened.next_run_at == expected_due
        for index in range(3):
            selected = _claim(services, schedule_id, now, f"calendar-owner-{index}")
            nominal = services.schedules.connection.execute(
                "SELECT nominal_at FROM schedule_slots "
                "WHERE schedule_id = ? AND occurrence_key = ?",
                (schedule_id, selected._claim.occurrence_key),
            ).fetchone()
            assert nominal is not None
            due.append(nominal[0])
            await scheduler._execute(selected)
            saved = services.schedules.get(schedule_id)
            assert saved is not None and saved.last_invocation_id is not None
            handle = services.invocations.get(saved.last_invocation_id)
            assert handle is not None and handle.status == "completed"
            assert handle.output == {"prompt": {"calendar": zone}}
            ids.append(handle.invocation_id)
        assert len(set(ids)) == len(model.calls) == 3
        assert due == [expected_due + index * 86400 for index in range(3)]
        persisted = services.schedules.get(schedule_id)
        assert persisted is not None and persisted.next_run_at == expected_due + 3 * 86400
    finally:
        services.schedules.cancel(schedule_id)
        await engine.shutdown()
        services.close()
