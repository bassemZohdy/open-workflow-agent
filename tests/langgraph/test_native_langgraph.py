from __future__ import annotations

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
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
