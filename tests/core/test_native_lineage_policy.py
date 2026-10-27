"""Declared replay/branch policy remains immutable, explicit and unadvertised."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace

import pytest
from open_workflow_agent.errors import OwaError
from open_workflow_agent.native_extensions import (
    LANGGRAPH_STATE_BRANCH,
    LANGGRAPH_STATE_REPLAY,
    LANGGRAPH_STATE_UPDATE,
)
from open_workflow_agent.native_lineage_policy import NativeLineagePolicy
from open_workflow_agent.state_lineage import StateLineageRequest
from open_workflow_agent.workflow import compile_workflow, generate_default_workflow


def definition(kind="replay", effect="read_only"):
    source = generate_default_workflow()
    source["do"] = [{"first": {"set": {"value": 1}}}]
    capability = LANGGRAPH_STATE_REPLAY if kind == "replay" else LANGGRAPH_STATE_BRANCH
    field = "replayEffectPolicy" if kind == "replay" else "branchEffectPolicy"
    owa = {
        "nativeExtensions": [{"name": "langgraph.state." + kind, "version": "1.0.0"}],
        "nativeExtensionData": {
            capability: {"authorizationPolicyRef": "lineage-tests", field: effect}
        },
    }
    if kind == "branch":
        owa["nativeExtensions"].append({"name": "langgraph.state.update", "version": "1.0.0"})
        owa["nativeExtensionData"][LANGGRAPH_STATE_UPDATE] = {
            "allowedTaskRefs": ["/do/0"],
            "updateSchema": {"type": "object"},
            "authorizationPolicyRef": "lineage-tests",
        }
    source["document"]["metadata"] = {"owa": owa}
    return source


@pytest.mark.parametrize("kind", ["replay", "branch"])
@pytest.mark.parametrize("effect", ["read_only", "reexecute"])
def test_declared_policy_is_detached_frozen_and_fingerprinted(kind, effect):
    source = definition(kind, effect)
    original = deepcopy(source)
    plan = compile_workflow(source)
    policy = plan.native_state_replay if kind == "replay" else plan.native_state_branch
    assert policy == NativeLineagePolicy(kind, "lineage-tests", effect)
    source["document"]["metadata"]["owa"]["nativeExtensionData"].clear()
    assert plan.source == original
    assert compile_workflow(original).fingerprint == plan.fingerprint
    assert (
        replace(policy, effect_policy="reexecute" if effect == "read_only" else "read_only").digest
        != policy.digest
    )
    with pytest.raises(FrozenInstanceError):
        policy.effect_policy = "reexecute"


@pytest.mark.parametrize("kind", ["replay", "branch"])
@pytest.mark.parametrize(
    "damage", ["missing", "optional", "unknown_version", "extra", "mode", "policy", "mismatch"]
)
def test_policy_requires_exact_declared_fields_versions_and_bindings(kind, damage):
    source = definition(kind)
    owa = source["document"]["metadata"]["owa"]
    capability = LANGGRAPH_STATE_REPLAY if kind == "replay" else LANGGRAPH_STATE_BRANCH
    if damage == "missing":
        owa["nativeExtensionData"].pop(capability)
    elif damage == "optional":
        owa["nativeExtensions"][0]["required"] = False
    elif damage == "unknown_version":
        owa["nativeExtensions"][0]["version"] = "2.0.0"
    elif damage == "extra":
        owa["nativeExtensionData"][capability]["enabled"] = True
    elif damage == "mode":
        field = "replayEffectPolicy" if kind == "replay" else "branchEffectPolicy"
        owa["nativeExtensionData"][capability][field] = "automatic"
    elif damage == "policy":
        owa["nativeExtensionData"][capability]["authorizationPolicyRef"] = "x\n"
    elif kind == "branch":
        owa["nativeExtensionData"][LANGGRAPH_STATE_UPDATE]["authorizationPolicyRef"] = "different"
    else:
        owa["nativeExtensionData"][capability]["authorizationPolicyRef"] = []
    with pytest.raises(OwaError):
        compile_workflow(source)


def request(kind="replay", **changes):
    fields = dict(
        source_invocation_id="source",
        target_invocation_id="target",
        workflow_fingerprint="fp",
        request_id="1" * 64,
        source_reference="2" * 64,
        source_basis_digest="3" * 64,
        policy_digest="4" * 64,
        storage_identity="5" * 64,
        kind=kind,
        effect_policy="read_only",
        acknowledge_reexecution=False,
    )
    if kind == "branch":
        fields.update(task_reference="/do/0/first", values_json='{"value":7}')
    return StateLineageRequest(**(fields | changes))


@pytest.mark.parametrize("kind", ["replay", "branch"])
def test_request_identity_is_fixed_when_target_policy_or_values_change(kind):
    original = request(kind)
    assert StateLineageRequest.from_descriptor(original.descriptor) == original
    changed = replace(original, target_invocation_id="different")
    assert (
        changed.identity == original.identity and changed.request_digest != original.request_digest
    )
    with pytest.raises(OwaError):
        StateLineageRequest.from_descriptor(original.descriptor | {"checkpoint_id": "private"})
    if kind == "branch":
        values = original.values
        values["value"] = 99
        assert original.values == {"value": 7}


@pytest.mark.parametrize(
    "changes",
    [
        {"target_invocation_id": "source"},
        {"target_invocation_id": ""},
        {"source_reference": "invalid"},
        {"source_basis_digest": "bad"},
        {"request_id": []},
        {"kind": []},
        {"effect_policy": []},
        {"effect_policy": "reexecute"},
        {"acknowledge_reexecution": True},
        {"acknowledge_reexecution": 1},
        {"task_reference": "/do/0/first", "values_json": "{}"},
    ],
)
def test_replay_command_needs_new_identity_complete_pins_and_explicit_effect_choice(changes):
    with pytest.raises(OwaError):
        request(**changes)


def test_reexecution_is_explicit_and_old_plan_fingerprint_meaning_is_unchanged():
    acknowledged = request(effect_policy="reexecute", acknowledge_reexecution=True)
    assert acknowledged.effect_policy == "reexecute"
    plan = compile_workflow(generate_default_workflow())
    assert plan.native_state_replay is None and plan.native_state_branch is None
    assert (
        replace(
            plan, native_state_replay=NativeLineagePolicy("replay", "policy", "read_only")
        ).fingerprint
        == plan.fingerprint
    )
