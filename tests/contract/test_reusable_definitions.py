from __future__ import annotations

import copy
import json

import pytest
from engine_cases import engine_cases
from open_workflow_agent.errors import WorkflowSemanticError
from open_workflow_agent.workflow import compile_workflow, generate_default_workflow


def _workflow(functions, tasks, **components):
    workflow = generate_default_workflow()
    workflow["use"] = {"functions": functions, **components}
    workflow["do"] = tasks
    return workflow


async def _invoke(services, engine_name, engine_type, source, data):
    plan = compile_workflow(source)
    engine = engine_type()
    await engine.initialize(services)
    handle = services.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id="user",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    try:
        return await engine.invoke(await engine.compile(plan), handle, data)
    finally:
        await engine.shutdown()


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.asyncio
async def test_reusable_body_parameters_pipeline_and_original_descriptors(
    services,
    engine_name,
    engine_type,
):
    function = {
        "input": {"from": "${ .value + 1 }"},
        "set": {
            "value": "${ . * 2 }",
            "raw": "${ $task.input }",
            "reference": "${ $task.reference }",
            "definition": "${ $task.definition }",
            "workflow": "${ $workflow.definition }",
        },
        "output": {"as": "${ . + {finished: true} }"},
        "export": {"as": "${ {saved: $output.value} }"},
    }
    workflow = _workflow(
        {"transform/name": function},
        [
            {
                "caller": {
                    "input": {"from": "${ .start }"},
                    "call": "transform/name",
                    "with": {"value": "${ . + 1 }", "literal": "${ $input }"},
                    "output": {"as": "${ . + {context: $context} }"},
                }
            },
        ],
    )
    original = copy.deepcopy(workflow)
    result = await _invoke(services, engine_name, engine_type, workflow, {"start": 2})
    assert result.status == "completed", result.error
    assert result.output == {
        "value": 8,
        "raw": {"value": 3, "literal": 2},
        "reference": "/use/functions/transform~1name",
        "definition": function,
        "workflow": original,
        "finished": True,
        "context": {"saved": 8},
    }
    assert workflow == original


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.asyncio
async def test_reusable_nested_scopes_and_calls_are_scoped_per_call_site(
    services,
    engine_name,
    engine_type,
):
    workflow = _workflow(
        {
            "increment": {"set": "${ . + 1 }"},
            "twice": {
                "do": [
                    {"first": {"call": "increment"}},
                    {"second": {"call": "increment"}},
                ]
            },
        },
        [{"one": {"call": "twice"}}, {"two": {"call": "twice"}}],
    )
    # No with means the transformed task input is the function's raw input.
    result = await _invoke(services, engine_name, engine_type, workflow, 1)
    assert result.status == "completed", result.error
    assert result.output == 5
    refs = [
        event.task_reference
        for event in services.events.events
        if event.event_type == "TaskCompleted"
    ]
    assert refs.count("/use/functions/increment") == 4
    assert "/use/functions/twice/do/0/first" in refs
    assert "/do/0/one" in refs and "/do/1/two" in refs


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.asyncio
async def test_named_error_retry_and_timeout_preserve_original_definition(
    services,
    engine_name,
    engine_type,
):
    services.model.failures = 1
    services.model.response = {"ok": True}
    workflow = _workflow(
        {},
        [
            {
                "attempt": {
                    "try": [{"request": {"call": "llm:1.0.0@default", "timeout": "short"}}],
                    "catch": {"retry": "once"},
                }
            },
            {
                "recover": {
                    "try": [{"fail": {"raise": {"error": "failure"}}}],
                    "catch": {
                        "errors": {"with": {"status": 422}},
                        "do": [
                            {
                                "handled": {
                                    "set": {
                                        "message": "${ $error.detail }",
                                        "instance": "${ $error.instance }",
                                    }
                                }
                            },
                        ],
                    },
                }
            },
        ],
        errors={"failure": {"type": "urn:test:failure", "status": 422, "detail": "named"}},
        retries={"once": {"limit": {"attempt": {"count": 1}}}},
        timeouts={"short": {"after": {"seconds": 5}}},
    )
    workflow["timeout"] = "short"
    plan = compile_workflow(workflow)
    assert plan.source == workflow
    assert plan.execution_source["timeout"] == {"after": {"seconds": 5}}
    result = await _invoke(services, engine_name, engine_type, workflow, {})
    assert result.status == "completed", result.error
    assert result.output == {"message": "named", "instance": "/do/1/recover/try/0/fail"}
    assert len(services.model.calls) == 2


@pytest.mark.parametrize(
    "section,task,reference",
    [
        ("timeouts", {"set": {"ok": True}, "timeout": "absent"}, "/do/0/caller/timeout"),
        ("errors", {"raise": {"error": "absent"}}, "/do/0/caller/raise/error"),
        (
            "retries",
            {"try": [{"noop": {"set": {"ok": True}}}], "catch": {"retry": "absent"}},
            "/do/0/caller/catch/retry",
        ),
    ],
)
def test_missing_component_references_fail_before_execution(section, task, reference):
    workflow = _workflow({}, [{"caller": task}], **{section: {}})
    with pytest.raises(WorkflowSemanticError) as error:
        compile_workflow(workflow)
    assert error.value.details == {"reference": reference}


@pytest.mark.parametrize(
    "functions",
    [
        {"self": {"call": "self"}},
        {"one": {"call": "two"}, "two": {"do": [{"back": {"call": "one"}}]}},
        {"http": {"set": {"ok": True}}},
        {"agent:1.0.0@default": {"set": {"ok": True}}},
    ],
)
def test_unused_recursive_or_shadowing_functions_fail_admission(functions):
    workflow = _workflow(functions, [{"noop": {"set": {"ok": True}}}])
    with pytest.raises(WorkflowSemanticError):
        compile_workflow(workflow)


def test_immutable_resolution_round_trip_and_parameter_data_are_not_code():
    workflow = _workflow({"body": {"set": "${ . }"}}, [{"caller": {"call": "body"}}])
    plan = compile_workflow(workflow)
    assert compile_workflow(json.loads(plan.source_json)).fingerprint == plan.fingerprint
    resolved = plan.execution_source
    resolved["use"]["functions"]["body"]["set"] = "changed"
    assert plan.execution_source["use"]["functions"]["body"]["set"] == "${ . }"
    assert (
        next(scope for scope in plan.scopes if scope.reference == "/do/0/caller/call")
        .tasks[0]
        .reference
        == "/use/functions/body"
    )


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.asyncio
async def test_reusable_operation_ids_are_distinct_for_parallel_call_sites(
    services, engine_name, engine_type
):
    calls = []

    async def protocol(call, payload):
        assert call == "http"
        calls.append(payload["operation_id"])
        return payload["body"]

    services.call_protocol = protocol
    workflow = _workflow(
        {
            "request": {
                "call": "http",
                "with": {
                    "method": "post",
                    "endpoint": "https://service.test/action",
                    "body": "${ . }",
                },
            }
        },
        [
            {
                "parallel": {
                    "fork": {
                        "branches": [
                            {"left": {"call": "request", "with": {"branch": "left"}}},
                            {"right": {"call": "request", "with": {"branch": "right"}}},
                        ]
                    }
                }
            }
        ],
    )
    result = await _invoke(services, engine_name, engine_type, workflow, {})
    assert result.status == "completed", result.error
    assert result.output == [{"branch": "left"}, {"branch": "right"}]
    assert len(calls) == len(set(calls)) == 2


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.asyncio
async def test_parameter_strings_are_not_reinterpreted_as_expressions(
    services, engine_name, engine_type
):
    workflow = _workflow({"echo": {"set": "${ . }"}}, [{"caller": {"call": "echo"}}])
    data = {"code": "${ env }", "call": "untrusted:1.0.0@remote"}
    result = await _invoke(services, engine_name, engine_type, workflow, data)
    assert result.status == "completed", result.error
    assert result.output == data


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.asyncio
async def test_reusable_retry_events_keep_the_failing_operation_identity(
    services, engine_name, engine_type
):
    from open_workflow_agent.errors import ToolError

    calls = []

    async def protocol(call, payload):
        calls.append(payload["operation_id"])
        if len(calls) % 2:
            raise ToolError("controlled failure")
        return {"ok": True}

    services.call_protocol = protocol
    workflow = _workflow(
        {
            "attempt": {
                "try": [
                    {
                        "effect": {
                            "call": "http",
                            "with": {"method": "post", "endpoint": "https://service.test/action"},
                        }
                    }
                ],
                "catch": {"retry": {"limit": {"attempt": {"count": 1}}}},
            }
        },
        [{"one": {"call": "attempt"}}, {"two": {"call": "attempt"}}],
    )
    result = await _invoke(services, engine_name, engine_type, workflow, {})
    assert result.status == "completed", result.error
    assert (
        len(calls) == 4 and calls[0] == calls[1] and calls[2] == calls[3] and calls[0] != calls[2]
    )
    events = [event for event in services.events.events if event.event_type == "TaskRetried"]
    assert [event.operation_id for event in events] == [calls[0], calls[2]]
    assert all(event.task_reference == "/use/functions/attempt/try/0/effect" for event in events)
    faults = [event.error for event in services.events.events if event.event_type == "TaskFaulted"]
    assert all("_operation_id" not in json.dumps(error) for error in faults)
