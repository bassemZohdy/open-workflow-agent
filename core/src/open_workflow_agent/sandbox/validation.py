"""Deployment-controlled capability gate and compilation for executable runs."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..catalog import FunctionCatalog
from ..config import SandboxConfig
from ..errors import UnsupportedWorkflowFeature, WorkflowSemanticError
from ..workflow import (
    WorkflowPlan,
    generate_default_workflow,
    load_workflow,
    normalize_workflow,
    validate_capabilities,
    validate_schema,
)
from .contract import RESERVED_ENVIRONMENT


def validate_sandbox_capabilities(
    workflow: Mapping[str, Any],
    *,
    sandbox: SandboxConfig,
    trusted_catalogs: Mapping[str, Any] | None = None,
) -> None:
    rewritten = copy.deepcopy(dict(workflow))
    _rewrite_executable_runs(rewritten, sandbox=sandbox, reference="")
    validate_capabilities(rewritten, trusted_catalogs=trusted_catalogs)


def compile_sandbox_workflow(
    source: str | Path | Mapping[str, Any] | None = None,
    *,
    sandbox: SandboxConfig,
    trusted_catalogs: Mapping[str, Any] | None = None,
) -> WorkflowPlan:
    workflow = generate_default_workflow() if source is None else load_workflow(source)
    validate_schema(workflow)
    validate_sandbox_capabilities(workflow, sandbox=sandbox, trusted_catalogs=trusted_catalogs)
    return normalize_workflow(workflow)


async def resolve_and_compile_sandbox_workflow(
    source: str | Path | Mapping[str, Any] | None = None,
    *,
    sandbox: SandboxConfig,
    trusted_catalogs: Mapping[str, Any] | None = None,
    resolver: Any,
    catalog: FunctionCatalog,
) -> WorkflowPlan:
    workflow = generate_default_workflow() if source is None else load_workflow(source)
    validate_schema(workflow)
    await resolver.resolve_workflow(workflow, catalog)
    validate_sandbox_capabilities(workflow, sandbox=sandbox, trusted_catalogs=trusted_catalogs)
    return normalize_workflow(workflow)


def _rewrite_executable_runs(value: Any, *, sandbox: SandboxConfig, reference: str) -> None:
    if isinstance(value, list):
        for index, item in enumerate(value):
            _rewrite_executable_runs(item, sandbox=sandbox, reference=f"{reference}/{index}")
        return
    if not isinstance(value, dict):
        return
    run = value.get("run")
    if isinstance(run, Mapping):
        run_reference = reference or "/run"
        replacement = _validate_executable_run(run, sandbox=sandbox, reference=run_reference)
        if replacement is not None:
            value["run"] = replacement
    for key, item in list(value.items()):
        if key == "run":
            continue
        _rewrite_executable_runs(item, sandbox=sandbox, reference=f"{reference}/{key}")


def _validate_executable_run(
    run: Mapping[str, Any], *, sandbox: SandboxConfig, reference: str
) -> dict[str, Any] | None:
    if "container" in run:
        if not sandbox.enabled or sandbox.backend not in {"docker", "kubernetes"}:
            raise UnsupportedWorkflowFeature(
                "run.container requires a deployment-enabled container sandbox backend",
                details={"reference": reference},
            )
        container = run["container"]
        if not isinstance(container, Mapping):
            raise WorkflowSemanticError(f"run.container must be an object at {reference}")
        image = container.get("image")
        if not isinstance(image, str) or not image.strip():
            raise WorkflowSemanticError(f"run.container requires image at {reference}")
        if "${" in image:
            raise UnsupportedWorkflowFeature(
                "dynamic container image selection is not enabled",
                details={"reference": reference},
            )
        allowed_images = (
            sandbox.kubernetes.allowed_images
            if sandbox.backend == "kubernetes"
            else sandbox.docker.allowed_images
        )
        if image not in allowed_images:
            raise UnsupportedWorkflowFeature(
                "container image is not deployment-approved",
                details={"reference": reference},
            )
        if container.get("ports"):
            raise UnsupportedWorkflowFeature(
                "run.container port mappings are not enabled",
                details={"reference": reference},
            )
        if container.get("volumes"):
            raise UnsupportedWorkflowFeature(
                "run.container host volume mappings are not enabled",
                details={"reference": reference},
            )
        if container.get("name"):
            raise UnsupportedWorkflowFeature(
                "run.container names are controller-owned",
                details={"reference": reference},
            )
        command = container.get("command")
        if command is not None and (not isinstance(command, str) or "${" in command):
            raise UnsupportedWorkflowFeature(
                "dynamic container commands are not enabled",
                details={"reference": reference},
            )
        _validate_environment(container.get("environment"), sandbox, reference)
        return _placeholder_workflow_run()
    if "script" in run:
        if not sandbox.enabled or sandbox.backend != "internal":
            raise UnsupportedWorkflowFeature(
                "run.script requires the deployment-enabled internal sandbox",
                details={"reference": reference},
            )
        script = run["script"]
        if not isinstance(script, Mapping):
            raise WorkflowSemanticError(f"run.script must be an object at {reference}")
        if "source" in script:
            raise UnsupportedWorkflowFeature(
                "external script resources are not enabled",
                details={"reference": reference},
            )
        language = script.get("language")
        if language not in sandbox.script_runtimes:
            raise UnsupportedWorkflowFeature(
                "script runtime is not enabled",
                details={"reference": reference, "runtime": language},
            )
        if not isinstance(script.get("code"), str):
            raise WorkflowSemanticError(f"run.script requires inline code at {reference}")
        _validate_environment(script.get("environment"), sandbox, reference)
        return _placeholder_workflow_run()
    if "shell" in run:
        if not sandbox.enabled or sandbox.backend != "internal" or not sandbox.allow_shell:
            raise UnsupportedWorkflowFeature(
                "run.shell requires deployment-enabled internal shell execution",
                details={"reference": reference},
            )
        shell = run["shell"]
        if not isinstance(shell, Mapping):
            raise WorkflowSemanticError(f"run.shell must be an object at {reference}")
        command = shell.get("command")
        if not isinstance(command, str) or not command.strip():
            raise WorkflowSemanticError(f"run.shell requires command at {reference}")
        if "${" in command:
            raise UnsupportedWorkflowFeature(
                "dynamic shell executable names are not enabled",
                details={"reference": reference},
            )
        _validate_environment(shell.get("environment"), sandbox, reference)
        return _placeholder_workflow_run()
    return None


def _validate_environment(value: Any, sandbox: SandboxConfig, reference: str) -> None:
    if value is None:
        return
    if not isinstance(value, Mapping):
        raise WorkflowSemanticError(f"sandbox environment must be an object at {reference}")
    for name, raw in value.items():
        if str(name) in RESERVED_ENVIRONMENT:
            raise UnsupportedWorkflowFeature(
                "sandbox environment cannot override runtime isolation variables",
                details={"reference": reference, "name": str(name)},
            )
        if isinstance(raw, Mapping) and set(raw) == {"fromEnv"}:
            environment_name = raw.get("fromEnv")
            if (
                not isinstance(environment_name, str)
                or environment_name not in sandbox.secret_environment
            ):
                raise UnsupportedWorkflowFeature(
                    "sandbox secret reference is not deployment-approved",
                    details={"reference": reference, "name": environment_name},
                )


def _placeholder_workflow_run() -> dict[str, Any]:
    return {
        "workflow": {
            "namespace": "open-workflow-agent",
            "name": "sandbox-placeholder",
            "version": "0.0.0",
        }
    }
