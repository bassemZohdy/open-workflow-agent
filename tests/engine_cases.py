"""Shared cross-engine test cases.

The ADK and LangGraph pair is always exercised, matching the established
contract-suite behavior. The Agent Framework engine is appended only when its
native dependency is importable as a real module (not a same-named test
directory resolved as an empty namespace package), so environments without it
keep their existing case sets while the Agent Framework CI job exercises the
real native adapter.
"""

from __future__ import annotations

import importlib
import os
from typing import Any


def _import_real_module(name: str) -> Any:
    module = importlib.import_module(name)
    # Namespace packages (empty test directories that share a name with a
    # native dependency) import successfully but are not the real module.
    if getattr(module, "__file__", None) is None:
        raise ImportError(f"{name} resolved to a namespace package, not a real module")
    return module


def engine_cases() -> list[tuple[str, Any]]:
    cases: list[tuple[str, Any]] = []
    for label, adapter_module, class_name, native_module in (
        ("adk", "open_workflow_agent_adk", "AdkWorkflowEngine", None),
        ("langgraph", "open_workflow_agent_langgraph", "LangGraphWorkflowEngine", None),
        (
            "agent-framework",
            "open_workflow_agent_agent_framework",
            "AgentFrameworkWorkflowEngine",
            "agent_framework",
        ),
    ):
        try:
            if native_module is not None:
                _import_real_module(native_module)
            module = _import_real_module(adapter_module)
        except Exception:  # native dependency or adapter module unavailable
            continue
        cases.append((label, getattr(module, class_name)))
    required = os.getenv("OWA_TEST_REQUIRED_ENGINE")
    if required:
        if required not in {"adk", "langgraph"}:
            raise RuntimeError("OWA_TEST_REQUIRED_ENGINE must be either 'adk' or 'langgraph'")
        selected = [case for case in cases if case[0] == required]
        if not selected:
            raise RuntimeError(
                f"OWA_TEST_REQUIRED_ENGINE={required!r}, but its real adapter is unavailable"
            )
        return selected
    return cases
