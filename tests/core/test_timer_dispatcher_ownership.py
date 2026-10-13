"""A competing timer worker cannot resolve dependencies before owning execution."""

from __future__ import annotations

import asyncio

import open_workflow_agent.timer_dispatcher as timer_module
import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.engine import PortableWorkflowEngine
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import WorkflowExecutor, compile_workflow


@pytest.mark.asyncio
async def test_separate_timer_worker_rejects_owned_invocation_before_dependency_resolution(
    tmp_path, monkeypatch
):
    now = 1000.0
    initialize = WorkflowExecutor.__init__

    def clocked(executor, *args, **kwargs):
        initialize(executor, *args, **kwargs)
        executor._absolute_clock = lambda: now

    monkeypatch.setattr(WorkflowExecutor, "__init__", clocked)
    first = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
    other = RuntimeServices(
        RuntimeConfig(), model=FakeModel({"done": True}), database_root=tmp_path
    )
    owner, worker = PortableWorkflowEngine(), PortableWorkflowEngine()
    network_resolved = []
    archive_reads = []
    resolve = timer_module.resolve_and_compile_sandbox_workflow

    async def counted(*args, **kwargs):
        network_resolved.append(args[0])
        return await resolve(*args, **kwargs)

    monkeypatch.setattr(timer_module, "resolve_and_compile_sandbox_workflow", counted)
    retained = other.resolve_original_definition

    def archived(handle):
        lease = handle._execution_lease
        if lease is not None:
            other.execution_ownership.verify(lease)
        original = retained(handle)
        archive_reads.append((handle.invocation_id, lease, original.fingerprint))
        return original

    monkeypatch.setattr(other, "resolve_original_definition", archived)
    resume = worker._resume_owned

    async def observed_resume(handle, resume_input, original):
        # Pure archive preflight is intentionally permitted before ownership.
        # Execution observes the same original definition under the real lease;
        # it never compiles/fetches dependencies from the timer's source copy.
        assert handle._execution_lease is not None
        other.execution_ownership.verify(handle._execution_lease)
        assert other.resolve_original_definition(handle) == original
        assert other.invocations.read_input(handle) == {"original": True}
        return await resume(handle, resume_input, original)

    monkeypatch.setattr(worker, "_resume_owned", observed_resume)
    plan = compile_workflow(
        {
            "document": {"dsl": "1.0.3", "namespace": "tests", "name": "owner", "version": "1.0.0"},
            "do": [
                {"pause": {"wait": {"seconds": 60}}},
                {"finish": {"call": "llm:1.0.0@default", "with": {"prompt": "done"}}},
            ],
        }
    )
    try:
        await owner.initialize(first)
        await worker.initialize(other)
        assert owner._timer_dispatcher is not None and worker._timer_dispatcher is not None
        await owner._timer_dispatcher.stop()
        await worker._timer_dispatcher.stop()
        handle = first.invocations.create(
            engine=owner.engine_name,
            session_id=None,
            user_id=None,
            workflow_name=plan.name,
            workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
        )
        assert (await owner.invoke(plan, handle, {"original": True})).status == "waiting"
        before = other.invocations.get(handle.invocation_id)._persisted_payload
        now = 1060.0
        async with owner._own_execution(handle):
            assert await worker._timer_dispatcher.tick(invocation_id=handle.invocation_id) == 1
            async with asyncio.timeout(10):
                while worker._timer_dispatcher._workers:
                    await asyncio.sleep(0.005)
            assert network_resolved == [] and other.model.calls == []
            assert all(lease is None for _, lease, _ in archive_reads)
            assert other.invocations.get(handle.invocation_id)._persisted_payload == before
        assert await worker._timer_dispatcher.tick(invocation_id=handle.invocation_id) == 1
        async with asyncio.timeout(10):
            while worker._timer_dispatcher._workers:
                await asyncio.sleep(0.005)
        stored = other.invocations.get(handle.invocation_id)
        assert stored.status == "completed" and stored.output == {"done": True}
        leased = [value for value in archive_reads if value[1] is not None]
        assert len(leased) == 1 and leased[0][0] == handle.invocation_id
        assert all(fingerprint == plan.fingerprint for _, _, fingerprint in archive_reads)
        assert network_resolved == [] and other.model.calls == [{"prompt": "done"}]
        assert other.invocations.read_input(stored) == {"original": True}
    finally:
        await owner.shutdown()
        await worker.shutdown()
        first.close()
        other.close()
