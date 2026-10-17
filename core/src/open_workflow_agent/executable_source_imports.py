"""Keep catalog captures outside the initial workflow-owned script-source profile."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .errors import UnsupportedWorkflowFeature
from .external_catalog import ResolvedCatalogFunction

_MAX_TASKS = 10_000
_MAX_DEPTH = 32


def reject_imported_script_sources(imports: tuple[ResolvedCatalogFunction, ...]) -> None:
    """Reject source-bearing original OR derived imported task definitions.

    Catalog captures have their own reference/authentication/integrity contract.
    The finite source profile binds only original workflow-owned task pointers;
    a derived inline body must not disguise an uncaptured imported source.
    Business payload fields named ``source`` are not task-definition slots.
    This guard performs no resolution, capture, network or execution work.
    """
    from .workflow import _task_scopes, _walk_tasks

    def unavailable() -> UnsupportedWorkflowFeature:
        return UnsupportedWorkflowFeature(
            "imported catalog script sources are outside the qualified source profile",
            details={"reason": "imported_executable_source_unsupported"},
        )

    remaining = _MAX_TASKS
    for imported in imports:
        if type(imported) is not ResolvedCatalogFunction:
            raise unavailable()
        try:
            definitions = (imported.definition, imported.execution_definition)
        except (ValueError, TypeError, RecursionError):
            raise unavailable() from None
        for definition in definitions:
            stack: list[tuple[Any, int]] = [(definition, 0)]
            while stack:
                task, depth = stack.pop()
                remaining -= 1
                if remaining < 0 or depth > _MAX_DEPTH or not isinstance(task, Mapping):
                    raise unavailable()
                run = task.get("run")
                script = run.get("script") if isinstance(run, Mapping) else None
                if isinstance(script, Mapping) and "source" in script:
                    raise unavailable()
                for suffix, children in _task_scopes(task):
                    stack.extend(
                        (child, depth + 1)
                        for _, child in _walk_tasks(
                            children, prefix="/do", indexed=not suffix.startswith("fork/branches/")
                        )
                    )
