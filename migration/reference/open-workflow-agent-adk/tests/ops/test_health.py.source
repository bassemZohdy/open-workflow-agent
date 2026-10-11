import asyncio

import pytest

from openworkflow_adk import WorkflowHealth


async def test_health_drains_in_flight_run() -> None:
    health = WorkflowHealth()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def active_run() -> None:
        async with health.run():
            entered.set()
            await release.wait()

    task = asyncio.create_task(active_run())
    await entered.wait()
    draining = asyncio.create_task(health.shutdown(timeout=1))
    await asyncio.sleep(0)
    assert health.readiness() == {"ready": False, "in_flight": 1}
    release.set()
    assert await draining is True
    await task
    assert health.readiness()["in_flight"] == 0


async def test_health_shutdown_times_out_and_rejects_new_runs() -> None:
    health = WorkflowHealth()
    async with health.run():
        assert await health.shutdown(timeout=0) is False
        with pytest.raises(RuntimeError, match="draining"):
            async with health.run():
                pass
