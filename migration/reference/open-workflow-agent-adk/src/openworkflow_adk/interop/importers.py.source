"""Best-effort imports from common workflow representations."""

from __future__ import annotations

from typing import Any

from openworkflow_adk.loader import load
from openworkflow_adk.models import OpenWorkflowDocument

__all__ = ["import_airflow", "import_argo"]


def _envelope(namespace: str, name: str, version: str) -> dict[str, Any]:
    return {
        "document": {"dsl": "1.0.3", "namespace": namespace, "name": name, "version": version},
        "do": [],
    }


def import_airflow(
    source: dict[str, Any],
    *,
    namespace: str = "imported",
    name: str = "airflow",
    version: str = "1.0.0",
) -> OpenWorkflowDocument:
    """Import a linear Airflow-like task mapping with Bash operators."""
    raw = _envelope(namespace, name, version)
    tasks = source.get("tasks") or source.get("dag", {}).get("tasks")
    if not isinstance(tasks, list):
        raise ValueError("Airflow import requires a tasks array")
    for task in tasks:
        if not isinstance(task, dict) or not isinstance(task.get("task_id"), str):
            raise ValueError("Airflow tasks require task_id")
        command = task.get("bash_command") or task.get("command")
        if not isinstance(command, str):
            raise ValueError("only BashOperator-like tasks are supported")
        raw["do"].append(
            {task["task_id"]: {"run": {"shell": {"command": "sh", "arguments": ["-c", command]}}}}
        )
    return load(raw)


def import_argo(
    source: dict[str, Any],
    *,
    namespace: str = "imported",
    name: str = "argo",
    version: str = "1.0.0",
) -> OpenWorkflowDocument:
    """Import a linear Argo Workflow template list with container commands."""
    raw = _envelope(namespace, name, version)
    templates = source.get("spec", {}).get("templates", [])
    if not isinstance(templates, list):
        raise ValueError("Argo import requires spec.templates")
    for template in templates:
        if not isinstance(template, dict) or not isinstance(template.get("name"), str):
            raise ValueError("Argo templates require name")
        container = template.get("container") or {}
        command = container.get("command") or []
        args = container.get("args") or []
        if not command:
            raise ValueError("only container templates are supported")
        raw["do"].append(
            {
                template["name"]: {
                    "run": {"shell": {"command": command[0], "arguments": [*command[1:], *args]}}
                }
            }
        )
    return load(raw)
