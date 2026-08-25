from __future__ import annotations

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.errors import UnsupportedWorkflowFeature, WorkflowSchemaError
from open_workflow_agent.workflow import ExpressionEvaluator, WorkflowExecutor, compile_workflow
from pydantic import ValidationError


def test_default_workflow_is_generated_and_stable():
    plan = compile_workflow()
    assert plan.dsl == "1.0.3"
    assert plan.name == "default-agent"
    assert plan.tasks[0].reference == "/do/0/respond"
    assert plan.fingerprint == compile_workflow().fingerprint


def test_workflow_schema_and_capability_errors():
    with pytest.raises(WorkflowSchemaError):
        compile_workflow({"document": {}, "do": []})
    workflow = {
        "document": {"dsl": "1.0.3", "namespace": "x", "name": "x", "version": "1"},
        "do": [{"unsafe": {"run": {"shell": "rm -rf /"}}}],
    }
    with pytest.raises(UnsupportedWorkflowFeature):
        compile_workflow(workflow)


def test_expression_evaluator_supports_paths_templates_and_conditions():
    evaluator = ExpressionEvaluator()
    data = {"customer": {"name": "Ada"}, "items": [2, 3]}
    assert evaluator.evaluate("${ .customer.name }", data) == "Ada"
    assert evaluator.evaluate("Hello ${ .customer.name }", data) == "Hello Ada"
    assert evaluator.evaluate(".items[1]", data) == 3
    assert evaluator.condition("${ .items[1] == 3 }", data)


@pytest.mark.asyncio
async def test_plan_executes_set_switch_for_fork_and_calls(services):
    services.model.response = {"label": "called"}
    workflow = {
        "document": {"dsl": "1.0.3", "namespace": "test", "name": "portable", "version": "1"},
        "do": [
            {"set_value": {"set": {"kind": "yes", "items": ["a", "b"]}}},
            {
                "route": {
                    "switch": [
                        {
                            "when": "${ .kind == 'yes' }",
                            "then": [{"set_route": {"set": {"routed": True}}}],
                        },
                        {"otherwise": [{"set_route": {"set": {"routed": False}}}]},
                    ]
                }
            },
            {"call_model": {"call": "llm:1.0.0@default", "with": {"prompt": "test"}}},
            {
                "loop": {
                    "for": {
                        "each": "item",
                        "in": "${ .items }",
                        "do": [{"set_seen": {"set": {"seen": "@item"}}}],
                    }
                }
            },
            {
                "branches": {
                    "fork": {
                        "branches": [
                            [{"left": {"set": {"left": 1}}}],
                            [{"right": {"set": {"right": 2}}}],
                        ]
                    }
                }
            },
        ],
    }
    executor = WorkflowExecutor(services.catalog, services=services)
    result = await executor.execute(compile_workflow(workflow), {})
    assert result["routed"] is True
    assert result["label"] == "called"
    assert result["seen"] == "b"


@pytest.mark.asyncio
async def test_retry_and_timeout_task_policies(services):
    services.model = FakeModel({"ok": True}, failures=1)
    services.catalog = services.catalog.default(services.model)
    workflow = {
        "document": {"dsl": "1.0.3", "namespace": "test", "name": "policies", "version": "1"},
        "do": [
            {
                "retry_call": {
                    "call": "llm:1.0.0@default",
                    "with": {"prompt": "ok"},
                    "retry": {"max_attempts": 1},
                }
            },
            {"wait": {"wait": "0ms", "timeout": {"after": "1s"}}},
        ],
    }
    result = await WorkflowExecutor(services.catalog, services=services).execute(
        compile_workflow(workflow), {}
    )
    assert result["ok"] is True


def test_strict_configuration_rejects_unknown_keys():
    with pytest.raises(ValidationError):
        RuntimeConfig.model_validate({"modle": {"name": "x"}})
