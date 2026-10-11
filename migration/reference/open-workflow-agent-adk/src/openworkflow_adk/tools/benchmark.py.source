"""Backward-compatible facade for :mod:`openworkflow_adk.devtools.benchmark`.

The ``tools`` package was split into ``interop`` (cross-runtime formats) and
``devtools`` (diagnostics/devUX) under C24.25. This module re-exports the
implementation for existing callers; new code should import from the new
location.
"""

import warnings

from openworkflow_adk.devtools.benchmark import benchmark, main

__all__ = ["benchmark", "main"]

warnings.warn(
    "openworkflow_adk.tools.benchmark is deprecated; "
    "import from openworkflow_adk.devtools.benchmark.",
    DeprecationWarning,
    stacklevel=2,
)
