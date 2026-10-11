"""Static workflow diagnostics and graph/plan projections."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from openworkflow_adk.models import OpenWorkflowDocument, TaskItem
from openworkflow_adk.translator import build_workflow, task_kind


@dataclass(frozen=True)
class Diagnostic:
    """A source-oriented workflow diagnostic."""

    code: str
    message: str
    path: str
    severity: str = "error"

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def _lint_task(item: TaskItem, index: int, known: set[str], path: str) -> list[Diagnostic]:
    """Lint a single task and recursively lint its nested task bodies."""
    diagnostics: list[Diagnostic] = []
    agent_config = item.task.effective_agent()
    if agent_config is not None and not agent_config.instruction:
        diagnostics.append(
            Diagnostic(
                "agent-instruction",
                f"agent task {item.name!r} has no instruction",
                f"{path}.metadata.adk.agent.instruction",
                severity="warning",
            )
        )
    if task_kind(item.task) == "switch":
        for case_index, case in enumerate(item.task.switch or []):
            configuration = next(iter(case.values())) if case else {}
            target = configuration.get("then") if isinstance(configuration, dict) else None
            if target and target not in {"continue", "end", "exit"} and target not in known:
                diagnostics.append(
                    Diagnostic(
                        "unknown-route",
                        f"switch route references unknown task {target!r}",
                        f"{path}.switch[{case_index}].then",
                    )
                )
    if isinstance(item.task.fork, dict):
        branch_names: set[str] = set()
        for branch_index, branch in enumerate(item.task.fork.get("branches", [])):
            branch_item = TaskItem.model_validate(branch)
            if branch_item.name in branch_names:
                diagnostics.append(
                    Diagnostic(
                        "duplicate-branch",
                        f"fork branch {branch_item.name!r} is duplicated",
                        f"{path}.fork.branches[{branch_index}]",
                    )
                )
            branch_names.add(branch_item.name)
            diagnostics.extend(
                _lint_container([branch_item], known, f"{path}.fork.branches[{branch_index}]")
            )
    diagnostics.extend(_lint_container(item.task.do or [], known, f"{path}.do"))
    diagnostics.extend(_lint_container(item.task.try_ or [], known, f"{path}.try"))
    catch = getattr(item.task, "catch", None)
    if isinstance(catch, dict):
        diagnostics.extend(_lint_container(catch.get("do", []), known, f"{path}.catch.do"))
    return diagnostics


def _lint_container(items: list[Any], known: set[str], path: str) -> list[Diagnostic]:
    """Lint a container of tasks for duplicate names and recurse into each task."""
    diagnostics: list[Diagnostic] = []
    names = [
        item.name if isinstance(item, TaskItem) else TaskItem.model_validate(item).name
        for item in items
    ]
    duplicate_names = {name for name in names if names.count(name) > 1}
    reported: set[str] = set()
    for index, name in enumerate(names):
        if name in duplicate_names and name not in reported:
            diagnostics.append(
                Diagnostic(
                    "duplicate-task", f"task name {name!r} is duplicated", f"{path}[{index}]"
                )
            )
            reported.add(name)
    for index, item in enumerate(items):
        if not isinstance(item, TaskItem):
            item = TaskItem.model_validate(item)
        diagnostics.extend(_lint_task(item, index, known, f"{path}[{index}]"))
    return diagnostics


def lint_workflow(document: OpenWorkflowDocument) -> list[Diagnostic]:
    """Find structural errors before translation or execution."""
    diagnostics: list[Diagnostic] = []
    items = document.do
    names = [item.name for item in items]
    known = set(names)
    duplicate_names = {name for name in names if names.count(name) > 1}
    reported: set[str] = set()
    for index, name in enumerate(names):
        if name in duplicate_names and name not in reported:
            diagnostics.append(
                Diagnostic("duplicate-task", f"task name {name!r} is duplicated", f"do[{index}]")
            )
            reported.add(name)
    for index, item in enumerate(items):
        directive = item.task.then
        if directive and directive not in {"continue", "end", "exit"} and directive not in known:
            diagnostics.append(
                Diagnostic(
                    "unknown-target",
                    f"task {item.name!r} references unknown task {directive!r}",
                    f"do[{index}].then",
                )
            )
        diagnostics.extend(_lint_task(item, index, known, f"do[{index}]"))
    reachable: set[int] = set()
    by_name = {item.name: index for index, item in enumerate(items)}
    pending = [0] if items else []
    while pending:
        index = pending.pop()
        if index in reachable or index not in range(len(items)):
            continue
        reachable.add(index)
        item = items[index]
        directive = item.task.then
        if directive in {"end", "exit"}:
            continue
        if directive in by_name:
            pending.append(by_name[directive])
        elif task_kind(item.task) == "switch":
            for case in item.task.switch or []:
                configuration = next(iter(case.values())) if case else {}
                target = configuration.get("then") if isinstance(configuration, dict) else None
                if target in by_name:
                    pending.append(by_name[target])
        elif index + 1 < len(items):
            pending.append(index + 1)
    for index, item in enumerate(items):
        if index not in reachable:
            diagnostics.append(
                Diagnostic(
                    "unreachable-task",
                    f"task {item.name!r} cannot be reached from workflow start",
                    f"do[{index}]",
                    severity="warning",
                )
            )
    return diagnostics


def _node_name(node: Any) -> str:
    if isinstance(node, str):
        return node
    return getattr(node, "name", str(node))


def workflow_plan(document: OpenWorkflowDocument) -> dict[str, Any]:
    """Return a JSON-serializable dry-run projection of the ADK graph."""
    workflow = build_workflow(document)
    nodes: set[str] = set()
    edges: list[dict[str, Any]] = []
    for edge in workflow.edges:
        if len(edge) < 2:
            continue
        source = _node_name(edge[0])
        target = edge[1]
        nodes.add(source)
        if isinstance(target, dict):
            for route, route_target in target.items():
                target_name = _node_name(route_target)
                nodes.add(target_name)
                edges.append({"from": source, "to": target_name, "route": str(route)})
        elif isinstance(target, tuple):
            for branch in target:
                target_name = _node_name(branch)
                nodes.add(target_name)
                edges.append({"from": source, "to": target_name})
        else:
            target_name = _node_name(target)
            nodes.add(target_name)
            edges.append({"from": source, "to": target_name})
    return {
        "workflow": document.document.name,
        "namespace": document.document.namespace,
        "version": document.document.version,
        "nodes": sorted(nodes),
        "edges": edges,
    }


def workflow_mermaid(document: OpenWorkflowDocument) -> str:
    """Render the compiled plan as a Mermaid flowchart."""
    plan = workflow_plan(document)
    lines = ["flowchart TD"]
    for edge in plan["edges"]:
        suffix = f"|{edge['route']}|" if edge.get("route") else ""
        lines.append(f"    {edge['from']} -->{suffix} {edge['to']}")
    return "\n".join(lines) + "\n"
