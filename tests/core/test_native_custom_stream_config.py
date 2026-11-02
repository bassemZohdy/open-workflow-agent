"""Declared custom stream bindings are immutable, bounded and admitted explicitly."""

import copy

import pytest
from open_workflow_agent.errors import WorkflowSemanticError
from open_workflow_agent.workflow import compile_workflow, generate_default_workflow


def definition():
    value = generate_default_workflow()
    value["document"]["metadata"] = {
        "owa": {
            "nativeExtensions": [
                {"name": "langgraph.streams", "version": "1.0.0"},
                {"name": "langgraph.streams.custom", "version": "1.0.0"},
            ],
            "nativeExtensionData": {
                "langgraph.streams.custom@1.0.0": {
                    "events": [
                        {
                            "task": "/do/0",
                            "name": "progress",
                            "data": {"literal": {"value": "${ . }"}},
                        }
                    ]
                }
            },
        }
    }
    return value


def test_custom_bindings_are_typed_immutable_and_detached_from_source():
    source = definition()
    plan = compile_workflow(source)
    event = plan.native_custom_stream.events[0]
    assert event.task == "/do/0/respond" and event.name == "progress"
    data = event.data
    data["value"] = "changed"
    source["document"]["metadata"]["owa"]["nativeExtensionData"]["langgraph.streams.custom@1.0.0"][
        "events"
    ][0]["data"] = None
    assert event.data == {"value": "${ . }"}


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "base_optional",
        "custom_optional",
        "shape",
        "empty",
        "too_many",
        "task",
        "long_task",
        "name",
        "duplicate",
        "expression",
        "variable",
        "oversized",
        "aggregate",
    ],
)
def test_custom_bindings_reject_unsupported_or_unbounded_configuration(damage):
    source = definition()
    owa = source["document"]["metadata"]["owa"]
    payload = owa["nativeExtensionData"]["langgraph.streams.custom@1.0.0"]
    event = payload["events"][0]
    if damage == "missing":
        owa.pop("nativeExtensionData")
    elif damage == "base_optional":
        owa["nativeExtensions"][0]["required"] = False
    elif damage == "custom_optional":
        owa["nativeExtensions"][1]["required"] = False
    elif damage == "shape":
        event["extra"] = True
    elif damage == "empty":
        payload["events"] = []
    elif damage == "too_many":
        payload["events"] = [copy.deepcopy(event) for _ in range(65)]
    elif damage == "task":
        event["task"] = "/do/99"
    elif damage == "long_task":
        event["task"] = "/do/" + "9" * 10_000
    elif damage == "name":
        event["name"] = "bad/name"
    elif damage == "duplicate":
        payload["events"].append(copy.deepcopy(event))
    elif damage == "expression":
        event["data"] = "${ [ }"
    elif damage == "variable":
        event["data"] = {"from": "${ $workflow }"}
    elif damage == "oversized":
        event["data"] = {"literal": "x" * 65_537}
    elif damage == "aggregate":
        payload["events"] = [
            {"task": "/do/0", "name": f"event{i}", "data": {"literal": "x" * 60_000}}
            for i in range(5)
        ]
    with pytest.raises(WorkflowSemanticError):
        compile_workflow(source)


def test_optional_custom_capability_without_bindings_collects_no_custom_events():
    source = definition()
    owa = source["document"]["metadata"]["owa"]
    owa["nativeExtensions"][1]["required"] = False
    owa.pop("nativeExtensionData")
    assert compile_workflow(source).native_custom_stream is None
