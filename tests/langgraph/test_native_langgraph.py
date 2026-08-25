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
from open_workflow_agent_langgraph.native import LANGGRAPH_AVAILABLE


@pytest.mark.asyncio
async def test_native_langgraph_functional_invocation(tmp_path):
    if not LANGGRAPH_AVAILABLE:
        pytest.skip("langgraph optional dependency is not installed")
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
            {"pause": {"wait": {"seconds": 1}}},
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
    await asyncio.wait_for(reached_side_effect.wait(), timeout=5)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    services.close()

    restarted = RuntimeServices(config, model=FakeModel(), database_root=tmp_path)
    restarted.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(handler)))
    restarted.tools.protocols = restarted.protocols
    resumed_engine = LangGraphWorkflowEngine()
    await resumed_engine.initialize(restarted)
    persisted = restarted.invocations.get(handle.invocation_id)
    assert persisted is not None
    assert persisted.status == "running"
    resumed = await resumed_engine.resume(persisted, {}, plan)
    assert resumed.status == "completed"
    assert resumed.output == {"done": True}
    assert len(calls) >= 2
    assert len(set(calls[-2:])) == 1
    restarted.close()
