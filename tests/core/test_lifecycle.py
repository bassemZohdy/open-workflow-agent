from __future__ import annotations

import asyncio

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.engine import PortableWorkflowEngine
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow


def _workflow(tasks):
    return {
        "document": {
            "dsl": "1.0.3",
            "namespace": "tests",
            "name": "lifecycle",
            "version": "1.0.0",
        },
        "do": tasks,
    }


async def _until(predicate):
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition was not reached")


@pytest.mark.asyncio
async def test_wait_resume_and_state_transitions(tmp_path):
    services = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
    engine = PortableWorkflowEngine()
    await engine.initialize(services)
    plan = compile_workflow(
        _workflow(
            [
                {"pause": {"wait": {"seconds": 5}}},
                {"finish": {"set": {"done": True}}},
            ]
        )
    )
    handle = services.invocations.create(
        engine="portable",
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    running = asyncio.create_task(engine.invoke(plan, handle, {}))
    await _until(lambda: handle.status == "waiting")
    assert handle.status == "waiting"
    resumed = await engine.resume(handle, {"answer": "continue"}, plan)
    assert resumed.status == "completed"
    assert handle.status == "completed"
    assert [event.event_type for event in services.events.events].count("WorkflowWaiting") == 1
    assert [event.event_type for event in services.events.events].count("WorkflowResumed") == 1
    assert (await running).status == "completed"
    services.close()


@pytest.mark.asyncio
async def test_cancellation_while_running_is_terminal_and_real(tmp_path):
    started = asyncio.Event()

    async def slow(_prompt):
        started.set()
        await asyncio.sleep(30)
        return {"never": "returned"}

    services = RuntimeServices(RuntimeConfig(), model=FakeModel(slow), database_root=tmp_path)
    engine = PortableWorkflowEngine()
    await engine.initialize(services)
    plan = compile_workflow(
        _workflow(
            [
                {
                    "slow": {
                        "call": "llm:1.0.0@default",
                        "with": {"prompt": "slow"},
                    }
                }
            ]
        )
    )
    handle = services.invocations.create(
        engine="portable",
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    running = asyncio.create_task(engine.invoke(plan, handle, {}))
    await started.wait()
    cancelled = await engine.cancel(handle, operation_id="cancel-1")
    assert cancelled.status == "cancelled"
    assert (await running).status == "cancelled"
    duplicate = await engine.cancel(handle, operation_id="cancel-1")
    assert duplicate.status == "cancelled"
    assert handle.status == "cancelled"
    assert any(event.event_type == "WorkflowCancelled" for event in services.events.events)
    services.close()


def test_invocation_store_rejects_invalid_terminal_transition(tmp_path):
    from open_workflow_agent.errors import InvocationStateError
    from open_workflow_agent.persistence import InvocationStore

    store = InvocationStore(tmp_path / "runtime.sqlite3")
    handle = store.create(
        engine="test",
        session_id=None,
        user_id=None,
        workflow_name="lifecycle",
        workflow_version="1.0.0",
        workflow_fingerprint="fingerprint",
    )
    store.update(handle, status="completed", output={"done": True})
    with pytest.raises(InvocationStateError):
        store.update(handle, status="running")
    store.close()
