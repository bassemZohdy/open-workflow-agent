"""Backward-compatible facade for the workflow suspension signal.

``WorkflowSuspended`` moved to core :mod:`openworkflow_adk.suspension` under
C24.18 so task builders do not depend on ``ops``. This module re-exports the
exception for existing callers; new code should import from
:mod:`openworkflow_adk.suspension`.
"""

import warnings

from openworkflow_adk.suspension import WorkflowSuspended

__all__ = ["WorkflowSuspended"]

warnings.warn(
    "openworkflow_adk.ops.suspension is deprecated; "
    "import WorkflowSuspended from openworkflow_adk.suspension.",
    DeprecationWarning,
    stacklevel=2,
)
