"""Backward-compatible facade for :mod:`openworkflow_adk.devtools.diagnostics_server`.

The ``tools`` package was split into ``interop`` (cross-runtime formats) and
``devtools`` (diagnostics/devUX) under C24.25. This module re-exports the
implementation for existing callers; new code should import from the new
location.
"""

import warnings

from openworkflow_adk.devtools.diagnostics_server import DiagnosticsServer, serve_stdio

__all__ = ["DiagnosticsServer", "serve_stdio"]

warnings.warn(
    "openworkflow_adk.tools.diagnostics_server is deprecated; "
    "import from openworkflow_adk.devtools.diagnostics_server.",
    DeprecationWarning,
    stacklevel=2,
)
