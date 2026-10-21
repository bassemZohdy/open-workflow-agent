"""Physical-profile waits retain real short leases without moving DB work off-loop."""

from __future__ import annotations

import asyncio
import copy
import os
import threading
from uuid import uuid4

import open_workflow_agent.services as services_module
import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.engine import PortableWorkflowEngine
from open_workflow_agent.errors import (
    ConfigurationError,
    InvocationStateConflict,
    InvocationStateError,
)
from open_workflow_agent.recovery_definitions import PROFILE
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.storage import StorageConnection
from open_workflow_agent.workflow import compile_workflow


@pytest.fixture(params=["sqlite", "postgresql"])
async def admission_runtime(tmp_path, request):
    backend = request.param
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("actual PostgreSQL datasource required")
    services = RuntimeServices(
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}}),
        model=FakeModel(),
        database_root=None if datasource else tmp_path,
    )
    assert (
        services.effect_journal.connection.kind == services.invocations.connection.kind == backend
    )
    engine = PortableWorkflowEngine()
    await engine.initialize(services)
    await engine._timer_dispatcher.stop()
    engine._execution_ownership_seconds = 0.5
    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "admission-renewal",
                "name": uuid4().hex,
                "version": "1.0.0",
            },
            "timeout": {"after": {"seconds": 45}},
            "do": [
                {
                    "publish": {
                        "emit": {"event": {"with": {"type": "verified.admission", "data": None}}}
                    }
                }
            ],
        }
    )
    handle = services.invocations.create(
        engine="reference",
        session_id=None,
        user_id="correlation",
        owner_principal=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    try:
        yield services, engine, plan, handle
    finally:
        await engine.shutdown()
        services.close()


def held_physical_profile(monkeypatch, services):
    owner = threading.get_ident()
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()
    calls = []

    def physical(engine, *, backend, config):
        assert threading.get_ident() != owner
        calls.append(copy.deepcopy(config))
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(timeout=5), "test did not release finite physical verifier"
        return {
            "profile": PROFILE,
            "engine": engine,
            "backend": backend,
            "continuation_supported": True,
            "physical": "a" * 64,
        }

    monkeypatch.setattr(services_module, "recovery_profile", physical)
    declaration_threads = []

    def declaration(engine):
        declaration_threads.append(threading.get_ident())
        assert threading.get_ident() == owner
        return {"id": "phase-fixture", "version": "1"}

    services._recovery_profile_provider = declaration
    execute = StorageConnection.execute

    def guarded_execute(self, *args, **kwargs):
        assert threading.get_ident() == owner, "database access escaped the owning loop"
        return execute(self, *args, **kwargs)

    monkeypatch.setattr(StorageConnection, "execute", guarded_execute)
    trust = services.recovery_definitions.trust_verifier

    def guarded_trust(plan):
        assert threading.get_ident() == owner
        assert trust is not None
        trust(plan)

    services.recovery_definitions.trust_verifier = guarded_trust
    return entered, release, calls, declaration_threads


def admission_counts(services, handle):
    connection = services.effect_journal.connection
    return tuple(
        connection.execute(
            "SELECT COUNT(*) FROM " + table + " WHERE invocation_id=?", (handle.invocation_id,)
        ).fetchone()[0]
        for table in (
            "recovery_invocation_definitions",
            "invocation_inputs",
            "event_invocation_profiles",
            "workflow_effect_invocations",
        )
    )


@pytest.mark.asyncio
async def test_physical_profile_wait_renews_real_half_second_owner_before_atomic_event_binding(
    admission_runtime, monkeypatch
):
    services, engine, plan, handle = admission_runtime
    entered, release, calls, declarations = held_physical_profile(monkeypatch, services)
    task = None
    original = {"original": None, "nested": [True, False]}
    try:
        async with engine._own_execution(handle):
            initial_expiry = handle._execution_lease.expires_at
            task = asyncio.create_task(engine._prepare_workflow_input_owned(plan, handle, original))
            await asyncio.wait_for(entered.wait(), 2)
            assert admission_counts(services, handle) == (0, 0, 0, 0)
            async with asyncio.timeout(2):
                while (
                    services.execution_ownership.connection.execute(
                        "SELECT " + services.execution_ownership._clock
                    ).fetchone()[0]
                    <= initial_expiry
                    or handle._execution_lease.expires_at <= initial_expiry
                ):
                    await asyncio.sleep(0.01)
            services.execution_ownership.verify(handle._execution_lease)
            release.set()
            assert await task == original
            assert len(calls) == 1 and declarations
            assert admission_counts(services, handle) == (1, 1, 1, 1)
            services.execution_ownership.verify(handle._execution_lease)
            assert services.invocations.read_input(handle) == original
            assert services.model.calls == []
            assert services.recovery_definitions._prepared_profile.get() is None
    finally:
        release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["config", "declaration", "provider"])
async def test_changed_binding_during_physical_wait_rejects_without_adopting_stale_manifest(
    admission_runtime, monkeypatch, changed
):
    services, engine, plan, handle = admission_runtime
    entered, release, _, _ = held_physical_profile(monkeypatch, services)
    task = None
    try:
        async with engine._own_execution(handle):
            task = asyncio.create_task(
                engine._prepare_workflow_input_owned(plan, handle, {"original": None})
            )
            await asyncio.wait_for(entered.wait(), 2)
            if changed == "config":
                services.config.model.temperature = 0.5
            elif changed == "declaration":
                services._recovery_profile_provider = lambda engine: {
                    "id": "phase-fixture",
                    "version": "2",
                }
            else:
                services.recovery_definitions.profile_provider = lambda engine: {
                    "replacement": True
                }
            release.set()
            with pytest.raises((ConfigurationError, InvocationStateError)) as caught:
                await task
            if changed == "provider":
                assert caught.value.details["reason"] == "runtime_profile_changed"
            else:
                assert (
                    caught.value.message
                    == "recovery deployment binding changed during verification"
                )
                assert caught.value.details == {}
            assert admission_counts(services, handle) == (0, 0, 0, 0)
            assert services.recovery_definitions._prepared_profile.get() is None
            assert handle.status == "running" and services.model.calls == []
            services.execution_ownership.verify(handle._execution_lease)
    finally:
        release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("takeover", [False, True])
async def test_expired_or_replaced_owner_never_commits_after_physical_wait(
    admission_runtime, monkeypatch, takeover
):
    services, engine, plan, handle = admission_runtime
    entered, release, _, _ = held_physical_profile(monkeypatch, services)

    async def no_renewal():
        await asyncio.Event().wait()

    monkeypatch.setattr(engine, "_wait_for_ownership_renewal", no_renewal)
    task = replacement = None
    try:
        with pytest.raises(InvocationStateConflict):
            async with engine._own_execution(handle):
                task = asyncio.create_task(
                    engine._prepare_workflow_input_owned(plan, handle, {"original": None})
                )
                await asyncio.wait_for(entered.wait(), 2)
                async with asyncio.timeout(2):
                    while services.execution_ownership.connection.execute(
                        "SELECT 1 FROM invocation_ownership WHERE invocation_id=? AND expires_at>"
                        + services.execution_ownership._clock,
                        (handle.invocation_id,),
                    ).fetchone():
                        await asyncio.sleep(0.01)
                if takeover:
                    replacement = services.execution_ownership.acquire(handle, seconds=0.5)
                release.set()
                await task
        assert admission_counts(services, handle) == (0, 0, 0, 0)
        assert services.model.calls == []
    finally:
        release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        if replacement is not None:
            services.execution_ownership.release(replacement)


@pytest.mark.asyncio
async def test_prepared_manifest_is_task_local_single_phase_and_not_inherited_by_spawned_reader(
    admission_runtime,
):
    services, _, _, _ = admission_runtime
    archive = services.recovery_definitions
    calls = []

    def current(engine):
        calls.append(engine)
        return {"revision": len(calls)}

    archive.profile_provider = current

    async def child_read():
        return archive._current_profile("reference")

    async with archive.owned_profile("reference"):
        assert archive._current_profile("reference") == {"revision": 1}
        assert await asyncio.create_task(child_read()) == {"revision": 2}
        assert archive._current_profile("reference") == {"revision": 1}
    assert archive._current_profile("reference") == {"revision": 3}
    assert archive._prepared_profile.get() is None


@pytest.mark.asyncio
async def test_cancelled_file_workers_retain_bounded_slots_until_actual_completion(
    admission_runtime, monkeypatch
):
    services, _, _, _ = admission_runtime
    loop = asyncio.get_running_loop()
    started = []
    two = asyncio.Event()
    release = threading.Event()

    def physical(engine, *, backend, config):
        def record():
            started.append(engine)
            if len(started) == 2:
                two.set()

        loop.call_soon_threadsafe(record)
        assert release.wait(timeout=5)
        return {
            "profile": PROFILE,
            "engine": engine,
            "backend": backend,
            "continuation_supported": True,
        }

    monkeypatch.setattr(services_module, "recovery_profile", physical)
    tasks = [asyncio.create_task(services._recovery_profile_async("reference")) for _ in range(2)]
    third = None
    try:
        await asyncio.wait_for(two.wait(), 2)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        third = asyncio.create_task(services._recovery_profile_async("reference"))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not third.done() and len(started) == 2
        release.set()
        assert (await asyncio.wait_for(third, 2))["engine"] == "reference"
        assert len(started) == 3
    finally:
        release.set()
        await asyncio.gather(*tasks, *([third] if third else []), return_exceptions=True)
