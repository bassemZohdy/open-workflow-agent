"""OpenWorkflow-to-ADK translation orchestration and graph assembly."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from typing import Any

from openworkflow_adk.adk_compat import DEFAULT_ROUTE, FunctionNode, JoinNode, Workflow
from openworkflow_adk.expressions import condition
from openworkflow_adk.models import TASK_KEYS, OpenWorkflowDocument, ProviderConfig, Task, TaskItem
from openworkflow_adk.registry import WorkflowRegistry
from openworkflow_adk.resources.broker import Broker
from openworkflow_adk.run_config import RunConfig
from openworkflow_adk.state import derive_state_schema

from .tasks import common as _task_common
from .tasks.agent import _agent_builder
from .tasks.base_semantics import task_base_marks, wrap_task_base
from .tasks.call import _function_builder, _grpc_builder
from .tasks.common import NodeBuilder, _adk_name
from .tasks.control_flow import _for_builder, _run_nested_builder, _try_builder
from .tasks.events import _a2a_builder, _asyncapi_builder, _mcp_builder, _openapi_builder
from .tasks.run import _run_builder
from .tasks.simple import (
    _compete_builder,
    _dynamic,
    _emit_builder,
    _generic_builder,
    _http_builder,
    _listen_builder,
    _raise_builder,
    _set_builder,
    _switch_builder,
    _wait_builder,
)

_kill_process_tree = _task_common._kill_process_tree
_sandbox_preexec = _task_common._sandbox_preexec


def _parse_extensions(raw: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Normalize ``use.extensions`` entries to bare extension mappings."""
    parsed: list[dict[str, Any]] = []
    for entry in raw or []:
        extension: Any = entry
        if isinstance(entry, dict) and len(entry) == 1 and not {"extend", "when"} & set(entry):
            _, extension = next(iter(entry.items()))
        if isinstance(extension, dict):
            parsed.append(extension)
    return parsed


class NodeBuilderRegistry:
    """Dispatch task kinds to ADK node builders."""

    def __init__(
        self,
        state_schema: type | None = None,
        broker: Broker | None = None,
        auth_policies: dict[str, Any] | None = None,
        environ: dict[str, str] | None = None,
        model_factory: Callable[[str], Any] | None = None,
        function_registry: dict[str, Callable[..., Any]] | None = None,
        function_tasks: dict[str, Task] | None = None,
        model_specs: dict[str, Any] | None = None,
        workflow_registry: WorkflowRegistry | None = None,
        provider_configs: dict[str, ProviderConfig] | None = None,
        provider_factory: Callable[[str, ProviderConfig], Any] | None = None,
        suspend_long_waits: bool = False,
        suspend_after: float = 3600,
        resume_input: Any = None,
        suspend_listens: bool = False,
        memoization: Any = None,
        self_healer: Callable[[Exception, dict[str, Any]], Any] | None = None,
        agent_defaults: Any = None,
        retry_policies: dict[str, Any] | None = None,
        error_definitions: dict[str, Any] | None = None,
        extensions: list[dict[str, Any]] | None = None,
        timeouts: dict[str, Any] | None = None,
        document_reference: dict[str, Any] | None = None,
    ) -> None:
        self.state_schema = state_schema
        self.broker = broker
        self.auth_policies = auth_policies or {}
        self.environ = environ
        self.model_factory = model_factory
        self.function_registry = function_registry or {}
        self.function_tasks = function_tasks or {}
        self.model_specs = model_specs or {}
        self.workflow_registry = workflow_registry
        self.provider_configs = provider_configs or {}
        self.provider_factory = provider_factory
        self.suspend_long_waits = suspend_long_waits
        self.suspend_after = suspend_after
        self.resume_input = resume_input
        self.suspend_listens = suspend_listens
        self.memoization = memoization
        self.self_healer = self_healer
        self.agent_defaults = agent_defaults
        self.retry_policies = retry_policies or {}
        self.error_definitions = error_definitions or {}
        self.extensions = extensions or []
        self.timeouts = timeouts or {}
        self.document_reference = document_reference or {}
        self._call_builders: dict[str, NodeBuilder] = {}
        self._builders: dict[str, NodeBuilder] = {key: _generic_builder for key in TASK_KEYS}
        self._builders.update(
            {
                "wait": lambda name, task: _wait_builder(
                    name,
                    task,
                    suspend_long_waits=self.suspend_long_waits,
                    suspend_after=self.suspend_after,
                ),
                "raise": lambda name, task: _raise_builder(
                    name,
                    task,
                    error_definitions=self.error_definitions,
                    document_reference=self.document_reference,
                ),
                "set": _set_builder,
                "switch": _switch_builder,
                "call:http": _http_builder,
            }
        )

    def register(self, task_kind: str, builder: NodeBuilder) -> None:
        if task_kind not in TASK_KEYS:
            raise KeyError(f"unknown OpenWorkflow task kind: {task_kind}")
        self._builders[task_kind] = builder

    def register_call(self, scheme: str, builder: NodeBuilder) -> None:
        """Register a plugin builder for an extension ``call`` scheme."""
        if not scheme or scheme in {"http", "openapi", "grpc", "asyncapi", "a2a", "mcp"}:
            raise ValueError(f"call scheme is reserved or empty: {scheme!r}")
        self._call_builders[scheme] = builder

    def build(self, name: str, task: Task) -> Any:
        kind = task_kind(task)
        agent_config = task.effective_agent()
        if agent_config is not None:
            agent = _agent_builder(
                name,
                task,
                agent_config,
                self.model_factory,
                self.model_specs,
                self.environ,
                self.function_registry,
                self.provider_configs,
                self.provider_factory,
                self.resume_input,
                {
                    case_name
                    for case in task.switch or []
                    for case_name in case
                    if case_name != "default"
                }
                if task.switch
                else None,
                self.agent_defaults,
            )
            agent = self._apply_base_semantics(name, kind, agent, task)
            if task.switch:

                async def routed(ctx: Any) -> Any:
                    result = await ctx.run_node(agent)
                    selected = ctx.state.to_dict().get("workflow:route")
                    ctx.route = selected or DEFAULT_ROUTE
                    return result

                return _dynamic(FunctionNode(func=routed, name=name))
            return agent
        plugin_builder = self._call_builders.get(task.call or "") if kind == "call" else None
        if kind == "do":
            node = _run_nested_builder(name, task.do or [], self)
        elif kind == "try":
            node = _try_builder(name, task, self)
        elif kind == "for":
            node = _for_builder(name, task, self)
        elif kind == "run":
            node = _run_builder(name, task, self)
        elif plugin_builder is not None:
            node = plugin_builder(name, task)
        elif kind == "call" and task.call not in {
            "http",
            "openapi",
            "grpc",
            "asyncapi",
            "a2a",
            "mcp",
        }:
            node = _function_builder(name, task, self.function_registry, self.function_tasks, self)
        elif kind == "call" and task.call == "openapi":
            node = _openapi_builder(name, task)
        elif kind == "call" and task.call == "grpc":
            node = _grpc_builder(name, task)
        elif kind == "call" and task.call == "asyncapi":
            node = _asyncapi_builder(name, task, self.broker)
        elif kind == "call" and task.call == "a2a":
            node = _a2a_builder(name, task)
        elif kind == "call" and task.call == "mcp":
            node = _mcp_builder(name, task)
        elif kind == "emit":
            node = _emit_builder(name, task, self.broker)
        elif kind == "listen":
            node = _listen_builder(
                name,
                task,
                self.broker,
                self,
                suspend_listens=self.suspend_listens,
            )
        elif kind == "call" and task.call == "http":
            node = _http_builder(name, task, self.auth_policies, self.environ)
        else:
            node = self._builders[kind](name, task)
        node = self._apply_base_semantics(name, kind, node, task)
        if isinstance(node, FunctionNode) and self.state_schema is not None:
            node.state_schema = self.state_schema
        return node

    def _apply_base_semantics(self, name: str, kind: str, node: Any, task: Task) -> Any:
        """Apply `use.extensions` injection and taskBase fields to a node.

        Switch tasks are excluded: they route by setting ``ctx.route`` directly
        and a wrapper would drop the route when running the switch as a child
        node. A conditional switch keeps its own ``if`` handling.
        """
        if kind == "switch":
            return node
        node = self._apply_extensions(name, kind, node)
        if task_base_marks(task):
            if isinstance(node, FunctionNode) and self.state_schema is not None:
                node.state_schema = self.state_schema
            node = wrap_task_base(name, task, node, self.timeouts)
        return node

    def _apply_extensions(self, name: str, kind: str, node: Any) -> Any:
        for extension in self.extensions:
            target = extension.get("extend")
            if target not in {kind, "all"}:
                continue
            before = extension.get("before") or []
            after = extension.get("after") or []
            when = extension.get("when")
            if not before and not after:
                continue
            before_nodes = [self._build_extension_task(name, "b", entry) for entry in before]
            after_nodes = [self._build_extension_task(name, "a", entry) for entry in after]
            node = self._wrap_extension(name, node, when, before_nodes, after_nodes)
        return node

    def _build_extension_task(self, parent: str, phase: str, entry: dict[str, Any]) -> Any:
        item = TaskItem.model_validate(entry)
        saved, self.extensions = self.extensions, []
        try:
            return _dynamic(self.build(f"{parent}__ext{phase}_{item.name}", item.task))
        finally:
            self.extensions = saved

    @staticmethod
    def _wrap_extension(
        name: str, node: Any, when: str | None, before_nodes: list[Any], after_nodes: list[Any]
    ) -> Any:
        async def extended(ctx: Any) -> Any:
            state = ctx.state.to_dict()
            if when is None or condition(when, state):
                for before_node in before_nodes:
                    await ctx.run_node(before_node)
                result = await ctx.run_node(node)
                for after_node in after_nodes:
                    await ctx.run_node(after_node)
                return result
            return await ctx.run_node(node)

        return _dynamic(FunctionNode(func=extended, name=name))

    def keys(self) -> tuple[str, ...]:
        return tuple(self._builders)


def task_kind(task: Task) -> str:
    """Return the single schema task discriminator."""
    for key in TASK_KEYS:
        if key == "do" and task.for_ is not None:
            continue
        attribute = key if key not in {"for", "raise", "try"} else f"{key}_"
        if getattr(task, attribute) is not None:
            return key
    raise ValueError("task has no task kind")


def build_workflow(
    document: OpenWorkflowDocument,
    registry: NodeBuilderRegistry | None = None,
    broker: Broker | None = None,
    model_factory: Callable[[str], Any] | None = None,
    function_registry: dict[str, Callable[..., Any]] | None = None,
    workflow_registry: WorkflowRegistry | None = None,
    provider_configs: dict[str, ProviderConfig] | None = None,
    provider_factory: Callable[[str, ProviderConfig], Any] | None = None,
    suspend_long_waits: bool | None = None,
    suspend_after: float | None = None,
    resume_input: Any = None,
    suspend_listens: bool = False,
    memoization: Any = None,
    self_healer: Callable[[Exception, dict[str, Any]], Any] | None = None,
    config: RunConfig | None = None,
) -> Workflow:
    """Build a linear ADK Workflow from a top-level `do` task list.

    Explicit arguments take precedence over ``config``; ``config`` supplies the
    remaining defaults so a :class:`RunConfig` can be threaded through without
    repeating every field.
    """
    state_schema = derive_state_schema(document)
    registry = registry or NodeBuilderRegistry(
        state_schema,
        broker=broker if broker is not None else (config.broker if config else None),
        auth_policies=document.use.authentications,
        model_factory=model_factory
        if model_factory is not None
        else (config.model_factory if config else None),
        function_registry=function_registry
        if function_registry is not None
        else (config.function_registry if config else None),
        function_tasks={
            function_name: Task.model_validate(function_task)
            for function_name, function_task in document.use.functions.items()
        },
        model_specs=document.effective_models(),
        agent_defaults=document.effective_agent_defaults(),
        workflow_registry=workflow_registry
        if workflow_registry is not None
        else (config.workflow_registry if config else None),
        provider_configs=provider_configs or document.effective_providers(),
        provider_factory=provider_factory,
        suspend_long_waits=suspend_long_waits
        if suspend_long_waits is not None
        else (config.suspend_long_waits if config else False) or False,
        suspend_after=suspend_after
        if suspend_after is not None
        else (config.suspend_after if config else 3600) or 3600,
        resume_input=resume_input
        if resume_input is not None
        else (config.resume_input if config else None),
        suspend_listens=suspend_listens,
        memoization=memoization
        if memoization is not None
        else (config.memoization if config else None),
        self_healer=self_healer
        if self_healer is not None
        else (config.self_healer if config else None),
        retry_policies=document.use.retries,
        error_definitions=document.use.errors,
        extensions=_parse_extensions(document.use.extensions),
        timeouts=document.use.timeouts,
        document_reference={
            "name": document.document.name,
            "namespace": document.document.namespace,
            "version": document.document.version,
        },
    )
    items: list[TaskItem] = document.do
    nodes = {item.name: registry.build(item.name, item.task) for item in items}
    fork_parts: dict[str, tuple[list[Any], JoinNode]] = {}
    for item in items:
        if task_kind(item.task) != "fork" or not isinstance(item.task.fork, dict):
            continue
        branches = []
        for branch in item.task.fork.get("branches", []):
            branch_item = TaskItem.model_validate(branch)
            branch_node = _dynamic(registry.build(branch_item.name, branch_item.task))
            nodes[branch_item.name] = branch_node
            branches.append(branch_node)
        if item.task.fork.get("compete"):
            nodes[item.name] = _compete_builder(item.name, branches)
            nodes[item.name].state_schema = state_schema
        elif branches:
            fork_parts[item.name] = (branches, JoinNode(name=f"{item.name}__join"))
    edges: list[tuple[Any, ...]] = []
    if items:
        edges.append(("START", nodes[items[0].name]))
    by_name = {item.name: index for index, item in enumerate(items)}
    pending = deque([0]) if items else deque()
    visited: set[int] = set()
    while pending:
        index = pending.popleft()
        if index in visited:
            continue
        visited.add(index)
        item = items[index]
        if task_kind(item.task) == "fork" and item.name in fork_parts:
            branches, join = fork_parts[item.name]
            edges.append((nodes[item.name], tuple(branches)))
            for branch in branches:
                edges.append((branch, join))
            continue_target = None
            if item.task.then not in {None, "continue", "end", "exit"}:
                continue_target = item.task.then
            elif index + 1 < len(items):
                continue_target = items[index + 1].name
                pending.append(index + 1)
            if continue_target:
                if continue_target not in nodes:
                    raise ValueError(
                        f"task {item.name!r} references unknown task {continue_target!r}"
                    )
                edges.append((join, nodes[continue_target]))
                pending.append(by_name[continue_target])
            continue
        if task_kind(item.task) == "switch":
            routes: dict[str, Any] = {}
            for case in item.task.switch or []:
                if not isinstance(case, dict):
                    continue
                case_name, configuration = next(iter(case.items()))
                target_name = configuration.get("then") if isinstance(configuration, dict) else None
                if target_name in nodes:
                    route_name = DEFAULT_ROUTE if case_name == "default" else case_name
                    routes[route_name] = nodes[target_name]
                    pending.append(by_name[target_name])
            if routes:
                edges.append((nodes[item.name], routes))
            continue
        directive = item.task.then
        if directive in {"end", "exit"}:
            continue
        if directive and directive not in {"continue"}:
            if directive not in nodes:
                raise ValueError(f"task {item.name!r} references unknown task {directive!r}")
            target = nodes[directive]
            pending.append(by_name[directive])
        elif index + 1 < len(items):
            target = nodes[items[index + 1].name]
            pending.append(index + 1)
        else:
            continue
        edges.append((nodes[item.name], target))
    return Workflow(name=_adk_name(document.document.name), state_schema=state_schema, edges=edges)
