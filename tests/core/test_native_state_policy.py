"""OWS metadata carries a finite immutable policy, while mutation admission stays closed."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy

import pytest
from open_workflow_agent.errors import OwaError, UnsupportedWorkflowFeature, WorkflowSemanticError
from open_workflow_agent.native_state_policy import CAPABILITY
from open_workflow_agent.workflow import compile_workflow, generate_default_workflow


def source():
    value = generate_default_workflow()
    value["do"] = [{"first": {"set": {"value": 1}}}, {"pause": {"wait": {"seconds": 10}}}]
    value["document"]["metadata"] = {
        "owa": {
            "nativeExtensions": [
                {"name": "langgraph.state.update", "version": "1.0.0", "required": True}
            ],
            "nativeExtensionData": {
                CAPABILITY: {
                    "allowedTaskRefs": ["/do/0"],
                    "authorizationPolicyRef": "operators",
                    "updateSchema": {
                        "type": "object",
                        "properties": {"value": {"type": "integer"}},
                        "required": ["value"],
                        "additionalProperties": False,
                    },
                }
            },
        }
    }
    return value


def payload(value):
    return value["document"]["metadata"]["owa"]["nativeExtensionData"][CAPABILITY]


def test_policy_is_immutable_derived_and_does_not_change_legacy_fingerprints():
    original = generate_default_workflow()
    legacy = compile_workflow(original)
    assert legacy.native_state_updates is None
    assert legacy.fingerprint == hashlib.sha256(legacy.source_json.encode()).hexdigest()
    value = source()
    plan = compile_workflow(value)
    policy = plan.native_state_updates
    assert policy.allowed_task_refs == ("/do/0/first",)
    assert policy.validate("/do/0/first", {"value": 7}, "operators") == '{"value":7}'
    previous = policy.digest
    payload(value)["updateSchema"]["properties"]["value"]["type"] = "string"
    policy.schema["properties"]["value"]["type"] = "boolean"
    assert policy.digest == previous
    assert compile_workflow(value).fingerprint != plan.fingerprint
    assert plan.source["document"]["dsl"] == "1.0.3"


@pytest.mark.parametrize(
    "changes",
    [
        {"allowedTaskRefs": []},
        {"allowedTaskRefs": ["/do/0"] * 65},
        {"allowedTaskRefs": ["/do/0", "/do/0"]},
        {"allowedTaskRefs": ["/do/99"]},
        {"allowedTaskRefs": ["/do/" + "9" * 5000]},
        {"allowedTaskRefs": ["/do/0/first"]},
        {"allowedTaskRefs": [True]},
        {"authorizationPolicyRef": ""},
        {"authorizationPolicyRef": "x\n"},
        {"authorizationPolicyRef": "x" * 129},
        {"updateSchema": []},
        {"updateSchema": {"type": "unrecognized"}},
        {"extra": True},
    ],
)
def test_invalid_policy_is_rejected_during_compile(changes):
    value = source()
    payload(value).update(changes)
    with pytest.raises(OwaError):
        compile_workflow(value)


@pytest.mark.parametrize("change", ["absent", "optional", "other_version", "missing_policy"])
def test_policy_needs_exact_required_declaration_and_data(change):
    value = source()
    owa = value["document"]["metadata"]["owa"]
    if change == "absent":
        owa["nativeExtensions"] = []
    elif change == "optional":
        owa["nativeExtensions"][0]["required"] = False
    elif change == "other_version":
        owa["nativeExtensions"][0]["version"] = "2.0.0"
    else:
        owa.pop("nativeExtensionData")
    with pytest.raises(WorkflowSemanticError):
        compile_workflow(value)


def test_non_set_attribution_is_not_silently_treated_as_completed_work():
    value = source()
    payload(value)["allowedTaskRefs"] = ["/do/1"]
    with pytest.raises(UnsupportedWorkflowFeature, match="set task"):
        compile_workflow(value)


@pytest.mark.parametrize(
    "schema",
    [
        {"$ref": "https://schema.test/remote"},
        {"$ref": "file:///private/schema.json"},
        {"$ref": "#/missing"},
        {"$schema": "http://json-schema.org/draft-07/schema#"},
    ],
)
def test_schemas_are_validated_with_an_offline_reference_closure(schema):
    value = source()
    payload(value)["updateSchema"] = schema
    with pytest.raises(OwaError):
        compile_workflow(value)


def test_schema_local_refs_and_literal_data_are_not_network_requests():
    value = source()
    payload(value)["updateSchema"] = {
        "$defs": {"count": {"type": "integer"}},
        "type": "object",
        "properties": {"value": {"$ref": "#/$defs/count"}},
        "examples": [{"$ref": "https://ordinary-data.test"}],
    }
    policy = compile_workflow(value).native_state_updates
    assert json.loads(policy.validate("/do/0/first", {"value": 3}, "operators")) == {"value": 3}


@pytest.mark.parametrize(
    "task,values,policy",
    [
        ("/do/1/pause", {"value": 3}, "operators"),
        ("/do/0/first", {"value": 3}, "another-policy"),
        ("/do/0/first", {"value": "three"}, "operators"),
        ("/do/0/first", {"value": 3, "extra": 1}, "operators"),
        ("/do/0/first", {}, "operators"),
        ("/do/0/first", [], "operators"),
        ("/do/0/first", {"value": float("nan")}, "operators"),
    ],
)
def test_update_values_and_attribution_must_match_the_exact_policy(task, values, policy):
    compiled = compile_workflow(source()).native_state_updates
    with pytest.raises(OwaError):
        compiled.validate(task, values, policy)


def test_channel_and_update_payloads_coexist_without_changing_channel_contract():
    value = source()
    owa = value["document"]["metadata"]["owa"]
    owa["nativeExtensions"].append({"name": "langgraph.channels", "version": "1.0.0"})
    owa["nativeExtensionData"]["langgraph.channels@1.0.0"] = {"channels": [], "reads": []}
    plan = compile_workflow(deepcopy(value))
    assert plan.native_channels is not None and plan.native_state_updates is not None
    owa["nativeExtensionData"]["unrecognized@1.0.0"] = {}
    with pytest.raises(WorkflowSemanticError):
        compile_workflow(value)
