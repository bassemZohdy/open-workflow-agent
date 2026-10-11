"""Conformance helpers for cross-runtime document portability."""

from __future__ import annotations

from typing import Any

import yaml

from openworkflow_adk.loader import load
from openworkflow_adk.models import OpenWorkflowDocument
from openworkflow_adk.translator import build_workflow


def portability_report(document: OpenWorkflowDocument) -> dict[str, Any]:
    """Validate a YAML round trip and compile both document representations."""
    raw = document.model_dump(by_alias=True, exclude_none=True)
    raw["do"] = [
        {item.name: item.task.model_dump(by_alias=True, exclude_none=True)} for item in document.do
    ]
    serialized = yaml.safe_dump(raw, sort_keys=False)
    round_tripped = load(serialized)
    build_workflow(document)
    build_workflow(round_tripped)
    original_tasks = [item.name for item in document.do]
    round_trip_tasks = [item.name for item in round_tripped.do]
    return {
        "portable": original_tasks == round_trip_tasks,
        "tasks": original_tasks,
        "round_trip_tasks": round_trip_tasks,
        "dsl": round_tripped.document.dsl,
    }
