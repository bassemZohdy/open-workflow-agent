"""Completed trust stages yield without extending ownership or weakening evidence."""

import asyncio
import os
import time
from uuid import uuid4

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.engine import PortableWorkflowEngine
from open_workflow_agent.errors import InvocationStateError, UnsupportedWorkflowFeature
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow


@pytest.fixture(params=["sqlite", "postgresql"])
async def recovery(tmp_path, request):
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if request.param == "postgresql" else None
    if request.param == "postgresql" and datasource is None:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    services = RuntimeServices(
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}}),
        model=FakeModel(),
        database_root=None if datasource else tmp_path,
    )
    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "recovery-cooperation",
                "name": uuid4().hex,
                "version": "1.0.0",
            },
            "do": [{"result": {"set": {"original": True}}}],
        }
    )
    handle = services.invocations.create(
        engine="portable",
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    owner = services.execution_ownership.acquire(handle, seconds=60)
    services.recovery_definitions.admit(plan, handle, {"original": None}, execution_lease=owner)
    services.execution_ownership.release(owner)
    engine = PortableWorkflowEngine()
    # Exercise the actual common ownership/recovery methods without starting
    # global delivery daemons against unrelated PostgreSQL fixture records.
    engine.services = services
    engine._execution_ownership_seconds = 5
    try:
        yield engine, services, plan, handle
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
async def test_original_five_second_lease_renews_during_completed_sync_trust_stages(
    recovery, monkeypatch
):
    engine, services, plan, handle = recovery
    stages = []
    for target, name in (
        (services.recovery_definitions, "verify"),
        (services, "verify_background_jobs"),
        (services.effect_journal, "verify_invocation"),
        (services.timers, "verify_invocation"),
        (services.child_waits, "verify_invocation"),
        (services.event_store, "verify_invocation"),
    ):
        original = getattr(target, name)

        def wrap(call, label):
            def checked(*args, **kwargs):
                result = call(*args, **kwargs)
                # Real verification has finished. Simulate bounded synchronous
                # artifact checking on this same thread, without a DB fence.
                time.sleep(1)
                stages.append(label)
                return result

            return checked

        monkeypatch.setattr(target, name, wrap(original, name))
    renewed = []
    original_renew = services.execution_ownership.renew

    def observe(lease, *, seconds):
        assert seconds == 5
        current = original_renew(lease, seconds=seconds)
        renewed.append(current)
        return current

    monkeypatch.setattr(services.execution_ownership, "renew", observe)
    before = services.invocations.get(handle.invocation_id)._persisted_payload
    started = time.monotonic()
    async with asyncio.timeout(20):
        async with engine._own_execution(handle):
            admitted = handle._execution_lease
            await engine._preflight_definition(handle, plan)
            original_input = await engine._verify_resume_state(handle, plan)
            services.execution_ownership.verify(handle._execution_lease)
    assert time.monotonic() - started >= 7
    # One renewal is sufficient to retain this unchanged five-second lease
    # across seven seconds. Timer scheduling does not promise two callbacks.
    assert len(stages) >= 7 and len(renewed) >= 1
    assert all(
        value.owner == admitted.owner and value.generation == admitted.generation
        for value in renewed
    )
    assert original_input == {"original": None}
    assert services.invocations.get(handle.invocation_id)._persisted_payload == before
    assert services.model.calls == []


@pytest.mark.asyncio
async def test_profile_changed_between_yielding_stages_is_verified_again_before_continuation(
    recovery, monkeypatch
):
    engine, services, plan, handle = recovery
    original_provider = services.recovery_definitions.profile_provider
    before = services.invocations.get(handle.invocation_id)._persisted_payload
    async with engine._own_execution(handle):
        await engine._preflight_definition(handle, plan)

        def changed(name):
            value = dict(original_provider(name))
            value["changed_deployment_revision"] = "must-reject"
            return value

        monkeypatch.setattr(services.recovery_definitions, "profile_provider", changed)
        with pytest.raises(InvocationStateError) as failure:
            await engine._verify_resume_state(handle, plan)
        assert failure.value.details["reason"] == "runtime_profile_changed"
    assert services.invocations.get(handle.invocation_id)._persisted_payload == before
    assert services.invocations.read_input(handle) == {"original": None}
    assert services.model.calls == []


@pytest.mark.asyncio
async def test_required_native_extension_rejects_before_ownership_or_archive_mutation(recovery):
    engine, services, plan, handle = recovery
    unsupported = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "recovery-cooperation",
                "name": "unsupported-native-feature",
                "version": "1.0.0",
                "metadata": {
                    "owa": {
                        "nativeExtensions": [{"name": "langgraph.channels", "version": "1.0.0"}]
                    }
                },
            },
            "do": [{"result": {"set": {"original": True}}}],
        }
    )
    ownership_before = services.execution_ownership.connection.execute(
        "SELECT * FROM invocation_ownership WHERE invocation_id=?", (handle.invocation_id,)
    ).fetchall()
    archived_before = services.recovery_definitions.resolve(handle)

    with pytest.raises(UnsupportedWorkflowFeature, match="required native workflow extension"):
        await engine._preflight_definition(handle, unsupported, initial=True)

    assert (
        services.execution_ownership.connection.execute(
            "SELECT * FROM invocation_ownership WHERE invocation_id=?", (handle.invocation_id,)
        ).fetchall()
        == ownership_before
    )
    assert services.recovery_definitions.resolve(handle).fingerprint == archived_before.fingerprint
    assert plan.fingerprint == archived_before.fingerprint
