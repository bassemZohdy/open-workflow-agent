from __future__ import annotations

import copy
import json
from datetime import UTC, datetime

import pytest
from engine_cases import engine_cases
from open_workflow_agent import __version__
from open_workflow_agent.errors import ExpressionError, WorkflowSemanticError
from open_workflow_agent.workflow import compile_workflow, generate_default_workflow


def _workflow(tasks, **sections):
    workflow = generate_default_workflow()
    workflow["do"] = tasks
    workflow.update(sections)
    return workflow


async def _invoke(services, engine_name, engine_type, workflow, data):
    plan = compile_workflow(workflow)
    engine = engine_type()
    await engine.initialize(services)
    handle = services.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id="real-user",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    try:
        result = await engine.invoke(await engine.compile(plan), handle, data)
        return result, handle
    finally:
        await engine.shutdown()


def _assert_date_descriptor(value):
    stamp = datetime.fromisoformat(value["iso8601"]).astimezone(UTC)
    assert isinstance(value["epoch"]["seconds"], int)
    assert value["epoch"]["seconds"] == int(stamp.timestamp())
    assert value["epoch"]["milliseconds"] == round(stamp.timestamp() * 1000)


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.asyncio
async def test_raw_transform_output_export_context_and_descriptors(
    services, engine_name, engine_type
):
    services.model.response = lambda payload: payload
    data = {"payload": {"value": 4, "keep": True}, "outer": "original"}
    original = copy.deepcopy(data)
    workflow = _workflow(
        [
            {
                "prepare": {
                    "input": {
                        "schema": {"document": {"type": "object", "required": ["value"]}},
                        "from": "${ .value }",
                    },
                    "call": "llm:1.0.0@default",
                    "with": {
                        "value": "${ . + 1 }",
                        "input": "${ $input }",
                        "raw": "${ $task.input }",
                        "context": "${ $context }",
                        "name": "${ $task.name }",
                        "reference": "${ $task.reference }",
                    },
                    "output": {
                        "as": "${ {computed: .value * 2, input: $input, raw: $task.output} }",
                        "schema": {"document": {"type": "object", "required": ["computed"]}},
                    },
                    "export": {
                        "as": (
                            "${ {stored: $output.computed, current: .computed, "
                            "original: $context.value} }"
                        ),
                        "schema": {
                            "document": {
                                "type": "object",
                                "required": ["stored", "current", "original"],
                                "additionalProperties": True,
                            }
                        },
                    },
                }
            },
            {
                "verify": {
                    "set": {
                        "context": "${ $context }",
                        "previous": "${ . }",
                        "input": "${ $input }",
                        "raw": "${ $task.input }",
                        "workflowInput": "${ $workflow.input }",
                        "workflowId": "${ $workflow.id }",
                        "workflowName": "${ $workflow.definition.document.name }",
                        "runtime": "${ $runtime.name }",
                        "runtimeVersion": "${ $runtime.version }",
                        "engine": "${ $runtime.metadata.engine }",
                        "workflowStarted": "${ $workflow.startedAt }",
                        "taskStarted": "${ $task.startedAt }",
                        "taskName": "${ $task.name }",
                        "taskReference": "${ $task.reference }",
                        "taskDefinition": "${ $task.definition }",
                    }
                }
            },
        ],
        input={
            "from": "${ .payload }",
            "schema": {"document": {"type": "object", "required": ["payload"]}},
        },
    )
    result, handle = await _invoke(services, engine_name, engine_type, workflow, data)
    assert result.status == "completed", result.error
    expected_call = {
        "value": 5,
        "input": 4,
        "raw": data["payload"],
        "context": data["payload"],
        "name": "prepare",
        "reference": "/do/0/prepare",
    }
    assert services.model.calls == [expected_call]
    expected_previous = {"computed": 10, "input": 4, "raw": expected_call}
    output = result.output
    assert output["context"] == {"stored": 10, "current": 10, "original": 4}
    assert output["previous"] == output["input"] == output["raw"] == expected_previous
    assert output["workflowInput"] == original
    assert output["workflowId"] == handle.invocation_id
    assert output["workflowName"] == workflow["document"]["name"]
    assert output["runtime"] == "open-workflow-agent"
    assert output["runtimeVersion"] == __version__
    assert output["engine"] == engine_name
    assert output["taskName"] == "verify"
    assert output["taskReference"] == "/do/1/verify"
    assert output["taskDefinition"] == workflow["do"][1]["verify"]
    _assert_date_descriptor(output["workflowStarted"])
    _assert_date_descriptor(output["taskStarted"])
    assert data == original
    assert handle.user_id == "real-user"
    assert handle.status == "completed"


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("context", [None, False, 7, "text", [1, 2], {"only": 3}])
@pytest.mark.asyncio
async def test_export_replaces_context_with_any_json_type_without_changing_identity(
    services, engine_name, engine_type, context
):
    workflow = _workflow(
        [
            {
                "replace": {
                    "set": {"done": True},
                    "export": {"as": "${ " + json.dumps(context) + " }"},
                }
            },
            {"read": {"set": {"context": "${ $context }", "id": "${ $workflow.id }"}}},
        ]
    )
    result, handle = await _invoke(
        services,
        engine_name,
        engine_type,
        workflow,
        {"invocation_id": "spoof", "user_id": "spoof", "_lifecycle": "ordinary data"},
    )
    assert result.status == "completed", result.error
    assert result.output == {"context": context, "id": handle.invocation_id}
    assert handle.user_id == "real-user"
    events = [event for event in services.events.events if event.task_reference]
    assert events and all(event.invocation_id == handle.invocation_id for event in events)


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.asyncio
async def test_nested_tasks_keep_parent_input_descriptor_and_exported_context(
    services, engine_name, engine_type
):
    workflow = _workflow(
        [
            {
                "outer": {
                    "input": {"from": "${ .payload }"},
                    "do": [
                        {
                            "inner": {
                                "input": {"from": "${ .value }"},
                                "set": {"inner": "${ . + 1 }"},
                                "export": {"as": "${ {child: .inner} }"},
                            }
                        }
                    ],
                    "output": {
                        "as": (
                            "${ {total: .inner + $input.value, input: $input, context: $context, "
                            "name: $task.name, reference: $task.reference, raw: $task.output} }"
                        )
                    },
                }
            }
        ]
    )
    result, _ = await _invoke(
        services, engine_name, engine_type, workflow, {"payload": {"value": 4}}
    )
    assert result.status == "completed", result.error
    assert result.output == {
        "total": 9,
        "input": {"value": 4},
        "context": {"child": 5},
        "name": "outer",
        "reference": "/do/0/outer",
        "raw": {"inner": 5},
    }
    completed = [
        event.task_reference
        for event in services.events.events
        if event.event_type == "TaskCompleted"
    ]
    assert completed == ["/do/0/outer/do/0/inner", "/do/0/outer"]


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.asyncio
async def test_skipped_task_preserves_raw_data_without_schema_transform_export_or_effects(
    services, engine_name, engine_type
):
    workflow = _workflow(
        [
            {
                "skip": {
                    "if": "${ false }",
                    "input": {"schema": {"document": False}, "from": '${ error("input") }'},
                    "call": "llm:1.0.0@default",
                    "output": {"as": '${ error("output") }'},
                    "export": {"as": '${ error("export") }'},
                }
            },
            {"observe": {"set": {"input": "${ . }", "context": "${ $context }"}}},
        ]
    )
    result, _ = await _invoke(services, engine_name, engine_type, workflow, [1, {"a": True}])
    assert result.status == "completed", result.error
    assert result.output == {"input": [1, {"a": True}], "context": [1, {"a": True}]}
    assert services.model.calls == []


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("phase", ["input", "output", "export", "workflow_output"])
@pytest.mark.asyncio
async def test_schema_failure_has_pointer_lifecycle_and_no_raw_data_leak(
    services, engine_name, engine_type, phase
):
    task = {"call": "llm:1.0.0@default"}
    sections = {}
    if phase == "input":
        task["input"] = {
            "schema": {
                "document": {"type": "object", "properties": {"secret": {"type": "integer"}}}
            },
            "from": "${ 1 }",
        }
    elif phase == "workflow_output":
        sections["output"] = {"schema": {"document": False}}
    else:
        task[phase] = {"schema": {"document": False}}
    workflow = _workflow([{"validate": task}], **sections)
    result, _ = await _invoke(
        services, engine_name, engine_type, workflow, {"secret": "secret-value"}
    )
    assert result.status == "faulted"
    assert result.error and result.error["code"] == "workflow_semantic_error"
    assert result.error["details"]["type"].endswith("/errors/validation")
    assert result.error["details"]["status"] == 400
    assert "secret-value" not in json.dumps(result.error)
    event_types = [event.event_type for event in services.events.events]
    assert "WorkflowFaulted" in event_types and "WorkflowCompleted" not in event_types
    if phase == "workflow_output":
        assert "TaskCompleted" in event_types
    else:
        assert result.error["details"]["instance"] == "/do/0/validate"
        assert "TaskFaulted" in event_types and "TaskCompleted" not in event_types
    if phase == "input":
        assert services.model.calls == []


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.asyncio
async def test_branch_payload_and_context_are_immutable_snapshots(
    services, engine_name, engine_type
):
    def mutate(payload):
        before = copy.deepcopy(payload)
        payload["values"].append(99)
        payload["context"]["values"].append(99)
        return {payload["branch"]: before}

    services.model.response = mutate
    workflow = _workflow(
        [
            {
                "fork": {
                    "fork": {
                        "branches": [
                            {
                                name: {
                                    "call": "llm:1.0.0@default",
                                    "with": {
                                        "branch": name,
                                        "values": "${ .values }",
                                        "context": "${ $context }",
                                    },
                                }
                            }
                            for name in ("left", "right")
                        ]
                    }
                }
            }
        ]
    )
    data = {"values": [1]}
    result, _ = await _invoke(services, engine_name, engine_type, workflow, data)
    assert result.status == "completed", result.error
    assert result.output == [
        {name: {"branch": name, "values": [1], "context": {"values": [1]}}}
        for name in ("left", "right")
    ]
    assert data == {"values": [1]}


@pytest.mark.parametrize(
    "phase,variable",
    [
        ("workflow_input", "input"),
        ("workflow_input", "context"),
        ("workflow_input", "task"),
        ("workflow_output", "task"),
        ("workflow_output", "input"),
        ("workflow_output", "output"),
        ("if", "input"),
        ("input", "input"),
        ("set", "output"),
        ("output", "output"),
        ("set", "authorization"),
        ("input", "secrets"),
    ],
)
def test_unavailable_phase_arguments_fail_before_effects(phase, variable):
    expression = "${ $" + variable + " }"
    task = {"set": {"ok": True}}
    workflow = _workflow([{"bad": task}])
    if phase.startswith("workflow_"):
        section = phase.removeprefix("workflow_")
        workflow[section] = {"from" if section == "input" else "as": expression}
    elif phase in {"input", "output"}:
        task[phase] = {"from" if phase == "input" else "as": expression}
    else:
        task[phase] = expression if phase == "if" else {"value": expression}
    with pytest.raises(ExpressionError):
        compile_workflow(workflow)


@pytest.mark.parametrize(
    "binding",
    [
        "context",
        "input",
        "output",
        "task",
        "workflow",
        "runtime",
        "ENV",
        "authorization",
        "secrets",
        "_task_reference",
        "_task_name",
        "invalid-name",
    ],
)
def test_reserved_or_invalid_loop_bindings_fail_admission(binding):
    workflow = _workflow(
        [{"loop": {"for": {"each": binding, "in": [1]}, "do": [{"body": {"set": {"ok": True}}}]}}]
    )
    with pytest.raises(WorkflowSemanticError):
        compile_workflow(workflow)


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.asyncio
async def test_workflow_input_descriptor_and_scalar_initial_context(
    services, engine_name, engine_type
):
    workflow = _workflow(
        [{"read": {"set": {"input": "${ $input }", "context": "${ $context }"}}}],
        input={"from": "${ $workflow.input.value + ($runtime.version | length) }"},
        output={"as": "${ {task: ., context: $context, raw: $workflow.input} }"},
    )
    result, _ = await _invoke(services, engine_name, engine_type, workflow, {"value": 2})
    assert result.status == "completed", result.error
    initial = 2 + len(__version__)
    assert result.output == {
        "task": {"input": initial, "context": initial},
        "context": initial,
        "raw": {"value": 2},
    }


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.asyncio
async def test_sequential_loop_exports_replace_context_for_next_iteration_and_task(
    services, engine_name, engine_type
):
    workflow = _workflow(
        [
            {
                "loop": {
                    "for": {"each": "number", "in": [1, 2]},
                    "do": [
                        {
                            "accumulate": {
                                "set": {"sum": "${ $context.sum + $number }"},
                                "export": {"as": "${ . }"},
                            }
                        }
                    ],
                }
            },
            {"read": {"set": {"context": "${ $context }", "input": "${ $input }"}}},
        ]
    )
    result, _ = await _invoke(services, engine_name, engine_type, workflow, {"sum": 0})
    assert result.status == "completed", result.error
    assert result.output == {"context": {"sum": 3}, "input": {"sum": 3}}


@pytest.mark.parametrize("binding", ["context", "task", "workflow", "input", "invalid-name"])
def test_reserved_or_invalid_catch_binding_fails_admission(binding):
    workflow = _workflow(
        [{"guard": {"try": [{"action": {"set": {"ok": True}}}], "catch": {"as": binding}}}]
    )
    with pytest.raises(WorkflowSemanticError):
        compile_workflow(workflow)
