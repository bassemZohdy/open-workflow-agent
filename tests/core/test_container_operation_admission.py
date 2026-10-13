"""Selected restricted-controller policies reject unsupported OWS fields early."""

from __future__ import annotations

import copy

import pytest
from open_workflow_agent.config import SandboxConfig
from open_workflow_agent.errors import UnsupportedWorkflowFeature
from open_workflow_agent.sandbox import compile_sandbox_workflow

IMAGE = "registry.example/worker@sha256:" + "a" * 64


def configuration(backend):
    policy = {"allowed_images": [IMAGE]}
    if backend == "kubernetes":
        policy["network_policy_enforced"] = True
        policy["process_limit_enforced"] = True
    return SandboxConfig.model_validate({"enabled": True, "backend": backend, backend: policy})


def workflow(container, reusable):
    result = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "admission",
            "name": "container",
            "version": "1.0.0",
        },
        "do": [{"action": {"run": {"container": {"image": IMAGE, **container}}}}],
    }
    if reusable:
        result["use"] = {"functions": {"execute": result["do"][0]["action"]}}
        result["do"] = [{"action": {"call": "execute"}}]
    return result


@pytest.mark.parametrize("backend", ["docker", "kubernetes"])
@pytest.mark.parametrize("reusable", [False, True])
@pytest.mark.parametrize("policy", ["ifNotPresent", "always", "never"])
def test_explicit_pull_policy_matches_the_actual_selected_controller(backend, reusable, policy):
    source = workflow({"pullPolicy": policy}, reusable)
    original = copy.deepcopy(source)
    supported = "never" if backend == "docker" else "ifNotPresent"
    if policy == supported:
        plan = compile_sandbox_workflow(source, sandbox=configuration(backend))
        assert plan.name == "container"
    else:
        with pytest.raises(UnsupportedWorkflowFeature, match="pull policy") as error:
            compile_sandbox_workflow(source, sandbox=configuration(backend))
        assert (
            "action" in error.value.details["reference"]
            or "execute" in error.value.details["reference"]
        )
    assert source == original


@pytest.mark.parametrize("backend", ["docker", "kubernetes"])
@pytest.mark.parametrize("reusable", [False, True])
@pytest.mark.parametrize(
    "lifetime",
    [
        {"cleanup": "never"},
        {"cleanup": "always"},
        {"cleanup": "eventually", "after": {"seconds": 5}},
    ],
)
def test_container_lifetime_is_not_silently_dropped(backend, reusable, lifetime):
    source = workflow({"lifetime": lifetime}, reusable)
    original = copy.deepcopy(source)
    with pytest.raises(UnsupportedWorkflowFeature, match="lifetime"):
        compile_sandbox_workflow(source, sandbox=configuration(backend))
    assert source == original


@pytest.mark.parametrize("reusable", [False, True])
@pytest.mark.parametrize("stdin", ["", "payload", "${ .payload }"])
def test_kubernetes_stdin_rejects_before_execution_admission(reusable, stdin):
    source = workflow({"stdin": stdin}, reusable)
    with pytest.raises(UnsupportedWorkflowFeature, match="stdin"):
        compile_sandbox_workflow(source, sandbox=configuration("kubernetes"))
    assert compile_sandbox_workflow(source, sandbox=configuration("docker")).name == "container"
