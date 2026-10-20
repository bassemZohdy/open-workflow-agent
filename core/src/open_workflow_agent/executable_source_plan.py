"""Derive private inline script bodies from exact deployment-captured sources."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from .definitions import resolve_reusable_components
from .errors import OwaError, UnsupportedWorkflowFeature, WorkflowSemanticError
from .executable_sources import ExecutableScriptBinding, ExecutableSourceRegistry


def _script_tasks(workflow: Mapping[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    from .workflow import _task_scopes, _walk_tasks

    result = []

    def walk(items: Any, prefix: str, indexed: bool = True) -> None:
        for reference, task in _walk_tasks(items, prefix=prefix, indexed=indexed):
            run = task.get("run")
            script = run.get("script") if isinstance(run, Mapping) else None
            if isinstance(script, dict) and "source" in script:
                result.append((reference, script))
            for suffix, children in _task_scopes(task):
                walk(children, f"{reference}/{suffix}", not suffix.startswith("fork/branches/"))

    walk(workflow.get("do", []), "/do")
    functions = workflow.get("use", {}).get("functions", {})
    for name, task in functions.items():
        walk([{name: task}], "/use/functions", False)
    return result


def capture_script_resources(
    workflow: Mapping[str, Any], registry: ExecutableSourceRegistry
) -> tuple[ExecutableScriptBinding, ...]:
    effective = resolve_reusable_components(workflow)
    bindings = []
    for reference, script in _script_tasks(effective):
        if "code" in script:
            raise WorkflowSemanticError("script code and source cannot be combined")
        language = script.get("language")
        if not isinstance(language, str):
            raise WorkflowSemanticError("script source language must be declared")
        try:
            source = registry.resolve(script["source"], language=language)
        except OwaError:
            raise UnsupportedWorkflowFeature(
                "script source is not an exact approved executable binding",
                details={"reference": reference},
            ) from None
        bindings.append(ExecutableScriptBinding(reference, source))
    return tuple(bindings)


def materialize_script_resources(
    workflow: Mapping[str, Any], bindings: tuple[ExecutableScriptBinding, ...]
) -> dict[str, Any]:
    from .executable_sources import ExecutableSourceRegistry

    effective = copy.deepcopy(dict(workflow))
    expected = {binding.reference: binding.source for binding in bindings}
    if len(expected) != len(bindings):
        raise WorkflowSemanticError("duplicate executable source task binding")
    used = set()
    for reference, script in _script_tasks(effective):
        source = expected.get(reference)
        if source is None or "code" in script:
            raise WorkflowSemanticError("original executable source binding is missing")
        registry = ExecutableSourceRegistry((source,))
        language = script.get("language")
        if not isinstance(language, str):
            raise WorkflowSemanticError("script source language must be declared")
        if registry.resolve(script["source"], language=language) != source:
            raise WorkflowSemanticError("original executable source binding differs")
        script.pop("source")
        script["code"] = source.verified_bytes().decode("utf-8")
        used.add(reference)
    if used != set(expected):
        raise WorkflowSemanticError("orphan executable source task binding")
    return effective
