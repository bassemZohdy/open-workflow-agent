from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.errors import (
    UnsupportedWorkflowFeature,
    WorkflowSchemaError,
    WorkflowSemanticError,
)
from open_workflow_agent.workflow import (
    OFFICIAL_SCHEMA_RELATIVE_PATH,
    ExpressionEvaluator,
    WorkflowExecutor,
    compile_workflow,
)
from pydantic import ValidationError


def test_default_workflow_is_generated_and_stable():
    plan = compile_workflow()
    assert plan.dsl == "1.0.3"
    assert plan.name == "default-agent"
    assert plan.tasks[0].reference == "/do/0/respond"
    assert plan.fingerprint == compile_workflow().fingerprint


def test_official_schema_is_vendored_and_used():
    schema_path = Path(__file__).parents[2] / OFFICIAL_SCHEMA_RELATIVE_PATH
    assert schema_path.is_file()
    digest = hashlib.sha256(schema_path.read_bytes()).hexdigest()
    assert digest == "704ef5e91c5d823167dd8751794edb1dd1a6f9a3bdf9bfd389bf9c6b23ae3816"
    assert compile_workflow().dsl == "1.0.3"


def test_workflow_schema_and_capability_errors():
    with pytest.raises(WorkflowSchemaError):
        compile_workflow({"document": {}, "do": []})
    workflow = {
        "document": {"dsl": "1.0.3", "namespace": "x", "name": "x", "version": "1.0.0"},
        "do": [{"unsafe": {"run": {"shell": {"command": "rm -rf /"}}}}],
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
        "document": {
            "dsl": "1.0.3",
            "namespace": "test",
            "name": "portable",
            "version": "1.0.0",
        },
        "do": [
            {"set_value": {"set": {"kind": "yes", "items": ["a", "b"]}}},
            {
                "route": {
                    "switch": [
                        {
                            "yes": {
                                "when": "${ .kind == 'yes' }",
                                "then": "set_route",
                            }
                        },
                        {"no": {"then": "set_default"}},
                    ]
                }
            },
            {"set_route": {"set": {"routed": True}}},
            {
                "set_default": {
                    "if": "${ .routed != true }",
                    "set": {"routed": False},
                }
            },
            {"call_model": {"call": "llm:1.0.0@default", "with": {"prompt": "test"}}},
            {
                "loop": {
                    "for": {
                        "each": "item",
                        "in": "${ .items }",
                    },
                    "do": [{"set_seen": {"set": {"seen": "${ $item }"}}}],
                }
            },
            {
                "branches": {
                    "fork": {
                        "branches": [
                            {"left": {"set": {"left": 1}}},
                            {"right": {"set": {"right": 2}}},
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
        "document": {
            "dsl": "1.0.3",
            "namespace": "test",
            "name": "policies",
            "version": "1.0.0",
        },
        "do": [
            {
                "retry_call": {
                    "try": [
                        {
                            "first_attempt": {
                                "call": "llm:1.0.0@default",
                                "with": {"prompt": "ok"},
                            }
                        }
                    ],
                    "catch": {
                        "retry": {
                            "limit": {"attempt": {"count": 1}},
                        },
                        "do": [
                            {
                                "second_attempt": {
                                    "call": "llm:1.0.0@default",
                                    "with": {"prompt": "ok"},
                                }
                            }
                        ],
                    },
                }
            },
            {
                "wait": {
                    "wait": {"milliseconds": 0},
                    "timeout": {"after": {"seconds": 1}},
                }
            },
        ],
    }
    result = await WorkflowExecutor(services.catalog, services=services).execute(
        compile_workflow(workflow), {}
    )
    assert result["ok"] is True


def test_strict_configuration_rejects_unknown_keys():
    with pytest.raises(ValidationError):
        RuntimeConfig.model_validate({"modle": {"name": "x"}})


@pytest.mark.asyncio
async def test_workflow_and_task_input_output_schemas_are_enforced(services):
    workflow = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "tests",
            "name": "schemas",
            "version": "1.0.0",
        },
        "input": {"schema": {"document": {"type": "object", "required": ["value"]}}},
        "do": [
            {
                "task": {
                    "input": {
                        "from": "${ .value }",
                        "schema": {"document": {"type": "integer"}},
                    },
                    "set": {"result": "${ . }"},
                    "output": {
                        "schema": {
                            "document": {
                                "type": "object",
                                "required": ["result"],
                            }
                        }
                    },
                }
            }
        ],
    }
    plan = compile_workflow(workflow)
    executor = WorkflowExecutor(services.catalog, services=services)
    assert await executor.execute(plan, {"value": 3}) == {"value": 3, "result": 3}
    with pytest.raises(WorkflowSemanticError):
        await executor.execute(plan, {"value": "bad"})
