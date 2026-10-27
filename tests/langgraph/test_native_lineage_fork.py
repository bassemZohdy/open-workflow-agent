"""Actual native multi-scope forks commit atomically without executing OWA tasks."""

from __future__ import annotations

import asyncio
import json
import threading
from contextlib import suppress
from dataclasses import replace
from uuid import uuid4

import pytest
from open_workflow_agent.config import NativeStateConfig
from open_workflow_agent.errors import InvocationStateConflict, OwaError
from open_workflow_agent.storage import open_storage
from open_workflow_agent.workflow import ExecutionState, WorkflowExecutor
from test_native_state_preflight import counts
from test_native_state_preflight import preflight as _preflight

pytest.importorskip("langgraph")
from open_workflow_agent_langgraph.graph import LangGraphScopeExecutor, _json_identity, _node_name
from open_workflow_agent_langgraph.native_lineage_fork import (
    TABLE,
    NativeForkPacket,
    NativeForkProof,
    NativeForkProposal,
    apply_native_lineage_fork,
    prepare_lineage_storage,
)
from open_workflow_agent_langgraph.native_lineage_preflight import prepare_native_lineage

preflight = _preflight


@pytest.fixture
async def fork(preflight):
    # Parametrization of the reused preflight is provided below. This fixture
    # manually reserves a new common handle, not a production lineage admission.
    services, engine, source, actor, reference, _item, saver_type, plan = preflight
    prepared = await prepare_native_lineage(
        services,
        source,
        actor,
        kind="replay",
        target_invocation_id=uuid4().hex,
        request_id=uuid4().hex * 2,
        source_reference=reference,
    )
    target = services.invocations.create(
        engine="langgraph",
        session_id=source.session_id,
        user_id=source.user_id,
        owner_principal=source.owner_principal,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    prepared = replace(
        prepared, request=replace(prepared.request, target_invocation_id=target.invocation_id)
    )
    packets = []
    for item in prepared.snapshots:
        value = json.loads(item.native_json)
        snapshot = json.loads(value["snapshot"])
        snapshot["variables"]["workflow"]["id"] = target.invocation_id
        value["snapshot"] = _json_identity(snapshot)
        packets.append(NativeForkPacket(item.reference, item.occurrence, _json_identity(value)))
    proposal = NativeForkProposal(prepared, tuple(packets))
    lease = services.execution_ownership.acquire(target, seconds=300)
    target._execution_lease = lease
    fence = services.execution_ownership.native_fence(lease)
    database = services.engine_database_path("langgraph", create_namespace=False)
    async with saver_type.from_conn_string(database) as saver:
        await prepare_lineage_storage(saver, postgresql=database.startswith("postgres"))
    try:
        yield (
            services,
            engine,
            source,
            target,
            plan,
            database,
            fence,
            proposal,
            saver_type,
            preflight,
        )
    finally:
        with suppress(InvocationStateConflict):
            services.execution_ownership.release(lease)


pytestmark = pytest.mark.parametrize(
    "preflight", [("sqlite", "lineage"), ("postgresql", "lineage")], indirect=True
)


async def write(fork, saver, configs):
    services, _engine, _source, target, plan, *_ = fork
    proposal = fork[7]
    driver = LangGraphScopeExecutor(plan, saver, target.invocation_id)
    executor = WorkflowExecutor(services.catalog, services=services)
    result = []
    for config, packet in zip(configs, proposal.packets[: len(configs)], strict=True):
        scope = next(scope for scope in plan.scopes if scope.reference == packet.reference)
        # aupdate_state routes via the supplied next_node but never calls this
        # OWA task. A future coordinator must remap/attest common dependencies.
        graph = driver.compile_scope(scope, executor, ExecutionState(data=None))
        result.append(
            await graph.aupdate_state(
                config, json.loads(packet.native_json), as_node=_node_name(scope.tasks[0])
            )
        )
    return tuple(result)


async def apply(fork, *, callback=None, proposal=None, fence=None, bounds=None):
    _, _, _, target, plan, database, original_fence, original, *_ = fork

    async def default(saver, configs):
        return await write(fork, saver, configs)

    return await apply_native_lineage_fork(
        database,
        fence or original_fence,
        target,
        plan,
        proposal or original,
        callback or default,
        bounds=bounds or NativeStateConfig(timeout_seconds=30),
    )


def snapshot(fork):
    _, _, source, target, plan, database, *_ = fork
    connection = open_storage(database, "owa_langgraph")
    try:
        tables = (
            ("checkpoints", "checkpoint_blobs", "checkpoint_writes")
            if connection.is_postgresql
            else ("checkpoints", "writes")
        )
        result = {}
        for owner in (source, target):
            for packet in fork[7].packets:
                thread = LangGraphScopeExecutor.scope_thread_id(
                    owner.invocation_id, plan.fingerprint, packet.reference, packet.occurrence
                )
                for table in tables:
                    result[(owner.invocation_id, packet.reference, table)] = sorted(
                        connection.execute(
                            f"SELECT * FROM {table} WHERE thread_id=?", (thread,)
                        ).fetchall(),
                        key=repr,
                    )
        result[TABLE] = connection.execute(
            f"SELECT * FROM {TABLE} WHERE target_id=?", (target.invocation_id,)
        ).fetchall()
        return result
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_atomic_scope_closure_is_new_and_repeat_never_redispatches(fork):
    services, _, source, _, _, database, _, proposal, saver_type, fixture = fork
    before, common = snapshot(fork), counts(fixture)
    proof = await apply(fork)
    assert isinstance(proof, NativeForkProof) and len(proof.checkpoints) == 2
    after = snapshot(fork)
    assert before != after and len(after[TABLE]) == 1
    assert all(
        after[key] == value
        for key, value in before.items()
        if isinstance(key, tuple) and key[0] == source.invocation_id
    )
    assert counts(fixture) == common and services.model.calls == []
    async with saver_type.from_conn_string(database) as saver:
        for packet, retained in zip(proposal.packets, proof.checkpoints, strict=True):
            config = LangGraphScopeExecutor(fork[4], None, fork[3].invocation_id).checkpoint_config(
                packet.reference, packet.occurrence
            )
            item = await saver.aget_tuple(config)
            assert item.checkpoint["id"] == retained.checkpoint_id and item.parent_config is None
            assert (
                json.loads(item.checkpoint["channel_values"]["snapshot"])["variables"]["workflow"][
                    "id"
                ]
                == fork[3].invocation_id
            )

    async def forbidden(*args):
        raise AssertionError("repeat dispatched")

    assert await apply(fork, callback=forbidden) == proof
    assert snapshot(fork) == after


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage", ["after_first", "after_all", "wrong_scope", "missing_result", "wrong_values"]
)
async def test_partial_scope_and_invalid_result_roll_back_all_native_evidence(fork, damage):
    before = snapshot(fork)

    async def broken(saver, configs):
        if damage == "after_first":
            await write(fork, saver, configs[:1])
            raise RuntimeError("interrupted")
        result = await write(fork, saver, configs)
        if damage == "after_all":
            raise RuntimeError("interrupted")
        if damage == "wrong_scope":
            configs[0]["configurable"]["thread_id"] = "wrong"
            result[0]["configurable"]["thread_id"] = "wrong"
        if damage == "missing_result":
            return result[:1]
        if damage == "wrong_values":
            config = result[0]
            item = await saver.aget_tuple(config)
            item.checkpoint["channel_values"]["snapshot"] = "{}"
            await saver.aput(
                {
                    "configurable": {
                        key: value
                        for key, value in config["configurable"].items()
                        if key != "checkpoint_id"
                    }
                },
                item.checkpoint,
                item.metadata,
                item.checkpoint["channel_versions"],
            )
        return result

    with pytest.raises(OwaError):
        await apply(fork, callback=broken)
    assert snapshot(fork) == before


@pytest.mark.asyncio
async def test_concurrent_requests_have_one_native_fork_and_one_dispatch(fork):
    calls = []

    async def counted(saver, configs):
        calls.append(True)
        await asyncio.sleep(0.05)
        return await write(fork, saver, configs)

    first, second = await asyncio.gather(
        apply(fork, callback=counted), apply(fork, callback=counted)
    )
    assert first == second and len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("repeat", [False, True])
async def test_stale_target_owner_cannot_fork_or_recover_proof(fork, repeat):
    if repeat:
        await apply(fork)
    before = snapshot(fork)
    services, _, _, target, *_ = fork
    services.execution_ownership.connection.execute(
        "UPDATE invocation_ownership SET expires_at=1 WHERE invocation_id=?",
        (target.invocation_id,),
    )
    services.execution_ownership.connection.commit()

    async def forbidden(*args):
        raise AssertionError("stale owner dispatched")

    with pytest.raises(OwaError):
        await apply(fork, callback=forbidden)
    assert snapshot(fork) == before


@pytest.mark.asyncio
async def test_expiry_during_fork_rolls_back_all_scopes_and_proof(fork):
    services, _, _, target, *_ = fork
    connection = services.execution_ownership.connection
    clock = services.execution_ownership._clock
    connection.execute(
        f"UPDATE invocation_ownership SET expires_at={clock}+2 WHERE invocation_id=?",
        (target.invocation_id,),
    )
    connection.commit()
    before = snapshot(fork)

    completed = []

    async def slow(saver, configs):
        result = await write(fork, saver, configs)
        completed.append(True)
        await asyncio.sleep(2.3)
        return result

    with pytest.raises(OwaError):
        await apply(fork, callback=slow)
    assert snapshot(fork) == before and completed == [True]


@pytest.mark.asyncio
async def test_repeated_cancellation_drains_commit_before_ownership_is_released(fork):
    entered, finish = threading.Event(), threading.Event()

    async def paused(saver, configs):
        result = await write(fork, saver, configs)
        entered.set()
        assert await asyncio.to_thread(finish.wait, 10)
        return result

    task = asyncio.create_task(apply(fork, callback=paused))
    assert await asyncio.to_thread(entered.wait, 10)
    task.cancel()
    await asyncio.sleep(0.02)
    task.cancel()
    assert not task.done()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    async def forbidden(*args):
        raise AssertionError("canceled committed fork was dispatched twice")

    assert isinstance(await apply(fork, callback=forbidden), NativeForkProof)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage",
    [
        "source_checkpoint",
        "target_checkpoint",
        "target_parent",
        "oversized_proof",
        "wrong_coordinate",
    ],
)
async def test_missing_changed_or_oversized_evidence_cannot_dispatch_or_recover(fork, damage):
    services, _, source, target, plan, database, _, proposal, *_ = fork
    proof = None if damage == "source_checkpoint" else await apply(fork)
    connection = open_storage(database, "owa_langgraph")
    try:
        owner = source if proof is None else target
        packet = proposal.packets[0]
        thread = LangGraphScopeExecutor.scope_thread_id(
            owner.invocation_id, plan.fingerprint, packet.reference, packet.occurrence
        )
        key = (
            proposal.preflight.snapshots[0].checkpoint_id
            if proof is None
            else proof.checkpoints[0].checkpoint_id
        )
        if damage in {"source_checkpoint", "target_checkpoint"}:
            connection.execute(
                "DELETE FROM checkpoints WHERE thread_id=? AND checkpoint_id=?", (thread, key)
            )
        elif damage == "target_parent":
            connection.execute(
                "UPDATE checkpoints SET parent_checkpoint_id=? "
                "WHERE thread_id=? AND checkpoint_id=?",
                (str(uuid4()), thread, key),
            )
        elif damage == "oversized_proof":
            connection.execute(
                f"UPDATE {TABLE} SET result_json=? WHERE identity=?",
                ("x" * 100_000, proposal.preflight.request.identity.key),
            )
        else:
            row = connection.execute(
                f"SELECT result_json FROM {TABLE} WHERE identity=?",
                (proposal.preflight.request.identity.key,),
            ).fetchone()
            corrupted = json.loads(row[0])
            corrupted[0][1] = True
            connection.execute(
                f"UPDATE {TABLE} SET result_json=? WHERE identity=?",
                (json.dumps(corrupted), proposal.preflight.request.identity.key),
            )
        connection.commit()
    finally:
        connection.close()
    before = snapshot(fork)

    async def forbidden(*args):
        raise AssertionError("corrupt evidence dispatched")

    with pytest.raises(OwaError):
        await apply(fork, callback=forbidden)
    assert snapshot(fork) == before and services.model.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["request", "packet", "identity"])
async def test_conflicting_commands_and_existing_target_never_create_another_fork(fork, changed):
    await apply(fork)
    original = fork[7]
    if changed == "packet":
        packet = original.packets[0]
        value = json.loads(packet.native_json)
        value["snapshot"] = "{}"
        candidate = replace(
            original,
            packets=(replace(packet, native_json=_json_identity(value)), *original.packets[1:]),
        )
    else:
        field = "request_id" if changed == "identity" else "policy_digest"
        request = replace(original.preflight.request, **{field: "a" * 64})
        candidate = replace(original, preflight=replace(original.preflight, request=request))
    before = snapshot(fork)

    async def forbidden(*args):
        raise AssertionError("conflicting command dispatched")

    with pytest.raises(OwaError):
        await apply(fork, proposal=candidate, callback=forbidden)
    assert snapshot(fork) == before


@pytest.mark.asyncio
async def test_source_pending_task_writes_are_never_inherited(fork):
    services, _, source, target, plan, database, _, proposal, saver_type, fixture = fork
    root = LangGraphScopeExecutor(plan, None, source.invocation_id).checkpoint_config("/do", 1)
    root["configurable"]["checkpoint_id"] = proposal.preflight.snapshots[0].checkpoint_id
    async with saver_type.from_conn_string(database) as saver:
        await saver.aput_writes(
            root, [("source_only_canary", {"origin": "source-only"})], str(uuid4())
        )
    prepared = await prepare_native_lineage(
        services,
        source,
        fixture[3],
        kind="replay",
        target_invocation_id=uuid4().hex,
        request_id=proposal.preflight.request.request_id,
        source_reference=proposal.preflight.request.source_reference,
    )
    prepared = replace(
        prepared, request=replace(prepared.request, target_invocation_id=target.invocation_id)
    )
    assert json.loads(prepared.snapshots[0].metadata_json)["has_pending_writes"] is True
    candidate = replace(proposal, preflight=prepared)
    await apply(fork, proposal=candidate)
    async with saver_type.from_conn_string(database) as saver:
        for packet in proposal.packets:
            config = LangGraphScopeExecutor(plan, None, target.invocation_id).checkpoint_config(
                packet.reference, packet.occurrence
            )
            item = await saver.aget_tuple(config)
            assert all(
                channel != "source_only_canary" for _, channel, _ in item.pending_writes or ()
            )
    assert services.model.calls == []


@pytest.mark.asyncio
async def test_scope_and_packet_limits_reject_before_sdk_callback(fork):
    original = fork[7]
    before = snapshot(fork)

    async def forbidden(*args):
        raise AssertionError("invalid proposal dispatched")

    for candidate, bounds in [
        (replace(original, packets=original.packets[:1]), None),
        (original, NativeStateConfig(max_history=1, max_page_size=1)),
        (original, NativeStateConfig(max_native_bytes=1024)),
    ]:
        with pytest.raises(OwaError):
            await apply(fork, proposal=candidate, callback=forbidden, bounds=bounds)
    assert snapshot(fork) == before


@pytest.mark.asyncio
async def test_source_fork_quota_rejects_before_callback(fork):
    request = fork[7].preflight.request
    connection = open_storage(fork[5], "owa_langgraph")
    try:
        for _ in range(64):
            connection.execute(
                f"INSERT INTO {TABLE} VALUES (?,?,?,?,?,?,?,?)",
                (
                    uuid4().hex * 2,
                    uuid4().hex,
                    request.source_invocation_id,
                    request.workflow_fingerprint,
                    "a" * 64,
                    "b" * 64,
                    "c" * 64,
                    "[]",
                ),
            )
        connection.commit()
    finally:
        connection.close()
    before = snapshot(fork)

    async def forbidden(*args):
        raise AssertionError("fork quota dispatched")

    with pytest.raises(OwaError) as failure:
        await apply(fork, callback=forbidden)
    assert failure.value.details["reason"] == "fork_limit"
    assert snapshot(fork) == before


@pytest.mark.asyncio
async def test_orphan_target_writes_cannot_be_adopted_as_an_empty_target(fork):
    config = LangGraphScopeExecutor(fork[4], None, fork[3].invocation_id).checkpoint_config(
        "/do", 1
    )
    config["configurable"]["checkpoint_id"] = str(uuid4())
    async with fork[8].from_conn_string(fork[5]) as saver:
        await saver.aput_writes(config, [("orphan", {"value": "unknown"})], str(uuid4()))
    before = snapshot(fork)

    async def forbidden(*args):
        raise AssertionError("orphan state was adopted")

    with pytest.raises(OwaError):
        await apply(fork, callback=forbidden)
    assert snapshot(fork) == before
