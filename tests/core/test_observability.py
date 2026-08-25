from __future__ import annotations

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.engine import PortableWorkflowEngine
from open_workflow_agent.errors import WorkflowExecutionError, WorkflowSemanticError
from open_workflow_agent.observability import InMemoryEventSink
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import WorkflowExecutor, compile_workflow


def _workflow(tasks):
    return {
        "document": {
            "dsl": "1.0.3",
            "namespace": "tests",
            "name": "events",
            "version": "1.0.0",
        },
        "do": tasks,
    }


@pytest.mark.asyncio
async def test_lifecycle_events_trace_success_and_task_reference(services):
    sink = InMemoryEventSink()
    executor = WorkflowExecutor(services.catalog, services=services, event_sink=sink)
    plan = compile_workflow(_workflow([{"answer": {"set": {"ok": True}}}]))
    result = await executor.execute(
        plan,
        {},
        metadata={
            "invocation_id": "i1",
            "session_id": "s1",
            "engine": "test",
            "engine_execution_reference": "e1",
            "workflow_name": plan.name,
            "workflow_version": plan.version,
        },
    )
    assert result == {"ok": True}
    assert [event.event_type for event in sink.events] == [
        "WorkflowStarted",
        "TaskStarted",
        "TaskProgress",
        "TaskCompleted",
        "WorkflowCompleted",
    ]
    task = sink.events[3]
    assert task.task_reference == "/do/0/answer"
    assert task.invocation_id == "i1"
    assert task.operation_id == "i1:/do/0/answer"
    assert "engine_execution_reference" not in task.as_dict()


@pytest.mark.asyncio
async def test_lifecycle_events_trace_fault_and_retry(services):
    sink = InMemoryEventSink()
    services.model = FakeModel({"ok": True}, failures=1)
    services.catalog = services.catalog.default(services.model, services=services)
    executor = WorkflowExecutor(services.catalog, services=services, event_sink=sink)
    plan = compile_workflow(
        _workflow(
            [
                {
                    "retry": {
                        "try": [
                            {
                                "model": {
                                    "call": "llm:1.0.0@default",
                                    "with": {"prompt": "retry"},
                                }
                            }
                        ],
                        "catch": {"retry": {"limit": {"attempt": {"count": 1}}}},
                    }
                }
            ]
        )
    )
    result = await executor.execute(plan, {})
    assert result == {"ok": True}
    assert any(event.event_type == "TaskRetried" for event in sink.events)


@pytest.mark.asyncio
async def test_lifecycle_events_trace_faulted_workflow(services):
    sink = InMemoryEventSink()
    executor = WorkflowExecutor(services.catalog, services=services, event_sink=sink)
    plan = compile_workflow(_workflow([{"fail": {"raise": {"error": "tests.failure"}}}]))
    with pytest.raises(WorkflowExecutionError):
        await executor.execute(plan, {})
    assert sink.events[-1].event_type == "WorkflowFaulted"
    assert sink.events[-1].error is not None


@pytest.mark.asyncio
async def test_lifecycle_events_trace_input_fault(services):
    sink = InMemoryEventSink()
    executor = WorkflowExecutor(services.catalog, services=services, event_sink=sink)
    plan = compile_workflow(
        _workflow(
            [
                {
                    "answer": {
                        "set": {"ok": True},
                        "input": {
                            "schema": {
                                "document": {
                                    "type": "object",
                                    "required": ["required"],
                                }
                            }
                        },
                    }
                }
            ]
        )
    )
    with pytest.raises(WorkflowSemanticError):
        await executor.execute(plan, {})
    assert sink.events[0].event_type == "WorkflowStarted"
    assert sink.events[-1].event_type == "WorkflowFaulted"


@pytest.mark.asyncio
async def test_resume_emits_common_lifecycle_events(tmp_path):
    sink = InMemoryEventSink()
    services = RuntimeServices(
        RuntimeConfig(), model=FakeModel(), database_root=tmp_path, event_sink=sink
    )
    engine = PortableWorkflowEngine()
    await engine.initialize(services)
    plan = compile_workflow()
    handle = services.invocations.create(
        engine="portable",
        session_id="resume-session",
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    await engine.invoke(plan, handle, {"input": "first"})
    resumed = await engine.resume(handle, {"input": "second"}, plan)
    assert resumed.status == "completed"
    assert sum(event.event_type == "WorkflowCompleted" for event in sink.events) == 1
    assert resumed.output == handle.output
    services.close()
