from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.errors import (
    UnsupportedWorkflowFeature,
    WorkflowExecutionError,
    WorkflowSchemaError,
    WorkflowSemanticError,
)
from open_workflow_agent.workflow import (
    OFFICIAL_SCHEMA_RELATIVE_PATH,
    ExecutionState,
    ExpressionEvaluator,
    WorkflowExecutor,
    _catch_error_value,
    _catch_matches,
    _duration_seconds,
    _error_details,
    _merge,
    _nested_task_lists,
    _resolve_protocol_endpoints,
    _retry_allowed,
    _retry_attempts,
    _retry_delay,
    _task_body,
    _validate_emit_capability,
    _validate_listen_capability,
    _validate_run_capability,
    _walk_tasks,
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

    external_catalog = {
        "document": {"dsl": "1.0.3", "namespace": "x", "name": "x", "version": "1.0.0"},
        "use": {"catalogs": {"remote": {"endpoint": "https://catalog.example.test"}}},
        "do": [{"finish": {"set": {"done": True}}}],
    }
    with pytest.raises(UnsupportedWorkflowFeature, match="external workflow catalogs"):
        compile_workflow(external_catalog)


def test_expression_evaluator_supports_paths_templates_and_conditions():
    evaluator = ExpressionEvaluator()
    data = {"customer": {"name": "Ada"}, "items": [2, 3]}
    assert evaluator.evaluate("${ .customer.name }", data) == "Ada"
    assert evaluator.evaluate("Hello ${ .customer.name }", data) == "Hello Ada"
    assert evaluator.evaluate(".items[1]", data) == 3
    assert evaluator.evaluate(".items | length", data) == 2
    assert evaluator.condition("${ .items[1] == 3 }", data)


def test_expression_evaluator_covers_composites_variables_and_boolean_operators():
    evaluator = ExpressionEvaluator()
    data = {"value": 3, "nested": {"name": "Ada"}, "items": [1, 2]}
    variables = {"extra": {"name": "Grace"}, "flag": True}
    assert evaluator.evaluate({"x": "${ .value }"}, data) == {"x": 3}
    assert evaluator.evaluate(["${ .value }", "literal"], data) == [3, "literal"]
    assert evaluator.evaluate("${ $extra.name }", data, variables=variables) == "Grace"
    assert evaluator.evaluate("${ @flag }", data, variables=variables) is True
    assert evaluator.evaluate("${ .items + [3] }", data) == [1, 2, 3]
    assert evaluator.evaluate("${ { 'value': .value, 'name': .nested.name } }", data) == {
        "value": 3,
        "name": "Ada",
    }
    assert evaluator.evaluate("${ [ .value, true, null ] }", data) == [3, True, None]
    assert evaluator.evaluate("${ 'quoted' }", data) == "quoted"
    assert evaluator.condition(".value > 2 and not .value < 2", data)
    assert evaluator.condition(".value == 3 or .value == 99", data)
    assert evaluator.condition("false", data) is False
    assert evaluator.evaluate(".missing[9]", data) is None


def test_workflow_helpers_cover_validation_and_retry_edges():
    state = ExecutionState(data={"ok": True}, variables={"value": 1})
    expressions = ExpressionEvaluator()
    assert _merge({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}
    assert _merge({"a": 1}, 2) == 2
    assert _duration_seconds({"after": {"seconds": 2}}) == 2
    assert _duration_seconds({"minutes": 1, "seconds": 2}) == 62
    assert _duration_seconds("PT1.5S") == 1.5
    assert _duration_seconds("2m") == 120
    with pytest.raises(WorkflowSemanticError, match="invalid duration"):
        _duration_seconds("invalid")
    assert _retry_attempts(None) == 1
    assert _retry_attempts({"limit": {"attempt": {"count": 2}}}) == 3
    assert _retry_attempts("invalid") == 1
    assert _retry_delay({"delay": "1s", "backoff": {"exponential": {}}}, 2) == 4
    assert _retry_delay({"delay": "1s", "backoff": {"linear": {}}}, 2) == 3
    assert _retry_delay({"delay": "1s"}, 2) == 1
    assert _retry_delay(None, 2) is None
    assert _retry_allowed(None, state, expressions) is True
    assert _retry_allowed({"when": "false"}, state, expressions) is False
    assert _retry_allowed({"exceptWhen": "true"}, state, expressions) is False
    assert _catch_matches(
        {"errors": {"with": {"type": "x"}}}, {"code": "x", "details": {}}, state, expressions
    )
    assert not _catch_matches({"when": "false"}, {"code": "x", "details": {}}, state, expressions)
    assert not _catch_matches(
        {"exceptWhen": "true"}, {"code": "x", "details": {}}, state, expressions
    )
    error = WorkflowExecutionError("boom")
    assert _error_details(error)["message"] == "boom"
    assert _error_details(RuntimeError("boom"))["code"] == "workflow_execution_error"


def test_workflow_helpers_cover_task_shapes_and_endpoint_templates():
    assert _task_body({"do": [{"one": {"set": {"x": 1}}}]}) == [{"one": {"set": {"x": 1}}}]
    assert _task_body({"set": {"x": 1}}) == [{"set": {"x": 1}}]
    assert _task_body("invalid") == []
    definition = {
        "do": [{"inner": {"set": {}}}],
        "for": {"each": "item"},
        "fork": {"branches": [{"do": []}]},
        "try": [],
        "catch": {"do": []},
        "switch": [{"case": {"do": []}}, "invalid"],
    }
    assert len(_nested_task_lists(definition)) == 6
    assert list(_walk_tasks([{"one": {}}, "bad"], prefix="/do")) == [
        ("/do/0/one", {}),
        ("/do/1", "bad"),
    ]
    payload = {"endpoint": "https://{.host}/api/{ $path }", "nested": [{"url": "{missing}"}]}
    assert _resolve_protocol_endpoints(
        payload, {"host": "service.test"}, {"path": "v1", "missing": "fallback"}
    ) == {"endpoint": "https://service.test/api/v1", "nested": [{"url": "fallback"}]}
    with pytest.raises(WorkflowSemanticError, match="unable to resolve"):
        _resolve_protocol_endpoints({"endpoint": "https://{missing}"}, {}, {})


def test_expression_evaluator_supports_bare_object_and_list_expressions():
    evaluator = ExpressionEvaluator()

    assert evaluator.evaluate(
        "{ ids: [ $input, .id ] }",
        {"id": 2},
        variables={"input": 1},
    ) == {"ids": [1, 2]}


def test_workflow_capability_helpers_reject_invalid_definitions():
    with pytest.raises(WorkflowSemanticError):
        _validate_emit_capability({}, "/do/0/emit")
    with pytest.raises(UnsupportedWorkflowFeature):
        _validate_listen_capability({"to": {"all": {}}}, "/do/0/listen")
    with pytest.raises(WorkflowSemanticError):
        _validate_listen_capability({"to": {"one": {}}}, "/do/0/listen")
    with pytest.raises(UnsupportedWorkflowFeature):
        _validate_listen_capability({"to": {"one": {"with": {}}}, "foreach": {}}, "/do/0/listen")
    with pytest.raises(WorkflowSemanticError):
        _validate_run_capability({}, "/do/0/run")
    with pytest.raises(UnsupportedWorkflowFeature):
        _validate_run_capability({"script": {}}, "/do/0/run")
    with pytest.raises(WorkflowSemanticError):
        _validate_run_capability({"workflow": {"name": "missing"}}, "/do/0/run")


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
    assert result["left"] == 1
    assert result["right"] == 2
    assert result["label"] == "called"


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


@pytest.mark.asyncio
async def test_try_catches_filtered_raise_and_exports_error(services):
    workflow = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "test",
            "name": "try-catch",
            "version": "1.0.0",
        },
        "do": [
            {
                "recover": {
                    "try": [{"fail": {"raise": {"error": "controlled failure"}}}],
                    "catch": {
                        "errors": {"with": {"detail": "controlled failure"}},
                        "as": "failure",
                        "do": [{"set_recovery": {"set": {"recovered": "${ $failure.message }"}}}],
                    },
                }
            }
        ],
    }

    result = await WorkflowExecutor(services.catalog, services=services).execute(
        compile_workflow(workflow), {}
    )

    assert result == {"recovered": "controlled failure"}


def test_catch_error_value_flattens_workflow_details_without_dropping_outer_fields():
    assert _catch_error_value(
        {
            "code": "tool_error",
            "message": "request failed",
            "details": {"type": "urn:error", "status": 404, "instance": "/do/0/task"},
        }
    ) == {
        "code": "tool_error",
        "message": "request failed",
        "type": "urn:error",
        "status": 404,
        "instance": "/do/0/task",
    }


@pytest.mark.asyncio
async def test_task_timeout_is_translated_to_common_workflow_error(services):
    workflow = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "test",
            "name": "timeout",
            "version": "1.0.0",
        },
        "do": [
            {
                "pause": {
                    "wait": {"milliseconds": 20},
                    "timeout": {"after": {"milliseconds": 1}},
                }
            }
        ],
    }

    with pytest.raises(WorkflowExecutionError, match="timed out"):
        await WorkflowExecutor(services.catalog, services=services).execute(
            compile_workflow(workflow), {}
        )


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
    assert await executor.execute(plan, {"value": 3}) == {"result": 3}
    with pytest.raises(WorkflowSemanticError):
        await executor.execute(plan, {"value": "bad"})
