"""Public custom-function references; deployment config owns storage and limits."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from .config import NativeMemoryBindingConfig
from .errors import UnsupportedWorkflowFeature, WorkflowSemanticError

if TYPE_CHECKING:
    from .workflow import WorkflowPlan

CAPABILITY = "langgraph.memory@1.0.0"
OPERATIONS = ("get", "put", "search", "delete", "sweep")
_REFERENCE = re.compile(r"memory\.(get|put|search|delete|sweep):1\.0\.0@([a-z][a-z0-9_-]{0,63})\Z")
FIELDS = {
    "get": {"key"},
    "put": {"key", "value"},
    "delete": {"key"},
    "search": {"filter", "limit"},
    "sweep": set(),
}
REQUIRED = {
    "get": {"key"},
    "put": {"key", "value"},
    "delete": {"key"},
    "search": set(),
    "sweep": set(),
}


def references(bindings: Sequence[NativeMemoryBindingConfig]) -> frozenset[str]:
    return frozenset(
        f"memory.{operation}:1.0.0@{binding.id}" for binding in bindings for operation in OPERATIONS
    )


def validate_arguments(operation: str, value: Any) -> None:
    if (
        not isinstance(value, Mapping)
        or set(value) - FIELDS[operation]
        or not REQUIRED[operation] <= set(value)
    ):
        raise ValueError("native memory function arguments do not match the declared profile")


def verify_plan(plan: WorkflowPlan, bindings: Sequence[NativeMemoryBindingConfig]) -> None:
    declared = any(
        item.required and item.capability_id == CAPABILITY for item in plan.native_extensions
    )
    allowed = references(bindings)
    if not bindings and not any(item.name == "langgraph.memory" for item in plan.native_extensions):
        return
    functions = plan.source.get("use", {}).get("functions", {})
    if any(isinstance(name, str) and _REFERENCE.fullmatch(name) for name in functions):
        raise WorkflowSemanticError("native memory functions are deployment-bound references")
    for scope in plan.scopes:
        for task in scope.tasks:
            reference = task.definition.get("call")
            if not isinstance(reference, str) or not any(
                reference.startswith(f"memory.{op}:") for op in OPERATIONS
            ):
                continue
            matched = _REFERENCE.fullmatch(reference)
            if matched is None or reference not in allowed or not declared:
                raise UnsupportedWorkflowFeature(
                    "native memory calls require an installed binding and exact required extension",
                    details={"reference": task.reference},
                )
            arguments = task.definition.get("with", {})
            if isinstance(arguments, str) and arguments.strip().startswith("${"):
                continue
            try:
                validate_arguments(matched[1], arguments)
            except ValueError:
                raise WorkflowSemanticError(
                    "native memory function arguments are unsupported",
                    details={"reference": task.reference},
                ) from None
