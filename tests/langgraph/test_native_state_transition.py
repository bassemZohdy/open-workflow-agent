"""Actual OWA checkpoint changes carry authorized immutable projection attestations."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from open_workflow_agent.errors import OwaError
from open_workflow_agent.execution_authority import authority_scope
from open_workflow_agent.state_update_resolution import StateUpdateResolution, resolution_identity
from open_workflow_agent.state_updates import StateUpdateBackend, StateUpdates
from open_workflow_agent.storage import open_storage
from test_native_state_preflight import preflight as _preflight_fixture
from test_native_state_preflight import prepare

pytest.importorskip("langgraph.graph")
from open_workflow_agent_langgraph.graph import LangGraphScopeExecutor
from open_workflow_agent_langgraph.native_state_mutations import prepare_state_update_storage
from open_workflow_agent_langgraph.native_state_transition import (
    AttestedStateControls,
    projection_digest,
)

preflight = _preflight_fixture


@pytest.fixture
async def controls(preflight):
    services, engine, handle, actor, _reference, _checkpoint, saver_type, plan = preflight
    bridge = AttestedStateControls(services, plan)
    async with saver_type.from_conn_string(bridge.database) as saver:
        await prepare_state_update_storage(
            saver, postgresql=services.effect_journal.connection.is_postgresql
        )
    coordinator = StateUpdates(
        services.effect_journal, services.invocations, services.execution_authorizations
    )
    coordinator.register(bridge.backend())
    return preflight, coordinator, bridge


async def checkpoint(controls):
    preflight, _coordinator, bridge = controls
    services, _engine, handle, _actor, _reference, _old, saver_type, plan = preflight
    async with saver_type.from_conn_string(bridge.database) as saver:
        driver = LangGraphScopeExecutor(
            plan, saver, handle.invocation_id, effect_journal=services.effect_journal
        )
        return await saver.aget_tuple(driver.checkpoint_config("/do", 1))


@pytest.mark.asyncio
async def test_owa_projection_and_common_receipt_commit_without_executing_tasks(controls):
    preflight, coordinator, bridge = controls
    services, _engine, handle, actor, _reference, original, _saver, plan = preflight
    transition = bridge.stage(await prepare(preflight))
    assert transition.request.profile == "owa/state-update/v2"
    proof = await coordinator.update(handle, transition.request, actor)
    current = await checkpoint(controls)
    packet = current.checkpoint["channel_values"]
    assert projection_digest(packet) == transition.request.projection_digest
    before = json.loads(original.checkpoint["channel_values"]["snapshot"])
    after = json.loads(packet["snapshot"])
    assert after == before | {"data": {"value": 7}}
    assert packet["counters"] == original.checkpoint["channel_values"]["counters"]
    assert packet["next_node"] == original.checkpoint["channel_values"]["next_node"]
    driver = LangGraphScopeExecutor(
        plan, None, handle.invocation_id, effect_journal=services.effect_journal
    )
    driver._expected_input_json = "{}"
    driver._validate_native(packet, "/do", 1)
    driver.verify_current_controls(packet)
    # Historical inspection still accepts the unchanged version-two packet.
    driver._validate_native(original.checkpoint["channel_values"], "/do", 1)
    assert bridge.proposals == {}
    assert await coordinator.update(handle, transition.request, actor) == proof
    assert (await checkpoint(controls)).checkpoint["id"] == current.checkpoint["id"]
    assert services.model.calls == [] and handle.status == "waiting"


@pytest.mark.asyncio
async def test_changed_attested_checkpoint_cannot_reuse_a_valid_common_receipt(controls):
    preflight, coordinator, bridge = controls
    services, _engine, handle, actor, *_ = preflight
    transition = bridge.stage(await prepare(preflight))
    await coordinator.update(handle, transition.request, actor)
    current = await checkpoint(controls)
    thread = current.config["configurable"]["thread_id"]
    key = current.config["configurable"]["checkpoint_id"]
    connection = open_storage(bridge.database, "owa_langgraph")
    try:
        if connection.is_postgresql:
            connection.execute(
                "UPDATE checkpoints SET checkpoint=jsonb_set(checkpoint,"
                "'{channel_values,snapshot}', to_jsonb(?::text)) "
                "WHERE thread_id=? AND checkpoint_id=?",
                ("{}", thread, key),
            )
        else:
            connection.execute(
                "UPDATE checkpoints SET checkpoint=? WHERE thread_id=? AND checkpoint_id=?",
                (b"corrupt", thread, key),
            )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(OwaError):
        await coordinator.update(handle, transition.request, actor)
    assert services.model.calls == []


@pytest.mark.asyncio
async def test_older_unattested_head_cannot_be_adopted_as_current_after_a_control(controls):
    preflight, coordinator, bridge = controls
    services, _engine, handle, actor, _reference, original, _saver, plan = preflight
    transition = bridge.stage(await prepare(preflight))
    await coordinator.update(handle, transition.request, actor)
    driver = LangGraphScopeExecutor(
        plan, None, handle.invocation_id, effect_journal=services.effect_journal
    )
    with pytest.raises(OwaError):
        driver.verify_current_controls(original.checkpoint["channel_values"])
    services.effect_journal._state_updates = None
    try:
        with pytest.raises(OwaError):
            driver.verify_current_controls(original.checkpoint["channel_values"])
    finally:
        services.effect_journal._state_updates = coordinator
    assert services.model.calls == []


@pytest.mark.asyncio
async def test_second_owa_control_preserves_ordered_prior_control_lineage(controls):
    preflight, coordinator, bridge = controls
    _services, _engine, handle, actor, *_ = preflight
    first = bridge.stage(await prepare(preflight))
    proof = await coordinator.update(handle, first.request, actor)
    second = bridge.stage(
        await prepare(
            preflight,
            request_id=uuid4().hex * 2,
            base_reference=proof.result_reference,
            values={"value": 9},
        )
    )
    assert second.request.previous_controls == (first.request.request_id,)
    await coordinator.update(handle, second.request, actor)
    packet = (await checkpoint(controls)).checkpoint["channel_values"]
    assert json.loads(packet["receipt_bindings"])["controls"] == [
        first.request.request_id,
        second.request.request_id,
    ]


@pytest.mark.asyncio
async def test_private_native_continuation_uses_updated_data_and_keeps_prior_work(controls):
    preflight, coordinator, bridge = controls
    services, engine, handle, actor, _reference, original, _saver, plan = preflight
    candidate = bridge.stage(await prepare(preflight))
    await coordinator.update(handle, candidate.request, actor)
    await continue_updated_invocation(controls)


async def continue_updated_invocation(controls):
    preflight, _coordinator, _bridge = controls
    services, engine, handle, actor, _reference, original, _saver, plan = preflight
    wakes = {}
    for registration in services.timers.resumable_for_invocation(
        handle.invocation_id, plan.fingerprint
    ):
        wake = services.timers.wake(
            registration, "resume_requested", execution_lease=handle._execution_lease
        )
        wakes[wake.timer_key] = wake.as_value()
    services.invocations.update(handle, status="running")
    with authority_scope(actor):
        output = await engine.native.execute_plan(
            engine.executor,
            plan,
            {},
            metadata={
                "invocation_id": handle.invocation_id,
                "_invocation_handle": handle,
                "authority": actor.as_value(),
                "engine": "langgraph",
                "_timer_wakes": wakes,
                "_timer_wake": next(iter(wakes.values()), None),
            },
            invocation_id=handle.invocation_id,
            resume=True,
            _execution_fence=services.execution_ownership.native_fence(handle._execution_lease),
        )
    assert output == {"value": 8}
    current = await checkpoint(controls)
    before = json.loads(original.checkpoint["channel_values"]["snapshot"])
    after = json.loads(current.checkpoint["channel_values"]["snapshot"])
    for key, count in before["task_visits"].items():
        assert after["task_visits"][key] == count
    assert services.model.calls == []


@pytest.mark.asyncio
async def test_prepared_owa_control_rebuilds_identically_after_proposal_cache_loss(
    controls, monkeypatch
):
    preflight, coordinator, bridge = controls
    services, _engine, handle, actor, *_ = preflight
    candidate = bridge.stage(await prepare(preflight))
    connection_type = type(coordinator.connection)
    original = connection_type.execute

    def interrupted(connection, statement, parameters=()):
        if connection is coordinator.connection and "SET phase='dispatched'" in statement:
            raise RuntimeError("stopped before durable native dispatch")
        return original(connection, statement, parameters)

    monkeypatch.setattr(connection_type, "execute", interrupted)
    with pytest.raises(OwaError):
        await coordinator.update(handle, candidate.request, actor)
    monkeypatch.setattr(connection_type, "execute", original)
    bridge.proposals.clear()
    rebuilt = bridge.stage(await prepare(preflight, request_id=candidate.request.request_id))
    assert rebuilt.request == candidate.request
    await coordinator.update(handle, rebuilt.request, actor)
    assert services.model.calls == []


@pytest.mark.asyncio
async def test_committed_attested_control_recovers_without_any_staged_proposal(controls):
    preflight, coordinator, bridge = controls
    services, _engine, handle, actor, *_ = preflight
    candidate = bridge.stage(await prepare(preflight))

    async def interrupted(request, handle):
        await bridge.apply(request, handle)
        raise RuntimeError("stopped after attested native commit")

    coordinator._backends[bridge.storage_identity] = StateUpdateBackend(
        bridge.storage_identity, interrupted, bridge.inspect
    )
    with pytest.raises(OwaError):
        await coordinator.update(handle, candidate.request, actor)
    bridge.proposals.clear()
    coordinator._backends[bridge.storage_identity] = bridge.backend()
    await coordinator.update(handle, candidate.request, actor)
    assert services.model.calls == []


@pytest.mark.asyncio
async def test_mismatched_sdk_projection_rolls_back_before_native_proof_commit(controls):
    preflight, coordinator, bridge = controls
    services, _engine, handle, actor, *_ = preflight
    candidate = bridge.stage(await prepare(preflight))
    before = await checkpoint(controls)
    original = bridge.updater

    async def wrong(saver, config, request):
        result = await original(saver, config, request)
        written = await saver.aget_tuple(result)
        altered = dict(written.checkpoint)
        altered["channel_values"] = dict(altered["channel_values"])
        value = json.loads(altered["channel_values"]["snapshot"])
        value["data"] = {"value": 99}
        altered["channel_values"]["snapshot"] = json.dumps(value)
        await saver.aput(config, altered, written.metadata, {})
        return result

    bridge.updater = wrong
    with pytest.raises(OwaError):
        await coordinator.update(handle, candidate.request, actor)
    after = await checkpoint(controls)
    assert after.checkpoint["id"] == before.checkpoint["id"]
    assert bridge.inspect(candidate.request) is None
    assert services.model.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "preflight", [("sqlite", "channels"), ("postgresql", "channels")], indirect=True
)
async def test_attested_update_preserves_native_channel_values_and_postgres_blob_integrity(
    controls,
):
    preflight, coordinator, bridge = controls
    services, _engine, handle, actor, _reference, original, *_ = preflight
    candidate = bridge.stage(await prepare(preflight))
    await coordinator.update(handle, candidate.request, actor)
    current = await checkpoint(controls)
    assert (
        current.checkpoint["channel_values"]["native_channels"]
        == original.checkpoint["channel_values"]["native_channels"]
    )
    assert bridge.inspect(candidate.request) is not None
    assert services.model.calls == []


@pytest.mark.asyncio
async def test_staged_native_packets_have_an_aggregate_byte_ceiling(controls):
    preflight, _coordinator, bridge = controls
    proposal = await prepare(preflight)
    bridge.bounds.max_native_bytes = 1024
    with pytest.raises(OwaError) as failure:
        bridge.stage(proposal)
    assert failure.value.details["reason"] == "staged_control_bytes"
    assert bridge.proposals == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["prepared", "dispatched", "inspect"])
async def test_fresh_process_recovers_or_verifies_attested_owa_state(
    controls, tmp_path, monkeypatch, mode
):
    preflight, coordinator, bridge = controls
    services, _engine, handle, actor, *_ = preflight
    candidate = bridge.stage(await prepare(preflight))
    if mode == "prepared":
        original = type(coordinator.connection).execute

        def interrupted(connection, statement, parameters=()):
            if connection is coordinator.connection and "SET phase='dispatched'" in statement:
                raise RuntimeError("process boundary before dispatch")
            return original(connection, statement, parameters)

        monkeypatch.setattr(type(coordinator.connection), "execute", interrupted)
        with pytest.raises(OwaError):
            await coordinator.update(handle, candidate.request, actor)
        monkeypatch.setattr(type(coordinator.connection), "execute", original)
    elif mode == "dispatched":

        async def interrupted(request, handle):
            await bridge.apply(request, handle)
            raise RuntimeError("process boundary before acknowledgement")

        coordinator._backends[bridge.storage_identity] = StateUpdateBackend(
            bridge.storage_identity, interrupted, bridge.inspect
        )
        with pytest.raises(OwaError):
            await coordinator.update(handle, candidate.request, actor)
    else:
        await coordinator.update(handle, candidate.request, actor)
    data = await run_attestation_worker(controls, tmp_path, mode, candidate.request)
    assert data["model_calls"] == 0 and data["staged"] == 0
    assert bridge.inspect(candidate.request) is not None


async def run_attestation_worker(controls, tmp_path, mode, request):
    preflight, _coordinator, _bridge = controls
    services, _engine, handle, *_ = preflight
    if mode != "inspect":
        services.execution_ownership.release(handle._execution_lease)
        handle._execution_lease = None
    source, result = tmp_path / "attested-worker.json", tmp_path / "attested-result.json"
    source.write_text(
        json.dumps(
            {
                "config": services.config.model_dump(mode="json", exclude={"sandbox"}),
                "root": str(services.database_root) if services.database_root is not None else None,
                "invocation_id": handle.invocation_id,
                "request": request.descriptor,
                "mode": mode,
            }
        )
    )
    process = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).with_name("native_state_attestation_worker.py")),
            str(source),
            str(result),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        output, _ = await asyncio.to_thread(process.communicate, timeout=30)
        assert process.returncode == 0, output.decode(errors="replace")
    finally:
        if process.poll() is None:
            process.kill()
            await asyncio.to_thread(process.communicate, timeout=10)
    return json.loads(result.read_text())


@pytest.mark.asyncio
@pytest.mark.parametrize("committed", [False, True])
async def test_resolution_of_real_owa_control_never_repeats_native_write(controls, committed):
    preflight, coordinator, bridge = controls
    services, _engine, handle, actor, *_ = preflight
    candidate = bridge.stage(await prepare(preflight))

    async def interrupted(request, handle):
        if committed:
            await bridge.apply(request, handle)
        raise RuntimeError("stopped around native dispatch")

    coordinator._backends[bridge.storage_identity] = StateUpdateBackend(
        bridge.storage_identity, interrupted, bridge.inspect
    )
    with pytest.raises(OwaError):
        await coordinator.update(handle, candidate.request, actor)
    before = await checkpoint(controls)
    audit = await coordinator.resolve(
        handle,
        candidate.request,
        StateUpdateResolution(
            "resolve-1", "alice", "complete" if committed else "abandon_unknown", not committed
        ),
        actor,
    )
    assert services.effect_journal.get(resolution_identity(candidate.request.identity)) == audit
    assert (await checkpoint(controls)).checkpoint["id"] == before.checkpoint["id"]
    services.effect_journal.verify_invocation(handle.invocation_id, handle.workflow_fingerprint)
    if committed:
        await continue_updated_invocation(controls)
    else:
        with pytest.raises(OwaError) as failure:
            await prepare(preflight)
        assert failure.value.details["reason"] == "control_abandoned"
        with pytest.raises(OwaError):
            await coordinator.update(handle, candidate.request, actor)
    assert services.model.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("committed", [False, True])
async def test_fresh_process_resolves_and_reinspects_real_owa_control(
    controls, tmp_path, committed
):
    preflight, coordinator, bridge = controls
    services, _engine, handle, actor, *_ = preflight
    candidate = bridge.stage(await prepare(preflight))

    async def interrupted(request, handle):
        if committed:
            await bridge.apply(request, handle)
        raise RuntimeError("stopped before process handoff")

    coordinator._backends[bridge.storage_identity] = StateUpdateBackend(
        bridge.storage_identity, interrupted, bridge.inspect
    )
    with pytest.raises(OwaError):
        await coordinator.update(handle, candidate.request, actor)
    before = await checkpoint(controls)
    mode = "resolve-complete" if committed else "resolve-abandon"
    data = await run_attestation_worker(controls, tmp_path, mode, candidate.request)
    assert data["status"] == ("completed" if committed else "faulted")
    assert data["model_calls"] == 0 and data["staged"] == 0
    assert (await checkpoint(controls)).checkpoint["id"] == before.checkpoint["id"]
    again = await run_attestation_worker(controls, tmp_path, "inspect", candidate.request)
    assert again == data
    services.effect_journal.verify_invocation(handle.invocation_id, handle.workflow_fingerprint)
