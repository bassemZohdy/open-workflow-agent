"""Backward-compatible facade for :mod:`openworkflow_adk.devtools.usage`.

The ``tools`` package was split into ``interop`` (cross-runtime formats) and
``devtools`` (diagnostics/devUX) under C24.25. This module re-exports the
implementation for existing callers; new code should import from the new
location.
"""

import warnings

from openworkflow_adk.devtools.usage import UsageMetrics

__all__ = ["UsageMetrics"]

warnings.warn(
    "openworkflow_adk.tools.usage is deprecated; import from openworkflow_adk.devtools.usage.",
    DeprecationWarning,
    stacklevel=2,
)
