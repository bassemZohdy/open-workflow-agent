"""Declared Command routing preserves standard workflow semantics on the SDK."""

from collections.abc import Hashable
from typing import Generic, TypeVar

import pytest
from open_workflow_agent.workflow import compile_workflow, generate_default_workflow
from open_workflow_agent_langgraph import LangGraphWorkflowEngine

Command = pytest.importorskip("langgraph.types").Command

N = TypeVar("N", bound=Hashable)


@pytest.mark.asyncio
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("required", [False, True])
async def test_declared_native_command_updates_and_routes_actual_nodes(
    services, monkeypatch, nested, required
):
    from open_workflow_agent_langgraph import graph as implementation

    commands = []

    class RecordingCommand(Command[N], Generic[N]):
        def __init__(self, **values):
            super().__init__(**values)
            if "goto" in values:
                commands.append((self.goto, set(self.update)))

    monkeypatch.setattr(implementation, "Command", RecordingCommand)
    body = [
        {"first": {"set": {"value": 1}, "then": "last"}},
        {"never": {"call": "llm:1.0.0@default"}},
        {"last": {"set": {"value": "${ .value + 1 }"}}},
    ]
    workflow = generate_default_workflow()
    workflow["document"]["metadata"] = {
        "owa": {
            "nativeExtensions": [
                {"name": "langgraph.command", "version": "1.0.0", "required": required}
            ]
        }
    }
    workflow["do"] = [{"outer": {"do": body}}] if nested else body
    plan = compile_workflow(workflow)
    engine = LangGraphWorkflowEngine()
    await engine.initialize(services)
    engine.native.database_path = None
    handle = services.invocations.create(
        engine="langgraph",
        session_id="command",
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    try:
        result = await engine.invoke(plan, handle, {})
        assert result.status == "completed", result.error
        assert result.output == {"value": 2} and services.model.calls == []
        assert len(commands) == ((3 if nested else 2) if required else 0)
        assert all(
            fields == {"snapshot", "counters", "next_node", "receipt_bindings"}
            for _, fields in commands
        )
        assert not commands or commands[-1][0] == "__end__"
    finally:
        await engine.shutdown()
