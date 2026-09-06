from __future__ import annotations

import pytest
from open_workflow_agent.config import SandboxConfig
from open_workflow_agent.errors import UnsupportedWorkflowFeature, WorkflowSemanticError
from open_workflow_agent.sandbox.validation import (
    _rewrite_executable_runs,
    _validate_environment,
    _validate_executable_run,
)

_IMAGE = "registry.example/worker@sha256:" + ("a" * 64)


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
            {"nested": [{"run": {"script": {"language": "python", "code": "print(2)"}}}]},
        ]
    }
    _rewrite_executable_runs(workflow, sandbox=config, reference="")
    assert workflow["do"][0]["execute"]["run"]["workflow"]["name"] == "sandbox-placeholder"

    with pytest.raises(WorkflowSemanticError, match="environment must be an object"):
        _validate_environment([], config, "/run")
    with pytest.raises(UnsupportedWorkflowFeature, match="isolation variables"):
        _validate_environment({"PATH": "unsafe"}, config, "/run")
    with pytest.raises(UnsupportedWorkflowFeature, match="not deployment-approved"):
        _validate_environment({"TOKEN": {"fromEnv": "MISSING"}}, config, "/run")
    _validate_environment({"TOKEN": {"fromEnv": "TOKEN"}}, config, "/run")
