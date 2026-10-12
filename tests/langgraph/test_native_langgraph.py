from __future__ import annotations

import asyncio

import httpx
import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow
from open_workflow_agent_langgraph import LangGraphWorkflowEngine
from open_workflow_agent_langgraph.native import LANGGRAPH_AVAILABLE, LangGraphFunctionalAdapter


@pytest.mark.asyncio
async def test_native_langgraph_task_graph_invocation(tmp_path):
    if not LANGGRAPH_AVAILABLE:
        pytest.skip("langgraph optional dependency is not installed")
    pytest.importorskip("langgraph.checkpoint.sqlite.aio")
    services = RuntimeServices(
        RuntimeConfig(), model=FakeModel({"response": "native"}), database_root=tmp_path
    )
    engine = LangGraphWorkflowEngine()
    await engine.initialize(services)
    plan = compile_workflow()
    handle = services.invocations.create(
        engine="langgraph",
        session_id="langgraph-session",
        user_id="langgraph-user",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    result = await engine.invoke(plan, handle, {"question": "hello"})
    assert result.status == "completed"
    services.close()

    restarted_services = RuntimeServices(
        RuntimeConfig(), model=FakeModel({"response": "resumed"}), database_root=tmp_path
    )
    restarted_engine = LangGraphWorkflowEngine()
    await restarted_engine.initialize(restarted_services)
    persisted = restarted_services.invocations.get(handle.invocation_id)
    assert persisted is not None
    resumed = await restarted_engine.resume(persisted, {"question": "again"}, plan)
    assert resumed.status == "completed"
    restarted_services.close()


@pytest.mark.asyncio
async def test_native_langgraph_interrupted_resume_preserves_operation_identity(tmp_path):
    if not LANGGRAPH_AVAILABLE:
        pytest.skip("langgraph optional dependency is not installed")
    pytest.importorskip("langgraph.checkpoint.sqlite.aio")
    workflow = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "tests",
            "name": "interrupted",
            "version": "1.0.0",
        },
        "do": [
            {
                "side_effect": {
                    "call": "http",
                    "with": {
                        "method": "POST",
                        "endpoint": "https://service.test/side-effect",
                        "body": {"action": "once-or-more"},
                    },
                }
            },
            {"pause": {"wait": {"seconds": 300}}},
            {"finish": {"set": {"done": True}}},
        ],
    }
    calls: list[str] = []
    reached_side_effect = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers["Idempotency-Key"])
        reached_side_effect.set()
        return httpx.Response(200, json={"ok": True})

    config = RuntimeConfig()
    services = RuntimeServices(config, model=FakeModel(), database_root=tmp_path)
    services.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(handler)))
    services.tools.protocols = services.protocols
    engine = LangGraphWorkflowEngine()
    await engine.initialize(services)
    plan = compile_workflow(workflow)
    handle = services.invocations.create(
        engine="langgraph",
        session_id="interrupted-session",
        user_id="interrupted-user",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    running = asyncio.create_task(engine.invoke(plan, handle, {}))
    try:
        await asyncio.wait_for(reached_side_effect.wait(), timeout=5)
        waiting = await asyncio.wait_for(asyncio.shield(running), timeout=5)
        assert waiting.status == "waiting", waiting.error
        assert handle.invocation_id not in engine._active
        assert handle.invocation_id not in engine._ownership_tasks
        assert handle._execution_lease is None
        assert services.invocations.read_input(handle) == {}
        assert services.model.calls == []
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()
        services.close()

    restarted = RuntimeServices(config, model=FakeModel(), database_root=tmp_path)
    restarted.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(handler)))
    restarted.tools.protocols = restarted.protocols
    resumed_engine = LangGraphWorkflowEngine()
    await resumed_engine.initialize(restarted)
    persisted = restarted.invocations.get(handle.invocation_id)
    assert persisted is not None
    assert persisted.status == "waiting"
    try:
        resumed = await resumed_engine.resume(persisted, {}, plan)
        assert resumed.status == "completed"
        assert resumed.output == {"done": True}
        assert len(calls) == 1 and len(set(calls)) == 1
        assert restarted.model.calls == []
    finally:
        await resumed_engine.shutdown()
        restarted.close()


@pytest.mark.asyncio
async def test_fresh_native_sqlite_storage_allows_concurrent_first_invocations(tmp_path):
    if not LANGGRAPH_AVAILABLE:
        pytest.skip("langgraph optional dependency is not installed")
    pytest.importorskip("langgraph.checkpoint.sqlite.aio")
    adapter = LangGraphFunctionalAdapter(str(tmp_path / "fresh.sqlite"))
    entered = 0
    all_entered = asyncio.Event()
    release = asyncio.Event()
    count = 12

    async def runner(value):
        nonlocal entered
        entered += 1
        if entered == count:
            all_entered.set()
        await release.wait()
        return {"value": value}

    running = [
        asyncio.create_task(adapter.invoke(runner, index, thread_id=f"thread-{index}"))
        for index in range(count)
    ]
    try:
        # Every workflow reaches the barrier before any can finish: checkpoint
        # initialization is ordered without serializing workflow execution.
        await asyncio.wait_for(all_entered.wait(), timeout=15)
        release.set()
        outputs = await asyncio.wait_for(asyncio.gather(*running), timeout=15)
        assert outputs == [{"value": index} for index in range(count)]
    finally:
        release.set()
        for task in running:
            task.cancel()
        await asyncio.gather(*running, return_exceptions=True)

    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "fresh.sqlite")) as saver:
        for index in range(count):
            checkpoint = await saver.aget_tuple({"configurable": {"thread_id": f"thread-{index}"}})
            assert checkpoint is not None
            assert checkpoint.checkpoint["channel_values"]["__end__"] == {"value": index}
