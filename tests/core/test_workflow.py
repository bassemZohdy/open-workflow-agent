from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.engine import EngineCapabilities, WorkflowEngine
from open_workflow_agent.errors import (
    ExpressionError,
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
    _path_get,
    _resolve_protocol_endpoints,
    _retry_allowed,
    _retry_attempts,
    _retry_delay,
    _task_body,
    _validate_emit_capability,
    _validate_json_value,
    _validate_listen_capability,
    _validate_run_capability,
    _validate_transitions,
    _walk_tasks,
    compile_workflow,
    load_json_value,
    load_workflow,
    load_workflow_text,
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


@pytest.mark.asyncio
async def test_versioned_native_metadata_requires_exact_engine_capability():
    workflow = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "x",
            "name": "x",
            "version": "1.0.0",
            "metadata": {
                "owa": {"nativeExtensions": [{"name": "langgraph.channels", "version": "1.0.0"}]}
            },
        },
        "do": [{"finish": {"set": {"done": True}}}],
    }
    plan = compile_workflow(workflow)
    assert plan.native_extensions[0].capability_id == "langgraph.channels@1.0.0"
    assert "nativeExtensions" in EngineCapabilities("test").as_dict()

    with pytest.raises(UnsupportedWorkflowFeature, match="required native workflow extension"):
        await WorkflowEngine().compile(plan)

    class ExtensionEngine(WorkflowEngine):
        def capabilities(self) -> EngineCapabilities:
            return EngineCapabilities(
                engine="test", native_extensions=("langgraph.channels@1.0.0",)
            )

    assert await ExtensionEngine().compile(plan) is plan

    class WrongVersionEngine(WorkflowEngine):
        def capabilities(self) -> EngineCapabilities:
            return EngineCapabilities(
                engine="test", native_extensions=("langgraph.channels@2.0.0",)
            )

    with pytest.raises(UnsupportedWorkflowFeature):
        await WrongVersionEngine().compile(plan)


@pytest.mark.asyncio
async def test_unsupported_optional_native_extension_is_inert_and_not_advertised():
    workflow = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "x",
            "name": "x",
            "version": "1.0.0",
            "metadata": {
                "owa": {
                    "nativeExtensions": [
                        {
                            "name": "langgraph.planning",
                            "version": "9.0.0",
                            "required": False,
                        }
                    ]
                }
            },
        },
        "do": [{"finish": {"set": {"done": True}}}],
    }
    plan = compile_workflow(workflow)
    assert plan.native_extensions[0].required is False
    assert EngineCapabilities("test").native_extensions == ()
    assert await WorkflowEngine().compile(plan) is plan


def test_native_metadata_rejects_malformed_and_unsupported_scope_declarations():
    base = {
        "document": {"dsl": "1.0.3", "namespace": "x", "name": "x", "version": "1.0.0"},
        "do": [{"finish": {"set": {"done": True}}}],
    }
    malformed = {
        **base,
        "document": {
            **base["document"],
            "metadata": {"owa": {"nativeExtensions": [{"name": "channels", "version": "latest"}]}},
        },
    }
    with pytest.raises(WorkflowSemanticError, match="namespaced identifier"):
        compile_workflow(malformed)

    scoped = {
        **base,
        "document": {
            **base["document"],
            "metadata": {
                "owa": {
                    "nativeExtensions": [
                        {"name": "langgraph.channels", "version": "1.0.0", "scope": "/do/0"}
                    ]
                }
            },
        },
    }
    with pytest.raises(WorkflowSemanticError, match="only name, version and required"):
        compile_workflow(scoped)

    duplicate = {
        **base,
        "document": {
            **base["document"],
            "metadata": {
                "owa": {
                    "nativeExtensions": [
                        {"name": "langgraph.channels", "version": "1.0.0"},
                        {"name": "langgraph.channels", "version": "1.0.0"},
                    ]
                }
            },
        },
    }
    with pytest.raises(WorkflowSemanticError, match="duplicate native extension"):
        compile_workflow(duplicate)

    invalid_required = {
        **base,
        "document": {
            **base["document"],
            "metadata": {
                "owa": {
                    "nativeExtensions": [
                        {"name": "langgraph.channels", "version": "1.0.0", "required": "yes"}
                    ]
                }
            },
        },
    }
    with pytest.raises(WorkflowSemanticError, match="required must be a boolean"):
        compile_workflow(invalid_required)


@pytest.mark.parametrize(
    ("declarations", "message"),
    [
        (
            [{"name": f"x.ext{i}", "version": "1.0.0"} for i in range(33)],
            "limit of 32",
        ),
        (
            [{"name": "a." + "x" * 127, "version": "1.0.0"}],
            "namespaced identifier",
        ),
        (
            [{"name": "langgraph.channels", "version": "1" * 62 + ".0.0"}],
            "exact semantic version",
        ),
    ],
)
def test_native_metadata_enforces_finite_declaration_and_identifier_limits(
    declarations: list[dict[str, object]], message: str
):
    workflow = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "x",
            "name": "x",
            "version": "1.0.0",
            "metadata": {"owa": {"nativeExtensions": declarations}},
        },
        "do": [{"finish": {"set": {"done": True}}}],
    }
    with pytest.raises(WorkflowSemanticError, match=message):
        compile_workflow(workflow)


def _native_channel_workflow():
    return {
        "document": {
            "dsl": "1.0.3",
            "namespace": "x",
            "name": "channels",
            "version": "1.0.0",
            "metadata": {
                "owa": {
                    "nativeExtensions": [{"name": "langgraph.channels", "version": "1.0.0"}],
                    "nativeExtensionData": {
                        "langgraph.channels@1.0.0": {
                            "channels": [
                                {
                                    "id": "items",
                                    "reducer": "append",
                                    "initial": [],
                                    "writes": [{"task": "/do/0"}],
                                }
                            ],
                            "reads": [
                                {
                                    "task": "/do/1",
                                    "as": "owa_native_channels",
                                    "channels": ["items"],
                                }
                            ],
                        }
                    },
                }
            },
        },
        "do": [
            {"write_items": {"set": '${ ["first", "second"] }'}},
            {"read_items": {"set": "${ $owa_native_channels.items }"}},
        ],
    }


def test_native_channel_metadata_binds_final_task_outputs_and_reader_expression_scope():
    plan = compile_workflow(_native_channel_workflow())
    assert plan.native_channels is not None
    channel = plan.native_channels.channels[0]
    assert (channel.channel_id, channel.reducer, channel.initial, channel.writers) == (
        "items",
        "append",
        [],
        ("/do/0/write_items",),
    )
    assert plan.native_channels.reads[0].task == "/do/1/read_items"

    workflow = _native_channel_workflow()
    workflow["do"][0]["write_items"]["set"] = "${ $owa_native_channels.items }"
    with pytest.raises(ExpressionError):
        compile_workflow(workflow)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda data: data["document"]["metadata"]["owa"].pop("nativeExtensions"),
            "exact required",
        ),
        (
            lambda data: data["document"]["metadata"]["owa"]["nativeExtensions"][0].update(
                required=False
            ),
            "exact required",
        ),
        (
            lambda data: data["document"]["metadata"]["owa"]["nativeExtensionData"][
                "langgraph.channels@1.0.0"
            ].update(extra=True),
            "only channels and reads",
        ),
        (
            lambda data: data["document"]["metadata"]["owa"]["nativeExtensionData"][
                "langgraph.channels@1.0.0"
            ]["channels"][0].update(reducer="custom.module:call"),
            "reducer must be append or last_value",
        ),
        (
            lambda data: data["document"]["metadata"]["owa"]["nativeExtensionData"][
                "langgraph.channels@1.0.0"
            ]["channels"][0]["writes"].append({"task": "/do/0"}),
            "duplicate native channel writer",
        ),
        (
            lambda data: data["document"]["metadata"]["owa"]["nativeExtensionData"][
                "langgraph.channels@1.0.0"
            ]["reads"][0].update(task="/do/0/nested"),
            "direct root /do/<index>",
        ),
        (
            lambda data: data["document"]["metadata"]["owa"]["nativeExtensionData"][
                "langgraph.channels@1.0.0"
            ]["channels"][0].update(initial=["x" * 65537]),
            "65536 bytes",
        ),
        (
            lambda data: data["document"]["metadata"]["owa"]["nativeExtensionData"][
                "langgraph.channels@1.0.0"
            ]["reads"].append(
                {"task": "/do/1", "as": "owa_native_channels", "channels": ["items"]}
            ),
            "duplicate native channel reader",
        ),
    ],
)
def test_native_channel_metadata_rejects_unsupported_or_unsafe_declarations(mutate, message):
    workflow = _native_channel_workflow()
    mutate(workflow)
    with pytest.raises(WorkflowSemanticError, match=message):
        compile_workflow(workflow)


def test_native_channel_reserved_variable_rejects_declared_alias_collision_only():
    workflow = _native_channel_workflow()
    workflow["do"] = [
        {
            "loop": {
                "for": {
                    "each": "owa_native_channels",
                    "in": "${ [] }",
                },
                "do": [{"body": {"set": {"ok": True}}}],
            }
        }
    ]
    workflow["document"]["metadata"]["owa"]["nativeExtensionData"]["langgraph.channels@1.0.0"][
        "reads"
    ] = []
    with pytest.raises(WorkflowSemanticError, match="reserved native channel variable"):
        compile_workflow(workflow)

    workflow = _native_channel_workflow()
    workflow["document"]["metadata"]["owa"]["nativeExtensionData"]["langgraph.channels@1.0.0"][
        "channels"
    ][0]["initial"] = float("nan")
    with pytest.raises(WorkflowSchemaError, match="finite JSON data"):
        compile_workflow(workflow)

    workflow = _native_channel_workflow()
    workflow["output"] = {"as": "literal output name"}
    assert compile_workflow(workflow).native_channels is not None


def test_expression_evaluator_supports_paths_templates_and_conditions():
    evaluator = ExpressionEvaluator()
    data = {"customer": {"name": "Ada"}, "items": [2, 3]}
    assert evaluator.evaluate("${ .customer.name }", data) == "Ada"
    assert evaluator.evaluate("Hello ${ .customer.name }", data) == "Hello Ada"
    assert evaluator.evaluate("${ .items[1] }", data) == 3
    assert evaluator.evaluate("${ .items | length }", data) == 2
    assert evaluator.condition("${ .items[1] == 3 }", data)


def test_expression_evaluator_covers_composites_variables_and_boolean_operators():
    evaluator = ExpressionEvaluator()
    data = {"value": 3, "nested": {"name": "Ada"}, "items": [1, 2]}
    variables = {"extra": {"name": "Grace"}, "flag": True}
    assert evaluator.evaluate({"x": "${ .value }"}, data) == {"x": 3}
    assert evaluator.evaluate(["${ .value }", "literal"], data) == [3, "literal"]
    assert evaluator.evaluate("${ $extra.name }", data, variables=variables) == "Grace"
    assert evaluator.evaluate("${ $flag }", data, variables=variables) is True
    assert evaluator.evaluate("${ .items + [3] }", data) == [1, 2, 3]
    assert evaluator.evaluate('${ { "value": .value, "name": .nested.name } }', data) == {
        "value": 3,
        "name": "Ada",
    }
    assert evaluator.evaluate("${ [ .value, true, null ] }", data) == [3, True, None]
    assert evaluator.evaluate('${ "quoted" }', data) == "quoted"
    assert evaluator.condition("${ (.value > 2) and (.value < 2 | not) }", data)
    assert evaluator.condition("${ .value == 3 or .value == 99 }", data)
    assert evaluator.condition("${ false }", data) is False
    assert evaluator.evaluate("${ .missing[9] }", data) is None


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
    assert _retry_allowed({"when": "${ false }"}, state, expressions) is False
    assert _retry_allowed({"exceptWhen": "${ true }"}, state, expressions) is False
    assert _catch_matches(
        {"errors": {"with": {"type": "x"}}}, {"code": "x", "details": {}}, state, expressions
    )
    assert not _catch_matches(
        {"when": "${ false }"}, {"code": "x", "details": {}}, state, expressions
    )
    assert not _catch_matches(
        {"exceptWhen": "${ true }"}, {"code": "x", "details": {}}, state, expressions
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


def test_expression_evaluator_supports_wrapped_object_and_list_expressions():
    evaluator = ExpressionEvaluator()

    assert evaluator.evaluate(
        "${ { ids: [ $input, .id ] } }",
        {"id": 2},
        variables={"input": 1},
    ) == {"ids": [1, 2]}


def test_workflow_capability_helpers_reject_invalid_definitions():
    with pytest.raises(WorkflowSemanticError):
        _validate_emit_capability({}, "/do/0/emit")
    with pytest.raises(WorkflowSemanticError, match="listen.all must be an array"):
        _validate_listen_capability({"to": {"all": {}}}, "/do/0/listen")
    with pytest.raises(WorkflowSemanticError):
        _validate_listen_capability({"to": {"one": {}}}, "/do/0/listen")
    with pytest.raises(WorkflowSemanticError, match="event filter.with is required"):
        _validate_listen_capability({"to": {"one": {"with": {}}}, "foreach": {}}, "/do/0/listen")
    with pytest.raises(WorkflowSemanticError):
        _validate_run_capability({}, "/do/0/run")
    with pytest.raises(UnsupportedWorkflowFeature):
        _validate_run_capability({"script": {}}, "/do/0/run")
    with pytest.raises(WorkflowSemanticError):
        _validate_run_capability({"workflow": {"name": "missing"}}, "/do/0/run")


@pytest.mark.asyncio
async def test_plan_executes_set_switch_for_fork_and_calls(services):
    services.model.response = {"label": "called", "items": []}
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
                                "when": '${ .kind == "yes" }',
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
    assert result == [{"left": 1}, {"right": 2}]


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
                    "try": [
                        {
                            "fail": {
                                "raise": {
                                    "error": {
                                        "type": "urn:test:controlled",
                                        "status": 500,
                                        "detail": "controlled failure",
                                    }
                                }
                            }
                        }
                    ],
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
                        "schema": {
                            "document": {
                                "type": "object",
                                "properties": {"value": {"type": "integer"}},
                                "required": ["value"],
                            }
                        },
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


def test_derived_scope_plan_is_immutable_and_preserves_nested_task_references():
    from dataclasses import FrozenInstanceError

    from open_workflow_agent.workflow import generate_default_workflow

    workflow = generate_default_workflow()
    workflow["do"] = [
        {"loop": {"for": {"in": "${ [] }"}, "do": [{"a/b~c": {"set": {"ok": True}}}]}},
        {
            "branches": {
                "fork": {"branches": [{"child": {"do": [{"deep": {"set": {"ok": True}}}]}}]}
            }
        },
    ]
    plan = compile_workflow(workflow)
    assert plan.tasks[0].kind == "for"
    assert [scope.reference for scope in plan.scopes] == [
        "/do",
        "/do/0/loop/do",
        "/do/1/branches/fork/branches/0",
        "/do/1/branches/fork/branches/0/child/do",
    ]
    assert plan.scopes[1].tasks[0].reference == "/do/0/loop/do/0/a~1b~0c"
    assert not plan.scopes[2].indexed
    with pytest.raises(FrozenInstanceError):
        plan.scopes[0].reference = "/changed"
    decoded = plan.scopes[1].tasks[0].definition
    decoded["set"]["ok"] = False
    assert plan.scopes[1].tasks[0].definition["set"]["ok"] is True
    assert plan.source == workflow


def test_workflow_loaders_reject_non_json_yaml_and_unsafe_mapping_shapes(tmp_path):
    loaded = load_workflow_text("on: true\noff: false\nname: sample\n")
    assert loaded == {"on": True, "off": False, "name": "sample"}
    assert load_json_value("- 1\n- null\n") == [1, None]

    with pytest.raises(WorkflowSchemaError, match="unique strings"):
        load_workflow_text("name: first\nname: second\n")
    with pytest.raises(WorkflowSchemaError, match="invalid workflow YAML"):
        load_json_value("value: [unterminated\n")
    with pytest.raises(WorkflowSchemaError, match="root must be an object"):
        load_workflow_text("[1, 2]\n")
    with pytest.raises(WorkflowSchemaError, match="unable to read workflow"):
        load_workflow(tmp_path / "missing.yaml")
    workflow_file = tmp_path / "workflow.yaml"
    workflow_file.write_text("name: loaded\n", encoding="utf-8")
    assert load_workflow(workflow_file) == {"name": "loaded"}

    cyclic: dict[str, object] = {}
    cyclic["child"] = cyclic
    with pytest.raises(WorkflowSchemaError, match="acyclic JSON"):
        load_workflow(cyclic)
    with pytest.raises(WorkflowSchemaError, match="keys must be strings"):
        _validate_json_value({1: "invalid key"})
    with pytest.raises(WorkflowSchemaError, match="finite.*JSON"):
        _validate_json_value(float("nan"))
    with pytest.raises(WorkflowSchemaError, match="finite.*JSON"):
        _validate_json_value(object())
    with pytest.raises(WorkflowSchemaError, match="depth at most 64"):
        _validate_json_value(0, depth=65)


def test_path_get_handles_empty_mapping_sequence_and_invalid_indexes():
    value = {"items": [{"name": "Ada"}], "empty": None}
    assert _path_get(value, "") is value
    assert _path_get(value, "items[0].name") == "Ada"
    assert _path_get(value, "items[not-an-index]") is None
    assert _path_get(value, "items[4]") is None
    assert _path_get(value, "empty.name") is None
    assert _path_get({"items": "text"}, "items[0]") is None


def test_transition_controls_set_exit_end_continue_and_jump_state():
    state = ExecutionState(data={"value": 1})

    WorkflowExecutor._apply_transition("continue", state)
    assert state.transition_selected is True
    assert state.scope_exit is False
    state.transition_selected = False

    WorkflowExecutor._apply_transition("exit", state)
    assert state.scope_exit is True
    state.scope_exit = False
    state.transition_selected = False

    WorkflowExecutor._apply_transition("next", state)
    assert state.jump_to == "next"
    state.jump_to = None
    state.transition_selected = False

    WorkflowExecutor._apply_transition("end", state)
    assert state.stop is True
    assert state.flow.ended.is_set()
    assert state.flow.output == {"value": 1}
    assert state.flow.context == {}
    WorkflowExecutor._apply_transition("end", state)
    assert state.flow.output == {"value": 1}
    WorkflowExecutor._apply_transition(None, state)


def test_transition_validation_rejects_unknown_duplicate_and_multiple_defaults():
    with pytest.raises(WorkflowSemanticError, match="belong to the current scope"):
        _validate_transitions({"then": "missing"}, "/do/0/task", {"known"})
    with pytest.raises(WorkflowSemanticError, match="duplicate switch case"):
        _validate_transitions(
            {"switch": [{"same": {"when": "${ true }"}}, {"same": {}}]},
            "/do/0/task",
            set(),
        )
    with pytest.raises(WorkflowSemanticError, match="at most one default"):
        _validate_transitions({"switch": [{"first": {}}, {"second": {}}]}, "/do/0/task", set())


def test_task_output_and_export_transformations_validate_and_copy_data():
    executor = WorkflowExecutor(FakeModel())
    state = ExecutionState(
        data={"original": True},
        context={"prior": 1},
        variables={"task": {"name": "transform"}},
    )
    output = executor._apply_task_data(
        {
            "output": {"as": "${ { total: .total + 1 } }"},
            "export": {"as": "${ { saved: .total } }"},
        },
        {"total": 4},
        state,
    )
    assert output == {"total": 5}
    assert state.variables["task"]["output"] == {"total": 4}
    assert state.variables["output"] == {"total": 5}
    assert state.context == {"saved": 5}
    assert state.variables["context"] is state.context

    copied = executor._apply_task_data({}, {"nested": [1]}, state)
    copied["nested"].append(2)
    assert state.variables["output"] == {"nested": [1]}


@pytest.mark.asyncio
async def test_transitions_skip_tasks_and_exit_only_the_nested_scope(services):
    workflow = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "tests",
            "name": "control-flow",
            "version": "1.0.0",
        },
        "do": [
            {"jump": {"set": {"step": "jumped"}, "then": "target"}},
            {"skipped": {"set": {"step": "incorrect"}}},
            {
                "target": {
                    "do": [
                        {"inside": {"set": {"step": "inside"}, "then": "exit"}},
                        {"after_exit": {"set": {"step": "incorrect"}}},
                    ]
                }
            },
            {"after_scope": {"set": {"step": "after"}}},
        ],
    }
    result = await WorkflowExecutor(services.catalog, services=services).execute(
        compile_workflow(workflow), {}
    )
    assert result == {"step": "after"}


@pytest.mark.asyncio
async def test_task_sequence_rejects_malformed_items_and_missing_transition_targets(services):
    executor = WorkflowExecutor(services.catalog, services=services)
    for items, message in [
        (["not-a-task"], "invalid task at sequence index 0"),
        ([{"task": "not-an-object"}], "definition must be an object"),
    ]:
        with pytest.raises(WorkflowExecutionError, match=message):
            await executor._run_tasks(items, ExecutionState(data={}))

    state = ExecutionState(data={})
    with pytest.raises(WorkflowExecutionError, match="transition target not found"):
        await executor._run_tasks([{"jump": {"set": {"ok": True}, "then": "absent"}}], state)


@pytest.mark.asyncio
async def test_workflow_output_and_task_export_schemas_reject_invalid_results(services):
    output_workflow = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "tests",
            "name": "output-schema",
            "version": "1.0.0",
        },
        "do": [{"set_result": {"set": {"value": "wrong"}}}],
        "output": {
            "schema": {"document": {"type": "object", "properties": {"value": {"type": "integer"}}}}
        },
    }
    with pytest.raises(WorkflowSemanticError, match="workflow output schema"):
        await WorkflowExecutor(services.catalog, services=services).execute(
            compile_workflow(output_workflow), {}
        )

    export_workflow = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "tests",
            "name": "export-schema",
            "version": "1.0.0",
        },
        "do": [
            {
                "set_result": {
                    "set": {"value": "wrong"},
                    "export": {
                        "as": "${ . }",
                        "schema": {"document": {"type": "string"}},
                    },
                }
            }
        ],
    }
    with pytest.raises(WorkflowSemanticError, match="task export schema"):
        await WorkflowExecutor(services.catalog, services=services).execute(
            compile_workflow(export_workflow), {}
        )
