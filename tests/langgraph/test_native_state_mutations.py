"""Actual SDK state updates keep writes, checkpoints and proof in one fenced unit."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import uuid
from dataclasses import asdict, replace
from pathlib import Path
from typing import TypedDict

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.errors import InvocationStateConflict, InvocationStateError
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.storage import open_storage
from open_workflow_agent.workflow import compile_workflow

pytest.importorskip("langgraph.graph")
from langgraph.graph import END, START, StateGraph
from open_workflow_agent_langgraph import native
from open_workflow_agent_langgraph.graph import LangGraphScopeExecutor
from open_workflow_agent_langgraph.native_state_mutations import (
    TABLE,
    NativeStateWrite,
    NativeStateWriteProof,
    apply_native_state_update,
    prepare_state_update_storage,
)


class State(TypedDict):
    value: int


def graph(saver):
    builder = StateGraph(State)
    builder.add_node("change", lambda state: {"value": state["value"] + 1})
    builder.add_edge(START, "change")
    builder.add_edge("change", END)
    return builder.compile(checkpointer=saver)


@pytest.fixture(params=["sqlite", "postgresql"])
async def runtime(request, tmp_path):
    config = RuntimeConfig()
    if request.param == "postgresql":
        datasource = os.getenv("OWA_TEST_POSTGRES_URL")
        if not datasource:
            pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
        config.persistence.datasource = datasource
    services = RuntimeServices(
        config, model=FakeModel(), database_root=None if request.param == "postgresql" else tmp_path
    )
    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "test",
                "name": "state-unit",
                "version": "1.0.0",
            },
            "do": [{"change": {"set": {"value": 1}}}],
        }
    )
    handle = services.invocations.create(
        engine="langgraph",
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    lease = services.execution_ownership.acquire(handle, seconds=300)
    fence = services.execution_ownership.native_fence(lease)
    database = services.engine_database_path("langgraph")
    saver_type = (
        native.AsyncPostgresSaver if request.param == "postgresql" else native.AsyncSqliteSaver
    )
    config = LangGraphScopeExecutor(plan, None, handle.invocation_id).checkpoint_config("/do", 1)
    try:
        async with saver_type.from_conn_string(database) as saver:
            await saver.setup()
            await prepare_state_update_storage(saver, postgresql=request.param == "postgresql")
            await graph(saver).ainvoke({"value": 1}, config)
            checkpoint = await saver.aget_tuple(config)
        write = NativeStateWrite(
            uuid.uuid4().hex * 2, "a" * 64, checkpoint.config["configurable"]["checkpoint_id"]
        )
        yield services, handle, plan, database, fence, write, request.param
    finally:
        services.close()


def snapshot(runtime):
    _services, _handle, plan, database, _fence, write, backend = runtime
    thread = LangGraphScopeExecutor(plan, None, _handle.invocation_id).checkpoint_config("/do", 1)
    connection = open_storage(database, "owa_langgraph")
    try:
        tables = (
            ("checkpoints", "checkpoint_blobs", "checkpoint_writes")
            if backend == "postgresql"
            else ("checkpoints", "writes")
        )
        result = {
            table: sorted(
                connection.execute(
                    f"SELECT * FROM {table} WHERE thread_id=?",
                    (thread["configurable"]["thread_id"],),
                ).fetchall(),
                key=repr,
            )
            for table in tables
        }
        result[TABLE] = connection.execute(
            f"SELECT * FROM {TABLE} WHERE identity=?", (write.identity,)
        ).fetchall()
        return result
    finally:
        connection.close()


async def update(saver, config):
    return await graph(saver).aupdate_state(config, {"value": 7}, as_node="change")


async def apply(runtime, *, callback=update, request=None, fence=None):
    _services, handle, plan, database, original_fence, original_request, _backend = runtime
    return await apply_native_state_update(
        database, fence or original_fence, handle, plan, request or original_request, callback
    )


@pytest.mark.asyncio
async def test_sdk_update_and_proof_commit_once_and_repeat_does_not_redispatch(runtime):
    before = snapshot(runtime)
    proof = await apply(runtime)
    after = snapshot(runtime)
    assert after != before and len(after[TABLE]) == 1
    assert proof.result_checkpoint != proof.request.base_checkpoint
    saver_type = (
        native.AsyncPostgresSaver if runtime[6] == "postgresql" else native.AsyncSqliteSaver
    )
    async with saver_type.from_conn_string(runtime[3]) as saver:
        config = LangGraphScopeExecutor(
            runtime[2], saver, runtime[1].invocation_id
        ).checkpoint_config("/do", 1)
        assert (await graph(saver).aget_state(config)).values == {"value": 7}

    async def forbidden(*_args):
        raise AssertionError("repeated update must not dispatch")

    assert await apply(runtime, callback=forbidden) == proof
    assert snapshot(runtime) == after
    with pytest.raises(InvocationStateConflict, match="request changed"):
        await apply(runtime, request=replace(proof.request, request_digest="b" * 64))
    assert snapshot(runtime) == after


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["after_sdk", "scope", "missing_result"])
async def test_partial_sdk_writes_and_provisional_proof_roll_back_together(runtime, failure):
    before = snapshot(runtime)

    async def broken(saver, config):
        result = await update(saver, config)
        if failure == "after_sdk":
            raise RuntimeError("interrupted after SDK checkpoint and pending writes")
        if failure == "scope":
            result["configurable"]["thread_id"] = "another-thread"
        else:
            result["configurable"]["checkpoint_id"] = str(uuid.uuid4())
        return result

    with pytest.raises(InvocationStateError):
        await apply(runtime, callback=broken)
    assert snapshot(runtime) == before


@pytest.mark.asyncio
async def test_stale_base_rejected_before_sdk_dispatch(runtime):
    await apply(runtime)
    before = snapshot(runtime)

    async def forbidden(*_args):
        raise AssertionError("stale CAS must not dispatch")

    request = replace(runtime[5], identity=uuid.uuid4().hex * 2)
    with pytest.raises(InvocationStateConflict, match="base is stale"):
        await apply(runtime, callback=forbidden, request=request)
    assert snapshot(runtime) == before


@pytest.mark.asyncio
async def test_concurrent_cas_has_one_winner_and_one_sdk_dispatch(runtime):
    calls = []

    async def counted(saver, config):
        calls.append(True)
        await asyncio.sleep(0.05)
        return await update(saver, config)

    second = replace(runtime[5], identity=uuid.uuid4().hex * 2)
    outcomes = await asyncio.gather(
        apply(runtime, callback=counted),
        apply(runtime, callback=counted, request=second),
        return_exceptions=True,
    )
    assert sum(isinstance(item, NativeStateWriteProof) for item in outcomes) == 1
    assert sum(isinstance(item, InvocationStateConflict) for item in outcomes) == 1
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["missing_checkpoint", "wrong_parent", "oversized_proof"])
async def test_existing_proof_requires_retained_matching_native_lineage(runtime, damage):
    proof = await apply(runtime)
    connection = open_storage(runtime[3], "owa_langgraph")
    thread = LangGraphScopeExecutor(runtime[2], None, runtime[1].invocation_id).checkpoint_config(
        "/do", 1
    )
    try:
        if damage == "missing_checkpoint":
            connection.execute(
                "DELETE FROM checkpoints WHERE thread_id=? AND checkpoint_id=?",
                (thread["configurable"]["thread_id"], proof.result_checkpoint),
            )
        elif damage == "wrong_parent":
            connection.execute(
                "UPDATE checkpoints SET parent_checkpoint_id=? "
                "WHERE thread_id=? AND checkpoint_id=?",
                (str(uuid.uuid4()), thread["configurable"]["thread_id"], proof.result_checkpoint),
            )
        else:
            connection.execute(
                f"UPDATE {TABLE} SET result_checkpoint=? WHERE identity=?",
                ("x" * 100_000, proof.request.identity),
            )
        connection.commit()
    finally:
        connection.close()
    before = snapshot(runtime)

    async def forbidden(*_args):
        raise AssertionError("damaged proof must not redispatch")

    with pytest.raises(InvocationStateError):
        await apply(runtime, callback=forbidden)
    assert snapshot(runtime) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("repeat", [False, True])
async def test_stale_owner_cannot_write_or_return_existing_proof(runtime, repeat):
    if repeat:
        await apply(runtime)
    before = snapshot(runtime)
    services, handle, *_ = runtime
    services.execution_ownership.connection.execute(
        "UPDATE invocation_ownership SET expires_at=1 WHERE invocation_id=?",
        (handle.invocation_id,),
    )
    services.execution_ownership.connection.commit()

    async def forbidden(*_args):
        raise AssertionError("stale owner must not dispatch")

    with pytest.raises(InvocationStateConflict):
        await apply(runtime, callback=forbidden)
    assert snapshot(runtime) == before


@pytest.mark.asyncio
async def test_expiry_during_sdk_update_rolls_back_all_native_evidence(runtime):
    before = snapshot(runtime)
    services, handle, *_ = runtime
    clock = services.execution_ownership._clock
    services.execution_ownership.connection.execute(
        f"UPDATE invocation_ownership SET expires_at={clock}+0.5 WHERE invocation_id=?",
        (handle.invocation_id,),
    )
    services.execution_ownership.connection.commit()

    async def slow(saver, config):
        result = await update(saver, config)
        await asyncio.sleep(0.8)
        return result

    with pytest.raises(InvocationStateConflict):
        await apply(runtime, callback=slow)
    assert snapshot(runtime) == before


@pytest.mark.asyncio
async def test_repeated_cancellation_drains_unit_and_repeat_observes_committed_proof(runtime):
    entered, finish = threading.Event(), threading.Event()

    async def paused(saver, config):
        result = await update(saver, config)
        entered.set()
        assert await asyncio.to_thread(finish.wait, 10)
        return result

    task = asyncio.create_task(apply(runtime, callback=paused))
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        task.cancel()
        await asyncio.sleep(0.02)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done()
    finally:
        finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    before = snapshot(runtime)

    async def forbidden(*_args):
        raise AssertionError("committed cancelled unit must not redispatch")

    await apply(runtime, callback=forbidden)
    assert snapshot(runtime) == before and len(before[TABLE]) == 1


@pytest.mark.asyncio
async def test_process_exit_after_sdk_writes_rolls_back_checkpoint_writes_and_proof(
    runtime, tmp_path
):
    _services, handle, plan, database, fence, request, _backend = runtime
    before = snapshot(runtime)
    source = tmp_path / "state-worker.json"
    ready = tmp_path / "state-worker.ready"
    source.write_text(
        json.dumps(
            {
                "database": database,
                "common": fence.database,
                "lease": asdict(fence.lease),
                "handle": {
                    key: value for key, value in asdict(handle).items() if not key.startswith("_")
                },
                "workflow": plan.source,
                "request": asdict(request),
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
    assert snapshot(runtime) == before
    # This private fixture starts a fresh local unit after the abandoned SQL
    # transaction. Public dispatched-intent reconciliation remains a separate gate.
    await apply(runtime)
    assert len(snapshot(runtime)[TABLE]) == 1


@pytest.mark.asyncio
async def test_mutation_never_creates_missing_proof_storage(runtime):
    connection = open_storage(runtime[3], "owa_langgraph")
    try:
        connection.execute(f"DROP TABLE {TABLE}")
        connection.commit()
    finally:
        connection.close()

    async def forbidden(*_args):
        raise AssertionError("missing setup must not dispatch")

    with pytest.raises(InvocationStateError):
        await apply(runtime, callback=forbidden)
    connection = open_storage(runtime[3], "owa_langgraph")
    try:
        query = (
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema=current_schema() AND table_name=?"
            if runtime[6] == "postgresql"
            else "SELECT name FROM sqlite_master WHERE name=?"
        )
        assert connection.execute(query, (TABLE,)).fetchone() is None
    finally:
        connection.close()
