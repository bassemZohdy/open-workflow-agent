"""Backward-compatible facade for :mod:`openworkflow_adk.interop.importers`.

The ``tools`` package was split into ``interop`` (cross-runtime formats) and
``devtools`` (diagnostics/devUX) under C24.25. This module re-exports the
implementation for existing callers; new code should import from the new
location.
"""

import warnings

from openworkflow_adk.interop.importers import import_airflow, import_argo

__all__ = ["import_airflow", "import_argo"]

warnings.warn(
    "openworkflow_adk.tools.importers is deprecated; "
    "import from openworkflow_adk.interop.importers.",
    DeprecationWarning,
    stacklevel=2,
)
