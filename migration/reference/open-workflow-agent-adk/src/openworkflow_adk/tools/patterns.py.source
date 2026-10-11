"""Backward-compatible facade for :mod:`openworkflow_adk.devtools.patterns`.

The ``tools`` package was split into ``interop`` (cross-runtime formats) and
``devtools`` (diagnostics/devUX) under C24.25. This module re-exports the
implementation for existing callers; new code should import from the new
location.
"""

import warnings

from openworkflow_adk.devtools.patterns import (
    debate_pattern,
    hierarchical_pattern,
    map_reduce_pattern,
)

__all__ = ["debate_pattern", "hierarchical_pattern", "map_reduce_pattern"]

warnings.warn(
    "openworkflow_adk.tools.patterns is deprecated; "
    "import from openworkflow_adk.devtools.patterns.",
    DeprecationWarning,
    stacklevel=2,
)
