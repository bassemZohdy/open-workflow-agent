"""Backward-compatible facade for :mod:`openworkflow_adk.devtools.visual`.

The ``tools`` package was split into ``interop`` (cross-runtime formats) and
``devtools`` (diagnostics/devUX) under C24.25. This module re-exports the
implementation for existing callers; new code should import from the new
location.
"""

import warnings

from openworkflow_adk.devtools.visual import graph_to_document, graph_to_yaml

__all__ = ["graph_to_document", "graph_to_yaml"]

warnings.warn(
    "openworkflow_adk.tools.visual is deprecated; import from openworkflow_adk.devtools.visual.",
    DeprecationWarning,
    stacklevel=2,
)
