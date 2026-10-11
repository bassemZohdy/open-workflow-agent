"""Common recovery inputs must keep their JSON meaning on every adapter."""

from __future__ import annotations

import asyncio

import pytest
from engine_cases import engine_cases
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize(
    "resume_input",
    [
        pytest.param(None, id="null"),
        pytest.param(False, id="false"),
        pytest.param(0, id="zero"),
        pytest.param("", id="empty-string"),
        pytest.param([], id="empty-array"),
        pytest.param({}, id="empty-object"),
    ],
)
async def test_fresh_wait_resume_preserves_all_json_inputs(
    tmp_path, engine_name, engine_type, resume_input
):
    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "test",
                "name": "checkpoint-recovery",
                "version": "1.0.0",
            },
            "do": [
                {"pause": {"wait": {"seconds": 300}}},
                {"report": {"set": {"resumed": "${ $resume_input }"}}},
            ],
        }
    )
    services = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
    engine = engine_type()
    await engine.initialize(services)
    handle = services.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    running = asyncio.create_task(engine.invoke(plan, handle, {}))
    try:
        async with asyncio.timeout(10):
            while handle.status != "waiting":
                await asyncio.sleep(0.005)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(running, timeout=10)
        assert services.model.calls == []
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()
        services.close()

    restarted = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
    resumed_engine = engine_type()
    await resumed_engine.initialize(restarted)
    persisted = restarted.invocations.get(handle.invocation_id)
    assert persisted is not None and persisted.status == "waiting"
    try:
        try:
            result = await asyncio.wait_for(
                resumed_engine.resume(persisted, resume_input, plan), timeout=10
            )
        except TimeoutError:
            pytest.fail("a submitted JSON resume value must wake the recovered wait")
        assert result.status == "completed", result.error
        assert result.output == {"resumed": resume_input}
        assert restarted.model.calls == []
        replay = await resumed_engine.resume(persisted, {"duplicate": True}, plan)
        assert replay.status == "completed" and replay.output == result.output
    finally:
        await resumed_engine.shutdown()
        restarted.close()
