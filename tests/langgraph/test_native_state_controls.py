"""Real common control intents reconcile real SDK commits without duplicate updates."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

import pytest
from native_stream_test_support import authority, configuration
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.errors import InvocationStateError, OwaError
from open_workflow_agent.execution_authority import authority_scope
from open_workflow_agent.execution_authorization import ExecutionAuthorizationStore
from open_workflow_agent.recovery_definitions import PROFILE, RecoveryDefinitionArchive
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.state_updates import (
    TABLE,
    StateUpdateBackend,
    StateUpdateRequest,
    StateUpdates,
)
from open_workflow_agent.storage import open_storage
from open_workflow_agent.workflow import compile_workflow
from test_native_state_mutations import graph

pytest.importorskip("langgraph.graph")
from open_workflow_agent_langgraph import native
from open_workflow_agent_langgraph.graph import LangGraphScopeExecutor
from open_workflow_agent_langgraph.native_state import state_reference
from open_workflow_agent_langgraph.native_state_controls import LangGraphStateControls
from open_workflow_agent_langgraph.native_state_mutations import (
    NativeStateWrite,
    prepare_state_update_storage,
)


@pytest.fixture(params=["sqlite", "postgresql"])
async def controls(request, tmp_path):
    config = configuration()
    config.observability.native_streams = None
    config.execution_authorization.policy.rules[0].actions.append("state.update")
    if request.param == "postgresql":
        datasource = os.getenv("OWA_TEST_POSTGRES_URL")
        if not datasource:
            pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
        config.persistence.datasource = datasource
    services = RuntimeServices(
        config, model=FakeModel(), database_root=None if request.param == "postgresql" else tmp_path
    )
    archive = RecoveryDefinitionArchive(
        services.effect_journal,
        services.invocations,
        profile_provider=lambda engine: {
            "profile": PROFILE,
            "engine": engine,
            "backend": request.param,
            "continuation_supported": True,
        },
    )
    auth = ExecutionAuthorizationStore(
        services.effect_journal,
        services.invocations,
        archive,
        config_provider=lambda: config.execution_authorization,
        security_provider=lambda: config.security,
    )
    archive.admission_participant = auth.admission_participant
    archive.authority_expectation = auth.expected_authority_digest
    archive.authority_verifier = auth.verify_invocation
    services.recovery_definitions = archive
    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "test",
                "name": "state-control-" + uuid4().hex,
                "version": "1.0.0",
            },
            "do": [{"change": {"set": {"value": 1}}}],
        }
    )
    handle = services.invocations.create(
        engine="langgraph",
        session_id=None,
        user_id=None,
        owner_principal="alice",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    handle._execution_lease = services.execution_ownership.acquire(handle, seconds=300)
    actor = authority(services, "alice")
    auth.reserve_invocation(handle, actor, input_data={"value": 1})
    with authority_scope(actor):
        archive.admit(plan, handle, {"value": 1}, execution_lease=handle._execution_lease)
    database = services.engine_database_path("langgraph")
    saver_type = (
        native.AsyncPostgresSaver if request.param == "postgresql" else native.AsyncSqliteSaver
    )
    root = LangGraphScopeExecutor(plan, None, handle.invocation_id).checkpoint_config("/do", 1)
    async with saver_type.from_conn_string(database) as saver:
        await saver.setup()
        await prepare_state_update_storage(saver, postgresql=request.param == "postgresql")
        await graph(saver).ainvoke({"value": 1}, root)
        initial = await saver.aget_tuple(root)
    calls = []

    async def update(saver, config, change):
        calls.append(change.identity.key)
        assert change.task_reference == "/do/0/change"
        return await graph(saver).aupdate_state(config, change.values, as_node="change")

    bridge = LangGraphStateControls(services, plan, update)
    coordinator = StateUpdates(services.effect_journal, services.invocations, auth)
    coordinator.register(bridge.backend())
    change = StateUpdateRequest.create(
        invocation_id=handle.invocation_id,
        workflow_fingerprint=plan.fingerprint,
        request_id=uuid4().hex * 2,
        base_reference=state_reference(
            handle.invocation_id, plan.fingerprint, initial.config["configurable"]["checkpoint_id"]
        ),
        task_reference="/do/0/change",
        values={"value": 7},
        policy_digest="a" * 64,
        storage_identity=bridge.storage_identity,
    )
    try:
        yield services, coordinator, bridge, handle, change, calls, actor, saver_type, root
    finally:
        services.close()


@pytest.mark.asyncio
async def test_real_sdk_commit_has_only_opaque_references_in_common_receipt(controls):
    services, coordinator, bridge, handle, change, calls, actor, saver_type, root = controls
    proof = await coordinator.update(handle, change, actor)
    assert bridge.inspect(change) == proof
    assert await coordinator.update(handle, change, actor) == proof
    assert calls == [change.identity.key]
    async with saver_type.from_conn_string(bridge.database) as saver:
        assert (await graph(saver).aget_state(root)).values == {"value": 7}
        checkpoint = await saver.aget_tuple(root)
    payload = json.dumps(coordinator.journal.get(change.identity).output)
    assert checkpoint.config["configurable"]["checkpoint_id"] not in payload
    assert root["configurable"]["thread_id"] not in payload
    services.effect_journal.verify_invocation(handle.invocation_id, handle.workflow_fingerprint)


@pytest.mark.asyncio
async def test_real_native_commit_without_common_ack_recovers_without_calling_sdk_again(controls):
    services, coordinator, bridge, handle, change, calls, actor, *_ = controls

    async def interrupted(request, handle):
        await bridge.apply(request, handle)
        raise RuntimeError("interrupted after native commit before acknowledgement")

    coordinator._backends[bridge.storage_identity] = StateUpdateBackend(
        bridge.storage_identity, interrupted, bridge.inspect
    )
    with pytest.raises(InvocationStateError):
        await coordinator.update(handle, change, actor)
    assert (
        coordinator.connection.execute(
            f"SELECT phase FROM {TABLE} WHERE identity=?", (change.identity.key,)
        ).fetchone()[0]
        == "dispatched"
    )
    assert bridge.inspect(change) is not None
    coordinator._backends[bridge.storage_identity] = bridge.backend()
    await coordinator.update(handle, change, actor)
    assert calls == [change.identity.key]
    services.effect_journal.verify_invocation(handle.invocation_id, handle.workflow_fingerprint)


@pytest.mark.asyncio
async def test_missing_native_proof_never_reexecutes_a_dispatched_control(controls):
    _services, coordinator, bridge, handle, change, calls, actor, *_ = controls

    async def interrupted(*_args):
        raise RuntimeError("interrupted before native dispatch")

    coordinator._backends[bridge.storage_identity] = StateUpdateBackend(
        bridge.storage_identity, interrupted, bridge.inspect
    )
    with pytest.raises(InvocationStateError):
        await coordinator.update(handle, change, actor)
    coordinator._backends[bridge.storage_identity] = bridge.backend()
    with pytest.raises(InvocationStateError) as failure:
        await coordinator.update(handle, change, actor)
    assert failure.value.details["reason"] == "outcome_unknown"
    assert calls == []


@pytest.mark.asyncio
async def test_real_native_proof_or_lineage_removal_prevents_completed_reuse(controls):
    _services, coordinator, bridge, handle, change, calls, actor, *_ = controls
    await coordinator.update(handle, change, actor)
    connection = open_storage(bridge.database, "owa_langgraph")
    try:
        connection.execute(
            "DELETE FROM owa_state_updates_v1 WHERE identity=?", (change.identity.key,)
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(OwaError):
        await coordinator.update(handle, change, actor)
    assert calls == [change.identity.key]


@pytest.mark.asyncio
@pytest.mark.parametrize("committed", [False, True])
async def test_process_exit_before_or_after_native_commit_preserves_control_recovery(
    controls, tmp_path, committed
):
    services, coordinator, bridge, handle, change, calls, actor, saver_type, root = controls
    async with saver_type.from_conn_string(bridge.database) as saver:
        initial = await saver.aget_tuple(root)
    native_request = NativeStateWrite(
        change.identity.key, change.request_digest, initial.config["configurable"]["checkpoint_id"]
    )

    async def crashed(request, handle):
        fence = services.execution_ownership.native_fence(handle._execution_lease)
        source = tmp_path / "control-worker.json"
        ready = tmp_path / "control-worker.ready"
        source.write_text(
            json.dumps(
                {
                    "database": bridge.database,
                    "common": fence.database,
                    "lease": asdict(fence.lease),
                    "handle": {
                        key: value
                        for key, value in asdict(handle).items()
                        if not key.startswith("_")
                    },
                    "workflow": bridge.plan.source,
                    "request": asdict(native_request),
                    "commit_before_ready": committed,
                }
            )
        )
        process = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).with_name("native_state_update_worker.py")),
                str(source),
                str(ready),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        try:
            async with asyncio.timeout(20):
                while not ready.exists():
                    if process.poll() is not None:
                        raise AssertionError(process.communicate()[0].decode(errors="replace"))
                    await asyncio.sleep(0.02)
            process.terminate()
            await asyncio.to_thread(process.wait, 10)
        finally:
            if process.poll() is None:
                process.kill()
                await asyncio.to_thread(process.wait, 10)
            process.communicate()
        raise RuntimeError("native worker exited before common acknowledgement")

    coordinator._backends[bridge.storage_identity] = StateUpdateBackend(
        bridge.storage_identity, crashed, bridge.inspect
    )
    with pytest.raises(InvocationStateError):
        await coordinator.update(handle, change, actor)
    assert (
        coordinator.connection.execute(
            f"SELECT phase FROM {TABLE} WHERE identity=?", (change.identity.key,)
        ).fetchone()[0]
        == "dispatched"
    )
    coordinator._backends[bridge.storage_identity] = bridge.backend()
    # A later ownership generation must retain the original request/authority.
    services.execution_ownership.connection.execute(
        "UPDATE invocation_ownership SET expires_at=1 WHERE invocation_id=?",
        (handle.invocation_id,),
    )
    services.execution_ownership.connection.commit()
    handle._execution_lease = services.execution_ownership.acquire(handle, seconds=300)
    if committed:
        assert bridge.inspect(change) is not None
        await coordinator.update(handle, change, actor)
        services.effect_journal.verify_invocation(handle.invocation_id, handle.workflow_fingerprint)
    else:
        assert bridge.inspect(change) is None
        with pytest.raises(InvocationStateError) as failure:
            await coordinator.update(handle, change, actor)
        assert failure.value.details["reason"] == "outcome_unknown"
    assert calls == []


@pytest.mark.asyncio
async def test_other_original_owner_cannot_start_native_control(controls):
    services, coordinator, _bridge, handle, change, calls, _actor, *_ = controls
    with pytest.raises(OwaError):
        await coordinator.update(handle, change, authority(services, "bob"))
    assert calls == []
