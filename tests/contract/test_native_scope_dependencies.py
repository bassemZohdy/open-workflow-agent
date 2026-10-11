"""Completed scope loss differs from a cancelled child without a checkpoint."""

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
@pytest.mark.parametrize("scenario", ["nested", "repeated", "cancelled-child"])
@pytest.mark.parametrize("damage", ["clean", "deleted", "changed"])
async def test_completed_native_scope_dependencies_reconcile_before_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str, scenario: str, damage: str
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
    cancelled_entered = asyncio.Event()

    async def request(message: httpx.Request) -> httpx.Response:
        calls.append(message.url.path)
        if scenario == "cancelled-child" and message.url.path == "/once":
            await asyncio.wait_for(cancelled_entered.wait(), 10)
        return httpx.Response(200, json={"accepted": True})

    def services() -> RuntimeServices:
        result = RuntimeServices(
            config, model=FakeModel(), database_root=None if datasource else tmp_path
        )
        result.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(request)))
        result.tools.protocols = result.protocols
        return result

    call = {"call": "http", "with": {"method": "POST", "endpoint": "https://service.test/once"}}
    first_task: dict[str, Any]
    if scenario == "cancelled-child":
        first_task = {
            "race": {
                "fork": {
                    "compete": True,
                    "branches": [{"slow": {"set": {"unexpected": True}}}, {"remember": call}],
                }
            }
        }
    elif scenario == "repeated":
        first_task = {
            "repeat": {"for": {"each": "item", "in": "${ .items }"}, "do": [{"remember": call}]}
        }
    else:
        first_task = {"outer": {"do": [{"remember": call}]}}
    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "test",
                "name": "scope-dependencies",
                "version": "1.0.0",
            },
            "do": [
                first_task,
                {"pause": {"wait": {"seconds": 300}}},
                {
                    "next": {
                        "call": "http",
                        "with": {"method": "POST", "endpoint": "https://service.test/next"},
                    }
                },
            ],
        }
    )
    completed_ref = next(
        scope.reference
        for scope in plan.scopes
        if any(task.name == "remember" for task in scope.tasks)
    )
    cancelled_ref: str | None = None
    if scenario == "cancelled-child":
        cancelled_ref = next(
            scope.reference
            for scope in plan.scopes
            if any(task.name == "slow" for task in scope.tasks)
        )
        run_scope = LangGraphScopeExecutor._run_native_scope

        async def interrupted_initial_checkpoint(self: Any, *args: Any, **kwargs: Any) -> None:
            if kwargs["prefix"] == cancelled_ref:
                # The real scope entry advances its cursor before its first
                # SDK read/checkpoint. Pause exactly at that cancellation window.
                self.counters[cancelled_ref] = self.counters.get(cancelled_ref, 0) + 1
                cancelled_entered.set()
                await asyncio.Event().wait()
            await run_scope(self, *args, **kwargs)

        monkeypatch.setattr(
            LangGraphScopeExecutor, "_run_native_scope", interrupted_initial_checkpoint
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
    running = asyncio.create_task(engine.invoke(plan, handle, {"items": [1, 2]}))
    before_calls = ["/once", "/once"] if scenario == "repeated" else ["/once"]
    try:
        async with asyncio.timeout(20):
            while handle.status != "waiting":
                if running.done():
                    pytest.fail(str(running.result()))
                await asyncio.sleep(0.005)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        assert calls == before_calls
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

    async def corrupt(saver: Any) -> None:
        driver = LangGraphScopeExecutor(plan, saver, handle.invocation_id)
        root_config = driver.checkpoint_config("/do", 1)
        root_saved = await saver.aget_tuple(root_config)
        assert root_saved is not None
        if cancelled_ref is not None:
            assert (
                json.loads(root_saved.checkpoint["channel_values"]["counters"])[cancelled_ref] == 1
            )
            assert await saver.aget_tuple(driver.checkpoint_config(cancelled_ref, 1)) is None
        # The first occurrence remains a dependency after a later loop visit.
        saved = await saver.aget_tuple(driver.checkpoint_config(completed_ref, 1))
        assert saved is not None and saved.checkpoint["channel_values"]["next_node"] == "__end__"
        if damage == "clean":
            return
        thread = saved.config["configurable"]["thread_id"]
        if damage == "deleted":
            await saver.adelete_thread(thread)
            assert await saver.aget_tuple(driver.checkpoint_config(completed_ref, 1)) is None
        else:
            checkpoint = copy.deepcopy(saved.checkpoint)
            channels = checkpoint["channel_values"]
            snapshot = json.loads(channels["snapshot"])
            snapshot["data"] = {"private-scope-sentinel": True}
            channels["snapshot"] = json.dumps(snapshot)
            bindings = json.loads(channels["receipt_bindings"])
            # The child is internally consistent. Its parent must still reject
            # a different completed child, rather than rely on the child's checksum.
            bindings["state_digest"] = driver._state_digest(channels)
            channels["receipt_bindings"] = json.dumps(bindings)
            namespace = saved.config["configurable"].get("checkpoint_ns", "")
            if backend == "postgresql":
                json_types = importlib.import_module("psycopg.types.json")
                updated = await saver.conn.execute(
                    "UPDATE checkpoints SET checkpoint = "
                    "jsonb_set(checkpoint, ARRAY['channel_values'], %s, false) "
                    "WHERE thread_id = %s AND checkpoint_ns = %s AND checkpoint_id = %s",
                    (json_types.Jsonb(channels), thread, namespace, checkpoint["id"]),
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
            assert altered is not None and altered.checkpoint["channel_values"] == channels
        assert await saver.aget_tuple(root_saved.config) is not None

    try:
        await recovered.native._with_saver(corrupt)
        before = loaded._persisted_payload
        if damage == "clean":
            result = await asyncio.wait_for(recovered.resume(loaded, {}, plan), 20)
            assert result.status == "completed" and calls == before_calls + ["/next"]
        else:
            with pytest.raises(InvocationStateError) as failure:
                await asyncio.wait_for(recovered.resume(loaded, {}, plan), 20)
            assert "private-scope-sentinel" not in json.dumps(failure.value.as_dict())
            assert calls == before_calls
            saved_handle = restarted.invocations.get(handle.invocation_id)
            assert saved_handle is not None and saved_handle._persisted_payload == before
            assert loaded._persisted_payload == before and loaded.status == "waiting"
    finally:
        await recovered.shutdown()
        restarted.close()
