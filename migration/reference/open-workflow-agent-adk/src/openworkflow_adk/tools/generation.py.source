"""Backward-compatible facade for :mod:`openworkflow_adk.devtools.generation`.

The ``tools`` package was split into ``interop`` (cross-runtime formats) and
``devtools`` (diagnostics/devUX) under C24.25. This module re-exports the
implementation for existing callers; new code should import from the new
location.
"""

import warnings

from openworkflow_adk.devtools.generation import WorkflowGenerationError, generate_workflow

__all__ = ["WorkflowGenerationError", "generate_workflow"]

warnings.warn(
    "openworkflow_adk.tools.generation is deprecated; "
    "import from openworkflow_adk.devtools.generation.",
    DeprecationWarning,
    stacklevel=2,
)
