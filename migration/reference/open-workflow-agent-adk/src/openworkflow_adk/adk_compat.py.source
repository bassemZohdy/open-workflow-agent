"""Consolidated compatibility seam over Google ADK private workflow APIs.

Several modules need ADK's internal workflow primitives (``FunctionNode``,
``DEFAULT_ROUTE``, ``JoinNode``) plus the public ``Workflow`` graph class.
Importing the private ``google.adk.workflow._*`` modules directly from nine
places makes upgrades fragile; this module is the single point that must change
when ADK renames or moves them. The nightly canary CI job (see
``.github/workflows/canary.yml``) runs the full suite against the latest ADK to
detect drift here early.
"""

from __future__ import annotations

from google.adk.workflow import Workflow
from google.adk.workflow._errors import DynamicNodeFailError
from google.adk.workflow._function_node import FunctionNode
from google.adk.workflow._graph import DEFAULT_ROUTE
from google.adk.workflow._join_node import JoinNode

__all__ = [
    "Workflow",
    "FunctionNode",
    "DEFAULT_ROUTE",
    "JoinNode",
    "DynamicNodeFailError",
    "unwrap_dynamic_error",
]


def unwrap_dynamic_error(error: BaseException) -> BaseException:
    """Unwrap ``ctx.run_node`` failures to the original user exception.

    ADK wraps dynamic-child failures in ``DynamicNodeFailError``; workflow
    semantics (catch filters, retry policies, error ``as`` mappings) must
    inspect the underlying error, not the wrapper.
    """
    while isinstance(error, DynamicNodeFailError) and error.error is not None:
        error = error.error
    return error
