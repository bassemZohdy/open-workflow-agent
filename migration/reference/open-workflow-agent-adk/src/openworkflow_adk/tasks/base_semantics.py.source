"""taskBase runtime semantics shared by every task kind.

OpenWorkflow's ``taskBase`` defines ``if``, ``input``, ``output``, ``export``,
and ``timeout`` on every task regardless of kind. They are implemented as a
wrapper node around the kind-specific builder output so all task kinds inherit
the behavior uniformly:

- ``if`` skips the task when the condition evaluates falsy.
- ``input.from`` evaluates a filtering expression and feeds the result to the
  wrapped node as its ``node_input`` (tasks that declare a ``node_input``
  parameter, such as ``set`` and nested ``do`` lists, consume it directly).
- ``output.as`` transforms the task's output.
- ``export.as`` merges an evaluated object into the workflow context.
- ``timeout`` (inline or a ``use.timeouts`` reference) bounds execution with a
  :class:`TimeoutError` when exceeded.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from openworkflow_adk.adk_compat import FunctionNode
from openworkflow_adk.durations import duration_seconds
from openworkflow_adk.expressions import ExpressionError, condition, evaluate
from openworkflow_adk.models import Task

from .simple import _dynamic


def task_base_marks(task: Task) -> bool:
    """Return True when the task declares any taskBase runtime field."""
    return bool(
        task.if_ is not None
        or (isinstance(task.input, dict) and task.input.get("from") is not None)
        or (isinstance(task.output, dict) and task.output.get("as") is not None)
        or (isinstance(task.export, dict) and task.export.get("as") is not None)
        or task.timeout is not None
    )


def resolve_timeout(task: Task, timeouts: Mapping[str, Any] | None = None) -> float | None:
    """Resolve the task timeout in seconds.

    A string ``timeout`` first resolves against the ``use.timeouts`` registry
    and otherwise parses as an ISO 8601 duration literal.
    """
    configured = task.timeout
    if configured is None:
        return None
    if isinstance(configured, str) and timeouts and configured in timeouts:
        configured = timeouts[configured]
    if isinstance(configured, str):
        return duration_seconds(configured)
    if isinstance(configured, dict):
        after = configured.get("after")
        return duration_seconds(after) if after is not None else duration_seconds(configured)
    if isinstance(configured, (int, float)):
        return float(configured)
    return None


def _accepts_node_input(node: Any) -> bool:
    """Whether the wrapped node declares a ``node_input`` parameter."""
    if not isinstance(node, FunctionNode):
        return False
    func = getattr(node, "_func", None)
    if func is None:
        return False
    try:
        import inspect

        return "node_input" in inspect.signature(func).parameters
    except (TypeError, ValueError):
        return False


def wrap_task_base(
    name: str,
    task: Task,
    node: Any,
    timeouts: Mapping[str, Any] | None = None,
) -> Any:
    """Wrap ``node`` so the task's taskBase fields take effect at runtime."""
    timeout = resolve_timeout(task, timeouts)
    skip_when = task.if_
    input_from = task.input.get("from") if isinstance(task.input, dict) else None
    output_as = task.output.get("as") if isinstance(task.output, dict) else None
    export_as = task.export.get("as") if isinstance(task.export, dict) else None
    can_pass_input = _accepts_node_input(node)

    async def run_with_base(ctx: Any) -> Any:
        state = ctx.state.to_dict()
        if skip_when is not None and not condition(skip_when, state):
            return None
        node_input = evaluate(input_from, state) if input_from is not None else None
        if node_input is not None and can_pass_input:
            run = ctx.run_node(node, node_input=node_input)
        else:
            run = ctx.run_node(node)
        if timeout is not None:
            try:
                result = await asyncio.wait_for(run, timeout)
            except asyncio.TimeoutError as error:
                raise TimeoutError(f"task {name!r} exceeded its timeout") from error
        else:
            result = await run
        if output_as is not None:
            view = ctx.state.to_dict()
            if isinstance(node_input, Mapping):
                view = {**node_input, **view}
            result = evaluate(output_as, {**view, "output": result})
        if export_as is not None:
            exported = evaluate(export_as, {**ctx.state.to_dict(), "output": result})
            if not isinstance(exported, Mapping):
                raise ExpressionError(f"task {name!r} export must evaluate to an object")
            for key, value in exported.items():
                ctx.state[key] = value
        return result

    return _dynamic(FunctionNode(func=run_with_base, name=name))
