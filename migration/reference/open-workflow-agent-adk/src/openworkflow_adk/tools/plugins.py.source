"""Backward-compatible facade for :mod:`openworkflow_adk.devtools.plugins`.

The ``tools`` package was split into ``interop`` (cross-runtime formats) and
``devtools`` (diagnostics/devUX) under C24.25. This module re-exports the
implementation for existing callers; new code should import from the new
location.
"""

import warnings

from openworkflow_adk.devtools.plugins import PluginManifest, PluginRegistry

__all__ = ["PluginManifest", "PluginRegistry"]

warnings.warn(
    "openworkflow_adk.tools.plugins is deprecated; import from openworkflow_adk.devtools.plugins.",
    DeprecationWarning,
    stacklevel=2,
)
