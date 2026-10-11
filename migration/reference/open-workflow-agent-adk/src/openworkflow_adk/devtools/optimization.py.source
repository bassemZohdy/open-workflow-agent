"""Conservative workflow optimization passes."""

from __future__ import annotations

from dataclasses import dataclass

from openworkflow_adk.devtools.diagnostics import lint_workflow
from openworkflow_adk.models import OpenWorkflowDocument
from openworkflow_adk.translator import task_kind


@dataclass(frozen=True)
class SimplificationResult:
    """Simplified document plus human-readable transformations."""

    document: OpenWorkflowDocument
    changes: tuple[str, ...]


def simplify_workflow(document: OpenWorkflowDocument) -> SimplificationResult:
    """Apply semantics-preserving, statically provable simplifications."""
    diagnostics = lint_workflow(document)
    unreachable = {
        item.path.split("[")[-1].split("]", 1)[0]
        for item in diagnostics
        if item.code == "unreachable-task"
    }
    names = {item.name for item in document.do}
    referenced: set[str] = set()
    for item in document.do:
        if item.task.then in names:
            referenced.add(item.task.then)
        if task_kind(item.task) == "switch":
            for case in item.task.switch or []:
                config = next(iter(case.values())) if case else {}
                target = config.get("then") if isinstance(config, dict) else None
                if target in names:
                    referenced.add(target)
    retained = []
    changes: list[str] = []
    for index, item in enumerate(document.do):
        if str(index) in unreachable and item.name not in referenced:
            changes.append(f"removed unreachable task {item.name!r}")
            continue
        if (
            task_kind(item.task) == "wait"
            and item.task.wait in (0, {"seconds": 0})
            and item.name not in referenced
        ):
            changes.append(f"removed zero-duration wait {item.name!r}")
            continue
        retained.append(item)
    if not changes:
        return SimplificationResult(document=document, changes=())
    return SimplificationResult(
        document=document.model_copy(update={"do": retained}),
        changes=tuple(changes),
    )
