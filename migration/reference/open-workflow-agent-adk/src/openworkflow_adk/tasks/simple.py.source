"""Builders for simple and event-oriented workflow tasks."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from openworkflow_adk._utils import response_body
from openworkflow_adk.adk_compat import DEFAULT_ROUTE, FunctionNode
from openworkflow_adk.errors import OpenWorkflowError
from openworkflow_adk.expressions import bind, condition, evaluate
from openworkflow_adk.models import Task, TaskItem
from openworkflow_adk.resources.broker import Broker
from openworkflow_adk.security.auth import resolve_authentication
from openworkflow_adk.security.security import guarded_async_client, validate_egress
from openworkflow_adk.suspension import WorkflowSuspended

from .common import _noop

if TYPE_CHECKING:
    from openworkflow_adk.translator import NodeBuilderRegistry


def _generic_builder(name: str, _task: Task) -> FunctionNode:
    return FunctionNode(func=_noop, name=name)


def _wait_builder(
    name: str, task: Task, *, suspend_long_waits: bool = False, suspend_after: float = 3600
) -> FunctionNode:
    async def wait(ctx: Any) -> None:
        if isinstance(task.wait, dict):
            seconds = float(task.wait.get("seconds", 0))
            seconds += float(task.wait.get("minutes", 0)) * 60
            seconds += float(task.wait.get("hours", 0)) * 3600
            seconds += float(task.wait.get("milliseconds", 0)) / 1000
            if suspend_long_waits and seconds >= suspend_after:
                raise WorkflowSuspended(
                    task=name,
                    resume_at=datetime.now(timezone.utc) + timedelta(seconds=seconds),
                )
            await asyncio.sleep(seconds)

    return FunctionNode(func=wait, name=name)


def _is_expression(value: Any) -> bool:
    """Whether the value is a `${...}` runtime expression string."""
    return isinstance(value, str) and value.strip().startswith("${")


def _raise_builder(
    name: str,
    task: Task,
    error_definitions: dict[str, Any] | None = None,
    document_reference: dict[str, Any] | None = None,
) -> FunctionNode:
    async def raise_error(ctx: Any) -> None:
        error = (task.raise_ or {}).get("error", task.raise_ or {})
        if isinstance(error, str):
            definition = (error_definitions or {}).get(error)
            if not isinstance(definition, dict):
                raise ValueError(
                    f"raise references unknown error {error!r}; define it under use.errors"
                )
            error = definition
        if not isinstance(error, dict):
            error = {"detail": str(error)}
        data = {
            "context": ctx.state.to_dict(),
            "workflow": {"definition": {"document": dict(document_reference or {})}},
        }

        def resolve(value: Any) -> Any:
            # Only `${...}` strings are runtime expressions; plain strings such
            # as error type URIs and titles are literals.
            return evaluate(value, data) if _is_expression(value) else value

        status = error.get("status")
        if _is_expression(status):
            status = evaluate(status, data)
        raise OpenWorkflowError(
            type=str(resolve(error.get("type", "about:blank"))),
            status=int(status) if status is not None else None,
            title=resolve(error.get("title")),
            detail=resolve(error.get("detail")),
            instance=resolve(error.get("instance")),
        )

    return FunctionNode(func=raise_error, name=name)


def _http_builder(
    name: str,
    task: Task,
    policies: dict[str, Any] | None = None,
    environ: dict[str, str] | None = None,
) -> FunctionNode:
    async def request(ctx: Any) -> Any:
        state = ctx.state.to_dict()
        arguments = bind(task.with_ or {}, state)
        endpoint = arguments.get("endpoint")
        endpoint_auth = endpoint.get("authentication") if isinstance(endpoint, dict) else None
        if isinstance(endpoint, dict):
            endpoint = endpoint.get("uri")
        if not endpoint:
            raise ValueError(f"HTTP task {name!r} requires with.endpoint")
        validate_egress(endpoint, environ)
        method = str(arguments.get("method", "get")).upper()
        output = arguments.get("output", "content")
        follow_redirects = bool(arguments.get("redirect", False))
        auth, auth_headers = resolve_authentication(endpoint_auth, policies, environ)
        headers = dict(arguments.get("headers") or {})
        headers.update(auth_headers)
        async with guarded_async_client(environ, follow_redirects=follow_redirects) as client:
            response = await client.request(
                method,
                endpoint,
                headers=headers,
                params=arguments.get("query"),
                json=arguments.get("body"),
                auth=auth,
            )
            if 300 <= response.status_code < 400 and not follow_redirects:
                response.raise_for_status()
            response.raise_for_status()
            if output == "raw":
                return response.content
            if output == "response":
                return {
                    "status": response.status_code,
                    "headers": dict(response.headers),
                    "body": response_body(response),
                }
            return response_body(response)

    return FunctionNode(func=request, name=name)


def _set_builder(name: str, task: Task) -> FunctionNode:
    async def set_values(ctx: Any, node_input: Any = None) -> None:
        if isinstance(task.set, dict):
            state = ctx.state.to_dict()
            if isinstance(node_input, dict):
                state.update(node_input)
            for key, value in task.set.items():
                ctx.state[key] = evaluate(value, state)
                state[key] = ctx.state[key]

    return FunctionNode(func=set_values, name=name)


def _emit_builder(name: str, task: Task, broker: Broker | None) -> FunctionNode:
    async def emit(ctx: Any) -> None:
        if broker is None:
            return
        event = ((task.emit or {}).get("event") or {}).get("with", {})
        await broker.publish(bind(event, ctx.state.to_dict()))

    return FunctionNode(func=emit, name=name)


def _listen_builder(
    name: str,
    task: Task,
    broker: Broker | None,
    registry: NodeBuilderRegistry | None = None,
    *,
    suspend_listens: bool = False,
) -> FunctionNode:
    # foreach children are resolved at build time: the listen node itself must
    # be marked dynamic (`rerun_on_resume=True`) because it schedules child
    # nodes via `ctx.run_node`.
    foreach_config = (task.model_extra or {}).get("foreach")
    if not isinstance(foreach_config, dict):
        foreach_config = None
    child_nodes: list[Any] = []
    item_name, index_name = "event", "index"
    if foreach_config is not None:
        if registry is None:
            raise ValueError(f"listen task {name!r} foreach requires the translator registry")
        item_name = str(foreach_config.get("item", "event"))
        index_name = str(foreach_config.get("at", "index"))
        for entry in foreach_config.get("do", []):
            item = TaskItem.model_validate(entry)
            child_nodes.append(_dynamic(registry.build(f"{name}__each_{item.name}", item.task)))

    async def listen(ctx: Any) -> Any:
        if broker is None:
            return None
        listen_config = task.listen or {}
        configuration = listen_config.get("to", {})
        read_mode = listen_config.get("read", "data")
        filters: list[dict[str, Any]] = []
        strategy = "one"
        until_expression: str | None = None
        until_strategy: dict[str, Any] | None = None
        if isinstance(configuration, dict):
            if "one" in configuration:
                filters = [configuration["one"]]
            elif "any" in configuration:
                strategy, filters = "any", configuration["any"]
            elif "all" in configuration:
                strategy, filters = "all", configuration["all"]
            maybe_until = configuration.get("until")
            if strategy == "any":
                if isinstance(maybe_until, str):
                    until_expression = maybe_until
                elif isinstance(maybe_until, dict):
                    until_strategy = maybe_until
        if suspend_listens:
            raise WorkflowSuspended(
                task=name,
                resume_at=datetime.now(timezone.utc),
                reason="broker_listen",
            )

        correlations: dict[str, Any] = {}
        until_correlations: dict[str, Any] = {}
        until_consumed_filters: set[int] = set()

        def _matches_filter(
            event: dict[str, Any], event_filter: Any, correlation_values: dict[str, Any]
        ) -> bool:
            if not isinstance(event_filter, dict):
                return True
            properties = event_filter.get("with") or {}
            if not isinstance(properties, dict):
                properties = {}
            if properties.get("type") is not None and event.get("type") != properties.get("type"):
                return False
            if properties.get("source") is not None and event.get("source") != properties.get(
                "source"
            ):
                return False
            for correlate_name, rule in (event_filter.get("correlate") or {}).items():
                if not isinstance(rule, dict):
                    continue
                value = evaluate(rule.get("from"), event) if rule.get("from") is not None else None
                if correlate_name not in correlation_values:
                    correlation_values[correlate_name] = value
                expected = rule.get("expect")
                if expected is not None:
                    expected = (
                        evaluate(expected, {"event": event, "context": ctx.state.to_dict()})
                        if isinstance(expected, str)
                        else expected
                    )
                    if value != expected:
                        return False
                elif correlation_values.get(correlate_name) != value:
                    return False
            return True

        def _matches_any(event: dict[str, Any]) -> bool:
            if not filters:
                return True
            return any(
                _matches_filter(event, event_filter, correlations) for event_filter in filters
            )

        def _matches_until(event: dict[str, Any]) -> bool:
            if until_strategy is None:
                return False
            if "one" in until_strategy:
                return _matches_filter(event, until_strategy["one"], until_correlations)
            if "any" in until_strategy:
                until_filters = until_strategy.get("any") or []
                return not until_filters or any(
                    _matches_filter(event, event_filter, until_correlations)
                    for event_filter in until_filters
                )
            if "all" in until_strategy:
                until_filters = until_strategy.get("all") or []
                for index, event_filter in enumerate(until_filters):
                    if index not in until_consumed_filters and _matches_filter(
                        event, event_filter, until_correlations
                    ):
                        until_consumed_filters.add(index)
                        break
                return len(until_consumed_filters) == len(until_filters)
            return False

        async def consume_one(event_filter: Any = None) -> tuple[dict[str, Any], bool]:
            while True:
                event = await broker.consume()
                if until_strategy is not None and _matches_until(event):
                    return event, True
                matched = (
                    _matches_any(event)
                    if event_filter is None
                    else _matches_filter(event, event_filter, correlations)
                )
                if matched:
                    return event, False

        def read(event: dict[str, Any]) -> Any:
            if read_mode == "raw":
                return getattr(event, "raw_data", event.get("data"))
            if read_mode == "envelope":
                return dict(event)
            return event.get("data")

        consumed: list[Any] = []
        results: list[Any] = []
        while True:
            if strategy == "all" and len(filters) > len(consumed):
                event, is_until_event = await consume_one(filters[len(consumed)])
            else:
                event, is_until_event = await consume_one()
            value = read(event)
            if is_until_event:
                break
            if until_expression is not None and evaluate(until_expression, [*consumed, value]):
                break
            consumed.append(value)
            index = len(consumed) - 1
            if child_nodes:
                ctx.state[item_name] = value
                ctx.session.state[item_name] = value
                ctx.state[index_name] = index
                ctx.session.state[index_name] = index
                for child in child_nodes:
                    results.append(
                        await ctx.run_node(child, node_input={item_name: value, index_name: index})
                    )
            else:
                results.append(value)
            if strategy == "all":
                if len(consumed) >= len(filters):
                    break
            elif until_expression is None and until_strategy is None:
                break
        has_until = until_expression is not None or until_strategy is not None
        if has_until and not child_nodes:
            return consumed
        if strategy == "all" and not child_nodes and not has_until:
            return results
        return results[-1] if results else None

    node = FunctionNode(func=listen, name=name)
    if child_nodes:
        # The listen closure schedules child nodes via `ctx.run_node`; ADK
        # requires such coordinators to be resumable (`rerun_on_resume=True`).
        return _dynamic(node)
    return node


def _switch_builder(name: str, task: Task) -> FunctionNode:
    async def route(ctx: Any) -> None:
        state = ctx.state.to_dict()
        if task.if_ is not None and not condition(task.if_, state):
            # A skipped conditional switch still needs a valid route; follow the
            # default route so the graph remains well-formed.
            ctx.route = DEFAULT_ROUTE
            return
        default = DEFAULT_ROUTE
        for case in task.switch or []:
            if not isinstance(case, dict):
                continue
            case_name, configuration = next(iter(case.items()))
            if case_name == "default":
                default = case_name
            elif isinstance(configuration, dict) and evaluate(configuration.get("when"), state):
                ctx.route = case_name
                return
        ctx.route = default

    return FunctionNode(func=route, name=name)


def _compete_builder(name: str, branches: list[Any]) -> FunctionNode:
    async def compete(ctx: Any) -> Any:
        tasks = {
            asyncio.create_task(ctx.run_node(branch, use_sub_branch=True)) for branch in branches
        }
        if not tasks:
            return None
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for pending_task in pending:
            pending_task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        winner = next(iter(done))
        return winner.result()

    return _dynamic(FunctionNode(func=compete, name=name))


def _dynamic(node: Any) -> Any:
    """Mark a node safe for ADK dynamic execution via ``ctx.run_node``."""
    if hasattr(node, "rerun_on_resume"):
        node.rerun_on_resume = True
    return node
