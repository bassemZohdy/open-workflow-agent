from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import yaml
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow
from open_workflow_agent_adk import AdkWorkflowEngine
from open_workflow_agent_langgraph import LangGraphWorkflowEngine


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("engine_name", "engine_type"),
    [("adk", AdkWorkflowEngine), ("langgraph", LangGraphWorkflowEngine)],
)
@pytest.mark.parametrize(
    "fixture_name",
    [
        "minimal-agent",
        "set",
        "switch",
        "for",
        "fork",
        "retry",
        "sequence",
        "llm-call",
        "data-transform",
        "input-validation",
        "nested-references",
        "protocol-calls",
        "controlled-error",
        "wait-timeout",
    ],
)
async def test_portable_fixture_has_same_result_on_each_engine(
    tmp_path, engine_name, engine_type, fixture_name
):
    fixture = Path(__file__).parent / "fixtures" / f"{fixture_name}.yaml"
    workflow = yaml.safe_load(fixture.read_text(encoding="utf-8"))
    services = RuntimeServices(
        RuntimeConfig(), model=FakeModel({"response": "ok"}), database_root=tmp_path / engine_name
    )
    if fixture_name == "protocol-calls":

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"path": request.url.path})

        services.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(handler)))
        services.tools.protocols = services.protocols
    engine = engine_type()
    await engine.initialize(services)
    plan = compile_workflow(workflow)
    handle = services.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    input_data = (
        {"payload": {"value": 3}}
        if fixture_name == "data-transform"
        else {"question": "hello", "kind": "priority", "items": [1, 2]}
    )
    result = await engine.invoke(plan, handle, input_data)
    if fixture_name == "controlled-error":
        assert result.status == "faulted"
        assert result.error and result.error["code"] == "workflow_execution_error"
    else:
        assert result.status == "completed"
        assert result.output == _expected(fixture_name, input_data)
    assert any(event.event_type == "TaskStarted" for event in services.events.events)
    if fixture_name != "controlled-error":
        assert any(event.event_type == "TaskCompleted" for event in services.events.events)
    assert all(
        event.task_reference
        for event in services.events.events
        if event.event_type in {"TaskStarted", "TaskCompleted"}
    )
    services.close()


def _expected(fixture_name: str, input_data: dict[str, object]) -> object:
    if fixture_name in {"minimal-agent", "llm-call", "retry"}:
        return {**input_data, "response": "ok"}
    if fixture_name == "set":
        return {**input_data, "answer": "hello"}
    if fixture_name == "for":
        return {**input_data, "last": 2}
    if fixture_name == "fork":
        return {**input_data, "left": True, "right": True}
    if fixture_name == "sequence":
        return {**input_data, "copied": "hello", "answer": "hello"}
    if fixture_name == "data-transform":
        return 3
    if fixture_name == "input-validation":
        return {**input_data, "accepted": "hello"}
    if fixture_name == "nested-references":
        return {**input_data, "nested": "hello"}
    if fixture_name == "protocol-calls":
        return {**input_data, "path": "/openapi"}
    return input_data
