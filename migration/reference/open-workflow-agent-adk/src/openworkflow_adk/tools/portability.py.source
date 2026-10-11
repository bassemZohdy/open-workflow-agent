"""Backward-compatible facade for :mod:`openworkflow_adk.devtools.portability`.

The ``tools`` package was split into ``interop`` (cross-runtime formats) and
``devtools`` (diagnostics/devUX) under C24.25. This module re-exports the
implementation for existing callers; new code should import from the new
location.
"""

import warnings

from openworkflow_adk.devtools.portability import portability_report

__all__ = ["portability_report"]

warnings.warn(
    "openworkflow_adk.tools.portability is deprecated; "
    "import from openworkflow_adk.devtools.portability.",
    DeprecationWarning,
    stacklevel=2,
)
