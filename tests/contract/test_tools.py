from __future__ import annotations

import httpx
import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.engine import InvocationResult
from open_workflow_agent.knowledge import DeterministicEmbeddingProvider, KnowledgeService
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow
from open_workflow_agent_adk import AdkWorkflowEngine
from open_workflow_agent_langgraph import LangGraphWorkflowEngine


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_type", [AdkWorkflowEngine, LangGraphWorkflowEngine])
async def test_configured_agent_tool_roundtrip_is_engine_independent(tmp_path, engine_type):
    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "tools": [
                {
                    "type": "mcp",
                    "name": "lookup",
                    "endpoint": "https://service.test/mcp",
                }
            ],
        }
    )
    model = FakeModel(
        [
            {"tool_call": {"name": "lookup", "arguments": {"query": "policy"}}},
            {"response": "tool-result-used"},
        ]
    )
    services = RuntimeServices(config, model=model, database_root=tmp_path / engine_type.__name__)

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/mcp"
        return httpx.Response(200, json={"result": {"content": "tool-ok"}})

    services.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(handler)))
    services.tools.protocols = services.protocols
    engine = engine_type()
    await engine.initialize(services)
    assert "lookup" in {getattr(tool, "name", "") for tool in engine.agent.tools}
    plan = compile_workflow()
    handle = services.invocations.create(
        engine=engine.engine_name,
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    result = await engine.invoke(plan, handle, {"question": "policy"})
    assert isinstance(result, InvocationResult)
    assert result.status == "completed"
    assert result.output["response"] == "tool-result-used"
    assert len(model.calls) == 2
    services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_type", [AdkWorkflowEngine, LangGraphWorkflowEngine])
async def test_search_knowledge_tool_is_engine_independent(tmp_path, engine_type):
    knowledge_root = tmp_path / "knowledge"
    knowledge_root.mkdir()
    (knowledge_root / "policy.md").write_text(
        "License renewal requires an application.", encoding="utf-8"
    )
    config = RuntimeConfig.model_validate({"knowledge": {"path": str(knowledge_root)}})
    model = FakeModel(
        [
            {"tool_call": {"name": "search_knowledge", "arguments": {"query": "renewal"}}},
            {"response": "knowledge-result-used"},
        ]
    )
    services = RuntimeServices(config, model=model, database_root=tmp_path / "runtime")
    services.knowledge.close()
    services.knowledge = KnowledgeService(
        knowledge_root,
        tmp_path / "runtime" / "knowledge.sqlite3",
        embedding=DeterministicEmbeddingProvider(),
    )
    assert services.knowledge.reload()["added"] == 1
    engine = engine_type()
    await engine.initialize(services)
    assert "search_knowledge" in {getattr(tool, "name", "") for tool in engine.agent.tools}
    plan = compile_workflow()
    handle = services.invocations.create(
        engine=engine.engine_name,
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    result = await engine.invoke(plan, handle, {"question": "renewal"})
    assert isinstance(result, InvocationResult)
    assert result.status == "completed"
    assert result.output["response"] == "knowledge-result-used"
    assert len(model.calls) == 2
    services.close()
