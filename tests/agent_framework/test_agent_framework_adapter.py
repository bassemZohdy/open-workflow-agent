from __future__ import annotations

from typing import Any

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow
from open_workflow_agent_agent_framework import AgentFrameworkWorkflowEngine
from open_workflow_agent_agent_framework import native as native_module
from open_workflow_agent_agent_framework.native import AgentFrameworkNativeAdapter


def test_agent_framework_adapter_capabilities_remain_portable() -> None:
    capabilities = AgentFrameworkWorkflowEngine().capabilities()
    assert capabilities.engine == "agent-framework"
    assert capabilities.resume is True
    assert capabilities.streaming is False


@pytest.mark.asyncio
async def test_native_bridge_falls_back_without_optional_dependency(monkeypatch) -> None:
    monkeypatch.setattr(native_module, "AGENT_FRAMEWORK_AVAILABLE", False)
    adapter = AgentFrameworkNativeAdapter()

    async def runner(value: Any) -> Any:
        return {"echo": value}

    assert await adapter.invoke(runner, "hello") == {"echo": "hello"}


@pytest.mark.asyncio
async def test_native_bridge_uses_agent_framework_workflow_without_leaking_state(monkeypatch) -> None:
    outputs: list[Any] = []

    class FakeExecutor:
        def __init__(self, *, id: str) -> None:
            self.id = id

    def fake_handler(function):
        return function

    class FakeContext:
        async def yield_output(self, value: Any) -> None:
            outputs.append(value)

    class FakeEvents:
        def get_outputs(self) -> list[Any]:
            return list(outputs)

    class FakeWorkflow:
        def __init__(self, executor: Any) -> None:
            self.executor = executor

        async def run(self, value: Any) -> FakeEvents:
            await self.executor.process(value, FakeContext())
            return FakeEvents()

    class FakeWorkflowBuilder:
        def __init__(self, *, start_executor: Any, name: str) -> None:
            assert name == "open-workflow-agent-portable-bridge"
            self.executor = start_executor

        def build(self) -> FakeWorkflow:
            return FakeWorkflow(self.executor)

    monkeypatch.setattr(native_module, "AGENT_FRAMEWORK_AVAILABLE", True)
    monkeypatch.setattr(native_module, "Executor", FakeExecutor)
    monkeypatch.setattr(native_module, "WorkflowBuilder", FakeWorkflowBuilder)
    monkeypatch.setattr(native_module, "handler", fake_handler)

    async def runner(value: Any) -> Any:
        return {"portable": value}

    result = await AgentFrameworkNativeAdapter().invoke(runner, {"input": True})
    assert result == {"portable": {"input": True}}
    assert outputs == [result]


@pytest.mark.asyncio
async def test_engine_fallback_executes_common_open_workflow_contract(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(native_module, "AGENT_FRAMEWORK_AVAILABLE", False)
    config = RuntimeConfig.model_validate({"model": {"provider": "fake"}})
    services = RuntimeServices(
        config,
        model=FakeModel({"response": "ok"}),
        database_root=tmp_path,
    )
    engine = AgentFrameworkWorkflowEngine()
    await engine.initialize(services)
    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "adapter",
                "name": "agent-framework",
                "version": "1.0.0",
            },
            "do": [{"answer": {"set": {"engine": "portable"}}}],
        }
    )
    handle = services.invocations.create(
        engine=engine.engine_name,
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    result = await engine.invoke(plan, handle, {})
    assert result.status == "completed"
    assert result.output == {"engine": "portable"}
    assert result.as_dict().keys() >= {"invocation_id", "session_id", "status", "output"}
    await engine.shutdown()
    services.close()
