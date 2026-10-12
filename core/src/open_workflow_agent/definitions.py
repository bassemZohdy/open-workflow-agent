"""Network-free resolution of workflow-owned reusable components."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from .errors import WorkflowSemanticError


def pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def definition_at_pointer(source: Mapping[str, Any], reference: str) -> Any:
    value: Any = source
    for token in reference.lstrip("/").split("/"):
        name = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, Mapping):
            value = value.get(name)
        elif isinstance(value, list) and name.isdecimal():
            value = value[int(name)] if int(name) < len(value) else None
        else:
            return None
    return value


def resolve_reusable_components(workflow: Mapping[str, Any]) -> dict[str, Any]:
    """Copy definitions; never treat application payloads as reference slots."""
    result = copy.deepcopy(dict(workflow))
    use = result.get("use", {})

    def resolve(value: Any, section: str, reference: str) -> Any:
        if not isinstance(value, str):
            return value
        components = use.get(section, {})
        if value not in components:
            raise WorkflowSemanticError(
                f"unknown reusable {section} reference", details={"reference": reference}
            )
        return copy.deepcopy(components[value])

    def tasks(items: Any, prefix: str, *, indexed: bool = True) -> None:
        for index, item in enumerate(items):
            for name, definition in item.items():
                start = f"{prefix}/{index}" if indexed else prefix
                task(definition, f"{start}/{pointer_token(name)}")

    def task(value: dict[str, Any], reference: str) -> None:
        if "timeout" in value:
            value["timeout"] = resolve(value["timeout"], "timeouts", f"{reference}/timeout")
        raised = value.get("raise")
        if isinstance(raised, dict) and "error" in raised:
            raised["error"] = resolve(raised["error"], "errors", f"{reference}/raise/error")
        catch = value.get("catch")
        if isinstance(catch, dict):
            if "retry" in catch:
                catch["retry"] = resolve(catch["retry"], "retries", f"{reference}/catch/retry")
            if "do" in catch:
                tasks(catch["do"], f"{reference}/catch/do")
        for key in ("do", "try"):
            if key in value:
                tasks(value[key], f"{reference}/{key}")
        iterator = value.get("foreach") if "listen" in value else None
        if isinstance(iterator, Mapping) and "do" in iterator:
            tasks(iterator["do"], f"{reference}/foreach/do")
        fork = value.get("fork")
        if isinstance(fork, Mapping):
            for index, branch in enumerate(fork["branches"]):
                tasks([branch], f"{reference}/fork/branches/{index}", indexed=False)

    if "timeout" in result:
        result["timeout"] = resolve(result["timeout"], "timeouts", "/timeout")
    tasks(result["do"], "/do")
    for name, value in use.get("functions", {}).items():
        task(value, f"/use/functions/{pointer_token(name)}")
    functions = use.get("functions", {})
    dependencies: dict[str, set[str]] = {}

    def calls(value: Any) -> set[str]:
        found: set[str] = set()
        if isinstance(value, Mapping):
            call = value.get("call")
            if isinstance(call, str) and call in functions:
                found.add(call)
            for key in ("do", "try"):
                for item in value.get(key, []):
                    for definition in item.values():
                        found.update(calls(definition))
            iterator = value.get("foreach") if "listen" in value else None
            if isinstance(iterator, Mapping):
                for item in iterator.get("do", []):
                    for definition in item.values():
                        found.update(calls(definition))
            catch = value.get("catch", {})
            for item in catch.get("do", []):
                for definition in item.values():
                    found.update(calls(definition))
            for item in value.get("fork", {}).get("branches", []):
                for definition in item.values():
                    found.update(calls(definition))
        return found

    for name, value in functions.items():
        dependencies[name] = calls(value)

    heights: dict[str, int] = {}

    def visit(name: str, stack: tuple[str, ...]) -> int:
        if name in stack:
            raise WorkflowSemanticError(
                "recursive reusable function references are not enabled",
                details={"reference": f"/use/functions/{pointer_token(name)}/call"},
            )
        if len(stack) >= 32:
            raise WorkflowSemanticError(
                "reusable function depth exceeds 32",
                details={"reference": f"/use/functions/{pointer_token(name)}"},
            )
        if name in heights:
            return heights[name]
        height = 1 + max(
            (visit(dependency, (*stack, name)) for dependency in dependencies[name]), default=0
        )
        if height > 32:
            raise WorkflowSemanticError(
                "reusable function depth exceeds 32",
                details={"reference": f"/use/functions/{pointer_token(name)}"},
            )
        heights[name] = height
        return height

    for name in functions:
        visit(name, ())
    return result
