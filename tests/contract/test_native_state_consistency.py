"""Corrupt actual engine-owned checkpoints without replacing their serializer."""

from __future__ import annotations

import asyncio
import copy
import importlib
import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.errors import InvocationStateError
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow
from open_workflow_agent_langgraph import LangGraphWorkflowEngine


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("scope", ["root", "nested"])
@pytest.mark.parametrize(
    "damage",
    [
        "clean",
        "outputs",
        "flags",
        "typed-stop",
        "typed-exit",
        "typed-end",
        "visits",
        "path",
        "scope-cursor",
        "unknown-scope",
        "missing-visits",
        "duplicate-keys",
        "context-binding",
        "workflow-identity",
        "workflow-definition",
    ],
)
async def test_langgraph_corrupt_root_or_nested_checkpoint_rejects_before_recovery_dispatch(
    tmp_path: Path, backend: str, scope: str, damage: str
) -> None:
    engine = LangGraphWorkflowEngine()
    if not engine.native.task_nodes_available:
        pytest.skip("independent native LangGraph checkpoint environment is required")
    from open_workflow_agent_langgraph.graph import LangGraphScopeExecutor

    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    config = (
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}})
        if datasource
        else RuntimeConfig()
    )
    calls: list[str] = []

    async def request(message: httpx.Request) -> httpx.Response:
        calls.append(message.url.path)
        return httpx.Response(200, json={"accepted": message.url.path})

    def services() -> RuntimeServices:
        result = RuntimeServices(
            config, model=FakeModel(), database_root=None if datasource else tmp_path
        )
        result.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(request)))
        result.tools.protocols = result.protocols
        return result

    body = [
        {
            "remember": {
                "call": "http",
                "with": {"method": "POST", "endpoint": "https://service.test/once"},
            }
        },
        {"pause": {"wait": {"seconds": 300}}},
        {
            "next": {
                "call": "http",
                "with": {"method": "POST", "endpoint": "https://service.test/next"},
            }
        },
    ]
    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "test",
                "name": "consistency",
                "version": "1.0.0",
            },
            "do": body if scope == "root" else [{"outer": {"do": body}}],
        }
    )
    first = services()
    await engine.initialize(first)
    handle = first.invocations.create(
        engine="langgraph",
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    running = asyncio.create_task(engine.invoke(plan, handle, {"original": True}))
    try:
        async with asyncio.timeout(20):
            while handle.status != "waiting":
                if running.done():
                    pytest.fail(str(running.result()))
                await asyncio.sleep(0.005)
        waiting = await asyncio.wait_for(asyncio.shield(running), 20)
        assert waiting.status == "waiting", waiting.error
        assert handle.invocation_id not in engine._active
        assert handle.invocation_id not in engine._ownership_tasks
        assert handle._execution_lease is None
        assert calls == ["/once"]
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()
        first.close()

    restarted = services()
    recovered = LangGraphWorkflowEngine()
    await recovered.initialize(restarted)
    loaded = restarted.invocations.get(handle.invocation_id)
    assert loaded is not None and loaded.status == "waiting"
    reference = "/do" if scope == "root" else "/do/0/outer/do"

    async def corrupt(saver: Any) -> None:
        driver = LangGraphScopeExecutor(plan, saver, handle.invocation_id)
        saved = await saver.aget_tuple(driver.checkpoint_config(reference, 1))
        assert saved is not None
        checkpoint = copy.deepcopy(saved.checkpoint)
        channels = checkpoint["channel_values"]
        snapshot = json.loads(channels["snapshot"])
        counters = json.loads(channels["counters"])
        channel = "snapshot"
        if damage == "clean":
            return
        if damage == "outputs":
            snapshot["outputs"] = ["private-checkpoint-sentinel"]
        elif damage == "flags":
            snapshot["stop"] = "false"
        elif damage == "typed-stop":
            snapshot["stop"] = True
        elif damage == "typed-exit":
            snapshot["scope_exit"] = True
        elif damage == "typed-end":
            snapshot["ended"] = True
        elif damage == "visits":
            snapshot["task_visits"] = {"/forged": -1}
        elif damage == "path":
            snapshot["execution_path"] = [["/forged", True]]
        elif damage == "scope-cursor":
            channel = "counters"
            counters[reference] = 900001
        elif damage == "unknown-scope":
            channel = "counters"
            counters["/forged"] = 1
        elif damage == "missing-visits":
            del snapshot["task_visits"]
        elif damage == "context-binding":
            snapshot["variables"]["context"] = {"private-checkpoint-sentinel": True}
        elif damage == "workflow-identity":
            snapshot["variables"]["workflow"]["id"] = "private-checkpoint-sentinel"
        elif damage == "workflow-definition":
            snapshot["variables"]["workflow"]["definition"]["document"]["name"] = (
                "private-checkpoint-sentinel"
            )
        if damage == "duplicate-keys":
            channels["snapshot"] = channels["snapshot"][:-1] + ',"stop":false}'
        else:
            channels[channel] = json.dumps(snapshot if channel == "snapshot" else counters)
        thread = saved.config["configurable"]["thread_id"]
        namespace = saved.config["configurable"].get("checkpoint_ns", "")
        # Follow the installed SDK's storage layout: PostgreSQL inlines
        # primitive JSON-text channels in checkpoint JSONB; SQLite serializes
        # the whole checkpoint. Do not assume every channel has a blob row.
        if backend == "postgresql":
            json_types = importlib.import_module("psycopg.types.json")

            updated = await saver.conn.execute(
                "UPDATE checkpoints SET checkpoint = "
                "jsonb_set(checkpoint, ARRAY['channel_values', %s], %s, false) "
                "WHERE thread_id = %s AND checkpoint_ns = %s AND checkpoint_id = %s",
                (
                    channel,
                    json_types.Jsonb(channels[channel]),
                    thread,
                    namespace,
                    checkpoint["id"],
                ),
            )
        else:
            encoding, payload = saver.serde.dumps_typed(checkpoint)
            updated = await saver.conn.execute(
                "UPDATE checkpoints SET type = ?, checkpoint = ? "
                "WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?",
                (encoding, payload, thread, namespace, checkpoint["id"]),
            )
        assert updated.rowcount == 1
        await updated.close()
        await saver.conn.commit()
        altered = await saver.aget_tuple(saved.config)
        assert altered is not None
        assert altered.checkpoint["channel_values"][channel] == channels[channel]

    try:
        await recovered.native._with_saver(corrupt)
        before = loaded._persisted_payload
        if damage == "clean":
            result = await asyncio.wait_for(recovered.resume(loaded, {}, plan), 20)
            assert result.status == "completed" and calls == ["/once", "/next"]
        else:
            with pytest.raises(InvocationStateError) as failure:
                await asyncio.wait_for(recovered.resume(loaded, {}, plan), 20)
            assert "private-checkpoint-sentinel" not in json.dumps(failure.value.as_dict())
            assert calls == ["/once"]
            saved = restarted.invocations.get(handle.invocation_id)
            assert saved is not None and saved._persisted_payload == before
            assert loaded._persisted_payload == before and loaded.status == "waiting"
    finally:
        await recovered.shutdown()
        restarted.close()
