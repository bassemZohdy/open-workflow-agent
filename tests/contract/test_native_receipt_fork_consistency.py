"""A waiting fork remains recoverable after its sibling saves an action receipt."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import httpx
import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.errors import InvocationStateError
from open_workflow_agent.persistence import ExecutionHandle
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow
from open_workflow_agent_adk import AdkWorkflowEngine
from open_workflow_agent_langgraph import LangGraphWorkflowEngine


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "engine_name,engine_type", [("adk", AdkWorkflowEngine), ("langgraph", LangGraphWorkflowEngine)]
)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("damage", ["clean", "missing"])
async def test_waiting_fork_retains_a_later_sibling_receipt_before_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: Any,
    backend: str,
    damage: str,
) -> None:
    engine = engine_type()
    available = (
        engine.native.available if engine_name == "adk" else engine.native.task_nodes_available
    )
    if not available:
        pytest.skip("independent native engine environment is required")
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    config = (
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}})
        if datasource
        else RuntimeConfig()
    )
    calls: list[str] = []
    handle: ExecutionHandle | None = None
    receipt_saved = asyncio.Event()

    async def request(message: httpx.Request) -> httpx.Response:
        calls.append(message.url.path)
        if message.url.path == "/once":
            assert handle is not None
            async with asyncio.timeout(10):
                while handle.status != "waiting":
                    await asyncio.sleep(0.005)
        return httpx.Response(200, json={"accepted": True})

    def services() -> RuntimeServices:
        result = RuntimeServices(
            config, model=FakeModel(), database_root=None if datasource else tmp_path
        )
        result.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(request)))
        result.tools.protocols = result.protocols
        return result

    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "test",
                "name": "parallel-receipt",
                "version": "1.0.0",
            },
            "do": [
                {
                    "parallel": {
                        "fork": {
                            "branches": [
                                {"pause": {"wait": {"seconds": 300}}},
                                {
                                    "action": {
                                        "call": "http",
                                        "with": {
                                            "method": "POST",
                                            "endpoint": "https://service.test/once",
                                        },
                                    }
                                },
                            ]
                        }
                    }
                },
                {
                    "next": {
                        "call": "http",
                        "with": {"method": "POST", "endpoint": "https://service.test/next"},
                    }
                },
            ],
        }
    )
    first = services()
    existing = {
        row[0]
        for row in first.effect_journal.connection.execute(
            "SELECT identity FROM workflow_effects"
        ).fetchall()
    }
    await engine.initialize(first)
    if engine_name == "adk":
        notify = engine.executor._notify_receipt

        async def notified(state: Any, identity: Any, receipt: Any) -> None:
            await notify(state, identity, receipt)
            # ADK's observer has committed its actual SDK event by return.
            receipt_saved.set()

        monkeypatch.setattr(engine.executor, "_notify_receipt", notified)
    else:
        from open_workflow_agent_langgraph.graph import LangGraphScopeExecutor

        run_scope = LangGraphScopeExecutor.run_scope
        action_scope = next(
            scope.reference
            for scope in plan.scopes
            if any(task.name == "action" for task in scope.tasks)
        )

        async def completed_scope(self: Any, *args: Any, **kwargs: Any) -> None:
            await run_scope(self, *args, **kwargs)
            # Native graph return drains SDK checkpoint persistence.
            if kwargs["prefix"] == action_scope:
                receipt_saved.set()

        monkeypatch.setattr(LangGraphScopeExecutor, "run_scope", completed_scope)
    handle = first.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    running = asyncio.create_task(engine.invoke(plan, handle, {}))
    receipt_key = ""
    try:
        await asyncio.wait_for(receipt_saved.wait(), 20)
        assert handle.status == "waiting" and calls == ["/once"]
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        keys = {
            row[0]
            for row in first.effect_journal.connection.execute(
                "SELECT identity FROM workflow_effects"
            ).fetchall()
        } - existing
        assert len(keys) == 1
        receipt_key = keys.pop()
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()
        first.close()
    restarted = services()
    recovered = engine_type()
    await recovered.initialize(restarted)
    loaded = restarted.invocations.get(handle.invocation_id)
    assert loaded is not None and loaded.status == "waiting"
    try:
        if damage == "missing":
            connection = restarted.effect_journal.connection
            updated = connection.execute(
                "DELETE FROM workflow_effects WHERE identity = ?", (receipt_key,)
            )
            assert updated.rowcount == 1
            connection.commit()
        before = loaded._persisted_payload
        if damage == "clean":
            result = await asyncio.wait_for(recovered.resume(loaded, {}, plan), 20)
            assert result.status == "completed" and calls == ["/once", "/next"]
        else:
            with pytest.raises(InvocationStateError):
                await asyncio.wait_for(recovered.resume(loaded, {}, plan), 20)
            assert calls == ["/once"]
            saved = restarted.invocations.get(handle.invocation_id)
            assert saved is not None and saved._persisted_payload == before
            assert loaded._persisted_payload == before and loaded.status == "waiting"
    finally:
        await recovered.shutdown()
        restarted.close()
