"""Backward-compatible facade for :mod:`openworkflow_adk.devtools.diagnostics`.

The ``tools`` package was split into ``interop`` (cross-runtime formats) and
``devtools`` (diagnostics/devUX) under C24.25. This module re-exports the
implementation for existing callers; new code should import from the new
location.
"""

import warnings

from openworkflow_adk.devtools.diagnostics import (
    Diagnostic,
    lint_workflow,
    workflow_mermaid,
    workflow_plan,
)

__all__ = ["Diagnostic", "lint_workflow", "workflow_mermaid", "workflow_plan"]

warnings.warn(
    "openworkflow_adk.tools.diagnostics is deprecated; "
    "import from openworkflow_adk.devtools.diagnostics.",
    DeprecationWarning,
    stacklevel=2,
)
