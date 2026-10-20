"""Deployment-controlled capability gate and compilation for executable runs."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..catalog import FunctionCatalog
from ..config import SandboxConfig
from ..definitions import resolve_reusable_components
from ..errors import SandboxPolicyError, UnsupportedWorkflowFeature, WorkflowSemanticError
from ..executable_source_plan import capture_script_resources, materialize_script_resources
from ..executable_sources import ExecutableSource, ExecutableSourceRegistry
from ..resources import materialize_schema_resources, schema_registry
from ..workflow import (
    WorkflowPlan,
    _task_scopes,
    _walk_tasks,
    generate_default_workflow,
    load_workflow,
    normalize_workflow,
    resolve_workflow_dependencies,
    validate_capabilities,
    validate_expression_profile,
    validate_schema,
)
from . import background_qualification
from .contract import RESERVED_ENVIRONMENT
from .stdio_policy import validate_stdio_transport


def validate_sandbox_capabilities(
    workflow: Mapping[str, Any],
    *,
    sandbox: SandboxConfig,
    trusted_catalogs: Mapping[str, Any] | None = None,
    _resource_preflight: bool = False,
    _schema_registry: Any = None,
    grpc_enabled: bool = False,
    enabled_function_calls: frozenset[str] = frozenset(),
    executable_sources: ExecutableSourceRegistry | None = None,
) -> None:
    effective = resolve_reusable_components(workflow)
    registry = executable_sources or ExecutableSourceRegistry(
        tuple(
            ExecutableSource.from_value(item.model_dump(mode="json"))
            for item in sandbox.script_sources
        )
    )
    bindings = capture_script_resources(workflow, registry)
    if bindings:
        from ..executable_source_admission import (
            ExecutableSourceProfileLimitError,
            source_business_receipt_limit,
        )

        try:
            source_business_receipt_limit(sandbox.max_output_bytes)
        except ExecutableSourceProfileLimitError:
            raise UnsupportedWorkflowFeature(
                "executable source output profile is unsupported",
                details={"reason": "executable_source_output_profile_unsupported"},
            ) from None
        effective = materialize_script_resources(effective, bindings)
    rewritten = copy.deepcopy(effective)
    _rewrite_executable_runs(rewritten, sandbox=sandbox, reference="")
    _validate_stdio_calls(effective, sandbox=sandbox)
    validate_capabilities(
        rewritten,
        trusted_catalogs=trusted_catalogs,
        _resource_preflight=_resource_preflight,
        _schema_registry=_schema_registry,
        _mcp_stdio=True,
        grpc_enabled=grpc_enabled,
        enabled_function_calls=enabled_function_calls,
    )
    validate_expression_profile(effective)


def _validate_stdio_calls(workflow: Mapping[str, Any], *, sandbox: SandboxConfig) -> None:
    """Check every expanded stdio declaration without changing public source."""

    def tasks(items: Any, path: str, indexed: bool = True) -> None:
        for reference, definition in _walk_tasks(items, prefix=path, indexed=indexed):
            parameters = definition.get("with")
            transport = parameters.get("transport") if isinstance(parameters, Mapping) else None
            if (
                definition.get("call") == "mcp"
                and isinstance(transport, Mapping)
                and "stdio" in transport
            ):
                try:
                    validate_stdio_transport(transport, sandbox)
                except SandboxPolicyError as error:
                    raise UnsupportedWorkflowFeature(
                        error.message, details={"reference": reference}
                    ) from None
            for suffix, children in _task_scopes(definition):
                tasks(children, f"{reference}/{suffix}", not suffix.startswith("fork/branches/"))

    tasks(workflow.get("do", []), "/do")
    use = workflow.get("use")
    functions = use.get("functions") if isinstance(use, Mapping) else None
    if isinstance(functions, Mapping):
        for name, definition in functions.items():
            tasks([{name: definition}], "/use/functions", False)


def compile_sandbox_workflow(
    source: str | Path | Mapping[str, Any] | None = None,
    *,
    sandbox: SandboxConfig,
    trusted_catalogs: Mapping[str, Any] | None = None,
    grpc_enabled: bool = False,
    enabled_function_calls: frozenset[str] = frozenset(),
    executable_sources: ExecutableSourceRegistry | None = None,
) -> WorkflowPlan:
    workflow = generate_default_workflow() if source is None else load_workflow(source)
    validate_schema(workflow)
    validate_sandbox_capabilities(
        workflow,
        sandbox=sandbox,
        trusted_catalogs=trusted_catalogs,
        grpc_enabled=grpc_enabled,
        enabled_function_calls=enabled_function_calls,
        executable_sources=executable_sources,
    )
    registry = executable_sources or ExecutableSourceRegistry(
        tuple(
            ExecutableSource.from_value(item.model_dump(mode="json"))
            for item in sandbox.script_sources
        )
    )
    return normalize_workflow(
        workflow, script_resources=capture_script_resources(workflow, registry)
    )


async def resolve_and_compile_sandbox_workflow(
    source: str | Path | Mapping[str, Any] | None = None,
    *,
    sandbox: SandboxConfig,
    trusted_catalogs: Mapping[str, Any] | None = None,
    resolver: Any,
    catalog: FunctionCatalog,
    grpc_enabled: bool = False,
    enabled_function_calls: frozenset[str] = frozenset(),
    executable_sources: ExecutableSourceRegistry | None = None,
) -> WorkflowPlan:
    workflow = generate_default_workflow() if source is None else load_workflow(source)
    validate_schema(workflow)
    enabled = bool(getattr(getattr(resolver, "schemas", None), "enabled", False))
    validate_sandbox_capabilities(
        workflow,
        sandbox=sandbox,
        trusted_catalogs=trusted_catalogs,
        _resource_preflight=enabled,
        grpc_enabled=grpc_enabled,
        enabled_function_calls=enabled_function_calls,
        executable_sources=executable_sources,
    )
    imports, resources = await resolve_workflow_dependencies(workflow, resolver, catalog)
    effective = materialize_schema_resources(workflow) if resources else workflow
    validate_sandbox_capabilities(
        effective,
        sandbox=sandbox,
        trusted_catalogs=trusted_catalogs,
        _schema_registry=schema_registry(resources),
        grpc_enabled=grpc_enabled,
        enabled_function_calls=enabled_function_calls,
        executable_sources=executable_sources,
    )
    registry = executable_sources or ExecutableSourceRegistry(
        tuple(
            ExecutableSource.from_value(item.model_dump(mode="json"))
            for item in sandbox.script_sources
        )
    )
    return normalize_workflow(
        workflow,
        imports=imports,
        resources=resources,
        script_resources=capture_script_resources(workflow, registry),
    )


def _rewrite_executable_runs(value: Any, *, sandbox: SandboxConfig, reference: str) -> None:
    if not isinstance(value, dict):
        return

    def tasks(items: Any, path: str, indexed: bool = True) -> None:
        for task_reference, definition in _walk_tasks(items, prefix=path, indexed=indexed):
            run = definition.get("run")
            if isinstance(run, Mapping):
                replacement = _validate_executable_run(
                    run, sandbox=sandbox, reference=task_reference
                )
                if replacement is not None:
                    definition["run"] = replacement
            for suffix, children in _task_scopes(definition):
                tasks(
                    children, f"{task_reference}/{suffix}", not suffix.startswith("fork/branches/")
                )

    tasks(value.get("do", []), f"{reference}/do")
    for name, definition in value.get("use", {}).get("functions", {}).items():
        tasks([{name: definition}], f"{reference}/use/functions", False)


def _validate_executable_run(
    run: Mapping[str, Any], *, sandbox: SandboxConfig, reference: str
) -> dict[str, Any] | None:
    if not {"script", "shell", "container"}.intersection(run):
        # Workflow child lifetime and return modes belong to the common
        # workflow contract; executable-process restrictions do not apply.
        return None
    if run.get("await", True) is not True:
        if not background_qualification.BACKGROUND_EXECUTION_PROFILE_QUALIFIED:
            raise UnsupportedWorkflowFeature(
                "background execution profile has not passed qualification",
                details={"reference": reference},
            )
        if not sandbox.background.enabled or not sandbox.enabled or sandbox.backend != "internal":
            raise UnsupportedWorkflowFeature(
                "background execution requires the deployment-enabled internal SQLite profile",
                details={"reference": reference},
            )
        if "container" in run:
            raise UnsupportedWorkflowFeature(
                "background container execution is not qualified",
                details={"reference": reference},
            )
        for kind in ("script", "shell"):
            configuration = run.get(kind)
            environment = (
                configuration.get("environment", {}) if isinstance(configuration, Mapping) else {}
            )
            if not isinstance(environment, Mapping) or any(
                not isinstance(value, Mapping) or set(value) != {"fromEnv"}
                for value in environment.values()
            ):
                raise UnsupportedWorkflowFeature(
                    "background environments require approved secret references",
                    details={"reference": reference},
                )
    mode = run.get("return", "stdout")
    if not isinstance(mode, str) or mode not in {"stdout", "stderr", "code", "all", "none"}:
        raise WorkflowSemanticError(f"invalid executable run return mode at {reference}")
    if "container" in run:
        if not sandbox.enabled or sandbox.backend not in {"docker", "kubernetes"}:
            raise UnsupportedWorkflowFeature(
                "run.container requires a deployment-enabled container sandbox backend",
                details={"reference": reference},
            )
        container = run["container"]
        if not isinstance(container, Mapping):
            raise WorkflowSemanticError(f"run.container must be an object at {reference}")
        if "lifetime" in container:
            raise UnsupportedWorkflowFeature(
                "run.container lifetime configuration is not enabled",
                details={"reference": reference},
            )
        if sandbox.backend == "kubernetes" and "stdin" in container:
            raise UnsupportedWorkflowFeature(
                "selected Kubernetes sandbox does not support container stdin",
                details={"reference": reference},
            )
        pull_policy = "ifNotPresent" if sandbox.backend == "kubernetes" else "never"
        if "pullPolicy" in container and container["pullPolicy"] != pull_policy:
            raise UnsupportedWorkflowFeature(
                "run.container pull policy is not supported by the selected controller",
                details={"reference": reference},
            )
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
