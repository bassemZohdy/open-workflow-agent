"""Cleanup before/partway through initialization preserves the original failure."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from engine_cases import engine_cases
from open_workflow_agent.engine import PortableWorkflowEngine
from open_workflow_agent.errors import WorkflowSchemaError
from open_workflow_agent.workflow import compile_workflow

ENGINES = [("portable", PortableWorkflowEngine), *engine_cases()]


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
async def test_shutdown_before_initialize_is_safe_and_repeatable(engine_name, engine_type):
    engine = engine_type()
    assert not hasattr(engine, "services")
    await engine.shutdown()
    await engine.shutdown()
    assert engine._active == {} and engine._ownership_tasks == {}
    assert not hasattr(engine, "services")


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
async def test_shutdown_does_not_mask_original_admission_failure(engine_name, engine_type):
    engine = engine_type()
    original = None
    with pytest.raises(WorkflowSchemaError) as caught:
        try:
            compile_workflow(
                {
                    "document": {
                        "dsl": "1.0.3",
                        "namespace": "cleanup",
                        "name": "invalid",
                        "version": "1.0.0",
                    },
                    "do": [{"invalid": {"call": "agent:1.0.0@default", "with": "${ . }"}}],
                }
            )
        except WorkflowSchemaError as error:
            original = error
            raise
        finally:
            await engine.shutdown()
    assert caught.value is original
    assert caught.value.code == "workflow_schema_error"
    assert not hasattr(engine, "services")


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
async def test_partially_initialized_shutdown_still_stops_observers_and_owned_tasks(
    engine_name, engine_type
):
    engine = engine_type()
    background = AsyncMock()
    sandbox = SimpleNamespace(shutdown=AsyncMock())
    engine.services = SimpleNamespace(stop_background_jobs=background, sandbox=sandbox)
    timer = SimpleNamespace(stop=AsyncMock())
    initial = SimpleNamespace(stop=AsyncMock())
    engine._timer_dispatcher = timer
    engine._initial_delivery_dispatcher = initial
    started = asyncio.Event()

    async def owned():
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(owned())
    await started.wait()
    engine._ownership_tasks["partial"] = task
    await engine.shutdown()
    assert task.cancelled()
    background.assert_awaited_once_with()
    timer.stop.assert_awaited_once_with()
    initial.stop.assert_awaited_once_with()
    sandbox.shutdown.assert_awaited_once_with()
