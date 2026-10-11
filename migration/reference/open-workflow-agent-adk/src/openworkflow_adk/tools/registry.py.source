"""Backward-compatible facade for :mod:`openworkflow_adk.registry`.

The ``tools`` package was split into ``interop`` (cross-runtime formats) and
``devtools`` (diagnostics/devUX) under C24.25. This module re-exports the
implementation for existing callers; new code should import from the new
location.
"""

import warnings

from openworkflow_adk.registry import WorkflowRegistry, WorkflowSearchResult

__all__ = ["WorkflowRegistry", "WorkflowSearchResult"]

warnings.warn(
    "openworkflow_adk.tools.registry is deprecated; import from openworkflow_adk.registry.",
    DeprecationWarning,
    stacklevel=2,
)
