from __future__ import annotations

import pytest
from open_workflow_agent.config import SandboxConfig
from open_workflow_agent.errors import UnsupportedWorkflowFeature, WorkflowSemanticError
from open_workflow_agent.sandbox import InternalSandboxBackend, SandboxManager
from open_workflow_agent.sandbox.validation import (
    _rewrite_executable_runs,
    _validate_environment,
    _validate_executable_run,
)
from sandbox_fixture_policy import supported_process_limits

_IMAGE = "registry.example/worker@sha256:" + ("a" * 64)


@pytest.mark.parametrize("enabled", [False, True])
def test_sandbox_return_capability_keeps_background_execution_disabled(enabled):
    backend = InternalSandboxBackend(SandboxConfig(**supported_process_limits(), enabled=enabled))
    original = backend.capabilities()
    projected = SandboxManager(backend).capabilities()
    assert projected["awaitedReturn"] == {
        "enabled": enabled,
        "default": "stdout",
        "modes": ["stdout", "stderr", "code", "all", "none"],
        "processResultFields": ["code", "stdout", "stderr"],
    }
    assert projected["backgroundExecution"] is False
    assert "awaitedReturn" not in original


@pytest.mark.parametrize("mode", [{"await": False}, {"return": "all"}])
def test_child_workflow_modes_are_not_rejected_as_external_process_modes(mode):
    run = {
        "workflow": {"namespace": "child", "name": "worker", "version": "1.0.0"},
        **mode,
    }
    assert _validate_executable_run(run, sandbox=SandboxConfig(), reference="/do/child") is None


@pytest.mark.parametrize("option", [{"await": False}, {"await": False, "return": "all"}])
def test_sandbox_rewrite_does_not_hide_unsupported_process_modes(option):
    config = SandboxConfig(enabled=True, backend="internal")
    with pytest.raises(UnsupportedWorkflowFeature, match="background"):
        _validate_executable_run(
            {"script": {"language": "python", "code": "print(1)"}, **option},
            sandbox=config,
            reference="/do/0/run",
        )


@pytest.mark.parametrize("mode", ["stdout", "stderr", "code", "all", "none"])
def test_awaited_return_selection_preserves_executable_policy_gate(mode):
    run = {"script": {"language": "python", "code": "print(1)"}, "return": mode}
    assert (
        _validate_executable_run(run, sandbox=SandboxConfig(enabled=True), reference="/do/0/action")
        is not None
    )
    with pytest.raises(UnsupportedWorkflowFeature, match="deployment-enabled"):
        _validate_executable_run(run, sandbox=SandboxConfig(), reference="/do/0/action")
    with pytest.raises(UnsupportedWorkflowFeature, match="background"):
        _validate_executable_run(
            {**run, "await": False},
            sandbox=SandboxConfig(enabled=True),
            reference="/do/0/action",
        )


@pytest.mark.parametrize("mode", [True, 0, None, [], "result", "${ .mode }"])
def test_invalid_return_selection_rejects_before_executable_dispatch(mode):
    with pytest.raises(WorkflowSemanticError, match="return mode"):
        _validate_executable_run(
            {"script": {"language": "python", "code": "print(1)"}, "return": mode},
            sandbox=SandboxConfig(enabled=True),
            reference="/do/0/action",
        )


def test_sandbox_validation_covers_script_shell_and_container_edges():
    internal = SandboxConfig(
        enabled=True,
        backend="internal",
        allow_shell=True,
        secret_environment=["TOKEN"],
    )
    assert (
        _validate_executable_run(
            {"script": {"language": "python", "code": "print(1)"}},
            sandbox=internal,
            reference="/run",
        )
        is not None
    )
    assert (
        _validate_executable_run(
            {"shell": {"command": "python"}}, sandbox=internal, reference="/run"
        )
        is not None
    )
    for run, message in [
        ({"script": []}, "script must be an object"),
        ({"script": {"source": {}}}, "external script"),
        ({"script": {"language": "ruby", "code": "x"}}, "runtime"),
        ({"script": {"language": "python"}}, "inline code"),
        ({"shell": []}, "shell must be an object"),
        ({"shell": {"command": ""}}, "requires command"),
        ({"shell": {"command": "${ .command }"}}, "dynamic shell"),
    ]:
        with pytest.raises((UnsupportedWorkflowFeature, WorkflowSemanticError), match=message):
            _validate_executable_run(run, sandbox=internal, reference="/run")

    docker = SandboxConfig(enabled=True, backend="docker", docker={"allowed_images": [_IMAGE]})
    assert (
        _validate_executable_run(
            {"container": {"image": _IMAGE, "command": "python"}},
            sandbox=docker,
            reference="/run",
        )
        is not None
    )
    for container, message in [
        ({}, "requires image"),
        ({"image": "${ .image }"}, "dynamic container image"),
        ({"image": _IMAGE, "ports": {"80": "80"}}, "port mappings"),
        ({"image": _IMAGE, "volumes": {"/host": "/container"}}, "host volume"),
        ({"image": _IMAGE, "name": "user-name"}, "controller-owned"),
        ({"image": _IMAGE, "command": "${ .command }"}, "dynamic container command"),
    ]:
        with pytest.raises((UnsupportedWorkflowFeature, WorkflowSemanticError), match=message):
            _validate_executable_run({"container": container}, sandbox=docker, reference="/run")

    with pytest.raises(UnsupportedWorkflowFeature, match="container sandbox backend"):
        _validate_executable_run(
            {"container": {"image": _IMAGE}}, sandbox=internal, reference="/run"
        )


def test_sandbox_validation_rewrites_nested_runs_and_environment_policy():
    config = SandboxConfig(
        enabled=True,
        backend="internal",
        secret_environment=["TOKEN"],
    )
    workflow = {
        "do": [
            {"execute": {"run": {"script": {"language": "python", "code": "print(1)"}}}},
            {
                "nested": {
                    "do": [
                        {"execute": {"run": {"script": {"language": "python", "code": "print(2)"}}}}
                    ]
                }
            },
        ]
    }
    _rewrite_executable_runs(workflow, sandbox=config, reference="")
    assert workflow["do"][0]["execute"]["run"]["workflow"]["name"] == "sandbox-placeholder"
    assert (
        workflow["do"][1]["nested"]["do"][0]["execute"]["run"]["workflow"]["name"]
        == "sandbox-placeholder"
    )

    with pytest.raises(WorkflowSemanticError, match="environment must be an object"):
        _validate_environment([], config, "/run")
    with pytest.raises(UnsupportedWorkflowFeature, match="isolation variables"):
        _validate_environment({"PATH": "unsafe"}, config, "/run")
    with pytest.raises(UnsupportedWorkflowFeature, match="not deployment-approved"):
        _validate_environment({"TOKEN": {"fromEnv": "MISSING"}}, config, "/run")
    _validate_environment({"TOKEN": {"fromEnv": "TOKEN"}}, config, "/run")
