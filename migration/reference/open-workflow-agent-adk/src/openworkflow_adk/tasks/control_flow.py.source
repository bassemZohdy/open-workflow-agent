"""Builders for nested, error-handling, and looping control flow."""

from __future__ import annotations

import asyncio
import inspect
import random
import time
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from openworkflow_adk.adk_compat import FunctionNode, unwrap_dynamic_error
from openworkflow_adk.durations import duration_seconds
from openworkflow_adk.errors import OpenWorkflowError
from openworkflow_adk.expressions import condition, evaluate
from openworkflow_adk.models import Task, TaskItem

from .simple import _dynamic

if TYPE_CHECKING:
    from openworkflow_adk.translator import NodeBuilderRegistry


def _resolve_retry_policy(
    catch: dict[str, Any] | None,
    retry_policies: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Resolve ``catch.retry`` from an inline policy or ``use.retries``."""
    if catch is None:
        return None
    retry = catch.get("retry")
    if retry is None or isinstance(retry, bool):
        return None
    if isinstance(retry, str):
        resolved = (retry_policies or {}).get(retry)
        if not isinstance(resolved, dict):
            raise ValueError(
                f"catch references unknown retry policy {retry!r}; define it under use.retries"
            )
        return resolved
    if isinstance(retry, dict):
        return retry
    return None


def _error_matches_filter(error: OpenWorkflowError, error_filter: dict[str, Any]) -> bool:
    """Whether the error satisfies every field of ``catch.errors.with``."""
    checks = (
        ("type", error.type),
        ("status", error.status),
        ("instance", error.instance),
        ("title", error.title),
        ("details", error.detail),
        ("detail", error.detail),
    )
    for field, actual in checks:
        expected = error_filter.get(field)
        if expected is None:
            continue
        if actual != expected:
            return False
    return True


def _retry_allowed(
    policy: dict[str, Any],
    attempt: int,
    error: OpenWorkflowError,
    state: dict[str, Any],
    deadline: float | None,
) -> bool:
    """Whether the failed attempt may be retried under the policy.

    ``limit.attempt.count`` is the total number of attempts, including the
    initial execution. Therefore ``count: 1`` permits no retry and ``count: 2``
    permits one retry.
    """
    limit = policy.get("limit") or {}
    attempt_limit = (limit.get("attempt") or {}).get("count")
    if attempt_limit is not None and attempt + 1 >= int(attempt_limit):
        return False
    if deadline is not None and time.monotonic() >= deadline:
        return False
    context = {"error": error.as_dict(), "context": state}
    when = policy.get("when")
    if when is not None and not condition(when, context):
        return False
    except_when = policy.get("exceptWhen")
    if except_when is not None and condition(except_when, context):
        return False
    return True


def _retry_delay(policy: dict[str, Any], attempt: int) -> float:
    """Compute the pre-retry delay including backoff and jitter."""
    base = duration_seconds(policy["delay"]) if policy.get("delay") is not None else 0.0
    backoff = policy.get("backoff") or {}
    if "exponential" in backoff:
        ratio = float((backoff["exponential"] or {}).get("ratio", 2) or 2)
        base = base * (ratio**attempt)
    elif "linear" in backoff:
        base = base * (attempt + 1)
    jitter = policy.get("jitter") or {}
    jitter_from, jitter_to = jitter.get("from"), jitter.get("to")
    if jitter_from is not None or jitter_to is not None:
        low = duration_seconds(jitter_from) if jitter_from is not None else 0.0
        high = duration_seconds(jitter_to) if jitter_to is not None else 0.0
        if high < low:
            low, high = high, low
        base += random.uniform(low, high)
    return max(0.0, base)


def _run_nested_builder(
    name: str, children: list[TaskItem], registry: NodeBuilderRegistry
) -> FunctionNode:
    child_nodes = [_dynamic(registry.build(item.name, item.task)) for item in children]

    async def run_nested(ctx: Any, node_input: Any = None) -> Any:
        result = None
        for child in child_nodes:
            result = await ctx.run_node(child, node_input=node_input)
        return result

    return _dynamic(FunctionNode(func=run_nested, name=name))


def _try_builder(name: str, task: Task, registry: NodeBuilderRegistry) -> FunctionNode:
    try_node = _run_nested_builder(name, task.try_ or [], registry)
    _dynamic(try_node)
    catch = task.catch
    catch_children = []
    if isinstance(catch, dict):
        catch_children = [TaskItem.model_validate(item) for item in catch.get("do", [])]
    catch_node = _run_nested_builder(f"{name}__catch", catch_children, registry)
    _dynamic(catch_node)
    retry_policy = _resolve_retry_policy(catch, getattr(registry, "retry_policies", None))
    error_filter = None
    errors_config = catch.get("errors") if isinstance(catch, dict) else None
    if isinstance(errors_config, dict) and isinstance(errors_config.get("with"), dict):
        error_filter = errors_config["with"]
    retry_deadline = None
    if retry_policy is not None:
        limit_duration = (retry_policy.get("limit") or {}).get("duration")
        if limit_duration is not None:
            retry_deadline = time.monotonic() + duration_seconds(limit_duration)

    async def run_try(ctx: Any) -> Any:
        """Apply independent retry and self-heal budgets to the try body.

        Retry and self-heal attempts compose additively: a self-heal diagnosis
        may request another try after the retry policy is exhausted, while the
        retry counter remains exhausted for subsequent failures.
        """
        policy = task.effective_self_heal()
        heal_attempts = int(policy.get("max_attempts", 1)) if isinstance(policy, dict) else 1
        heal_attempt = 0
        retry_attempt = 0
        while True:
            try:
                return await ctx.run_node(try_node)
            except Exception as wrapped:
                error = unwrap_dynamic_error(wrapped)
                if not isinstance(error, Exception):
                    raise
                normalized = OpenWorkflowError.from_exception(error)
                if error_filter is not None and not _error_matches_filter(normalized, error_filter):
                    raise
                if retry_policy is not None and _retry_allowed(
                    retry_policy, retry_attempt, normalized, ctx.state.to_dict(), retry_deadline
                ):
                    await asyncio.sleep(_retry_delay(retry_policy, retry_attempt))
                    retry_attempt += 1
                    continue
                healer = registry.self_healer
                heal_attempt += 1
                if healer is not None and heal_attempt < max(1, heal_attempts):
                    diagnosis = healer(error, ctx.state.to_dict())
                    if inspect.isawaitable(diagnosis):
                        diagnosis = await diagnosis
                    if isinstance(diagnosis, dict) and diagnosis.get("retry"):
                        patch = diagnosis.get("state")
                        if isinstance(patch, dict):
                            ctx.state.update(patch)
                        continue
                if not catch_children:
                    raise
                as_name = catch.get("as") if isinstance(catch, dict) else None
                if as_name:
                    ctx.state[as_name] = normalized.as_dict()
                return await ctx.run_node(catch_node)

    return _dynamic(FunctionNode(func=run_try, name=name))


def _for_builder(name: str, task: Task, registry: NodeBuilderRegistry) -> FunctionNode:
    children = task.do or []
    child_nodes = [_dynamic(registry.build(item.name, item.task)) for item in children]
    configuration = task.for_ or {}
    each_name = configuration.get("each", "item")
    index_name = configuration.get("at")
    collection_expression = configuration.get("in", "[]")
    while_expression = task.while_

    async def run_for(ctx: Any) -> list[Any]:
        state = ctx.state.to_dict()
        collection = evaluate(collection_expression, state)
        results = []
        for index, item in enumerate(collection or []):
            if while_expression and not evaluate(while_expression, ctx.state.to_dict()):
                break
            ctx.state[each_name] = item
            ctx.session.state[each_name] = item
            if index_name:
                ctx.state[index_name] = index
                ctx.session.state[index_name] = index
            result = None
            for child in child_nodes:
                loop_input = {each_name: item}
                if index_name:
                    loop_input[index_name] = index
                result = await ctx.run_node(child, node_input=loop_input)
            results.append(result)
        return results

    return _dynamic(FunctionNode(func=run_for, name=name))
