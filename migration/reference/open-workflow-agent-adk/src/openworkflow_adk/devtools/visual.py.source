"""Interchange helpers for visual workflow builders."""

from __future__ import annotations

from typing import Any

import yaml

from openworkflow_adk.loader import load


def graph_to_document(graph: dict[str, Any]) -> dict[str, Any]:
    """Convert a visual graph payload into a validated workflow mapping."""
    document = graph.get("document")
    nodes = graph.get("nodes")
    edges = graph.get("edges", [])
    if not isinstance(document, dict) or not isinstance(nodes, list):
        raise ValueError("graph requires document and nodes")
    by_id: dict[str, dict[str, Any]] = {}
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get("id"), str):
            raise ValueError("each graph node requires a string id")
        if node["id"] in by_id:
            raise ValueError(f"duplicate graph node {node['id']!r}")
        task = node.get("task")
        if not isinstance(task, dict) or len(task) != 1:
            raise ValueError(f"graph node {node['id']!r} requires one task mapping")
        by_id[node["id"]] = task
    outgoing: dict[str, str] = {}
    for edge in edges:
        if (
            not isinstance(edge, dict)
            or edge.get("from") not in by_id
            or edge.get("to") not in by_id
        ):
            raise ValueError("graph edges must reference known nodes")
        source = str(edge["from"])
        if source in outgoing:
            raise ValueError(f"node {source!r} has multiple outgoing edges")
        outgoing[source] = str(edge["to"])
    ordered: list[dict[str, Any]] = []
    current = str(graph.get("start", next(iter(by_id), "")))
    visited: set[str] = set()
    while current:
        if current in visited:
            raise ValueError("graph contains a cycle")
        if current not in by_id:
            raise ValueError(f"unknown graph node {current!r}")
        visited.add(current)
        named_task = by_id[current]
        if current in outgoing:
            task_name, task_body = next(iter(named_task.items()))
            task_body = dict(task_body)
            task_body["then"] = outgoing[current]
            named_task = {task_name: task_body}
        ordered.append(named_task)
        current = outgoing.get(current, "")
    if len(visited) != len(by_id):
        raise ValueError("graph contains disconnected nodes")
    raw = {"document": document, "do": ordered}
    load(raw)
    return raw


def graph_to_yaml(graph: dict[str, Any]) -> str:
    """Export a visual graph as validated YAML."""
    return yaml.safe_dump(graph_to_document(graph), sort_keys=False)
