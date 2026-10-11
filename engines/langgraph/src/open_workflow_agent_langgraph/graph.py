"""Private native graphs: one node per admitted Open Workflow task."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, TypedDict, cast

from langgraph.graph import END, START, StateGraph
from open_workflow_agent.errors import OwaError, WorkflowExecutionError
from open_workflow_agent.workflow import (
    ExecutionState,
    ScopePlan,
    TaskPlan,
    WorkflowExecutor,
    WorkflowPlan,
)

from .native import NATIVE_SCOPE_STEP_LIMIT


class _GraphState(TypedDict):
    snapshot: str
    counters: str
    next_node: str


def _node_name(task: TaskPlan) -> str:
    return "task_" + hashlib.sha256(task.reference.encode()).hexdigest()


def _snapshot(state: ExecutionState) -> str:
    # Live controls, services, expression evaluators and asyncio tasks never enter
    # native channels. JSON text also avoids serializer-specific integer limits
    # for inert document metadata admitted by the official schema.
    return json.dumps(
        {
            "data": state.data,
            "outputs": state.outputs,
            "variables": state.variables,
            "context": state.context,
            "jump_to": state.jump_to,
            "stop": state.stop,
            "scope_exit": state.scope_exit,
            "transition_selected": state.transition_selected,
            "ended": state.flow.ended.is_set(),
            "end_output": state.flow.output,
            "end_context": state.flow.context,
            "task_visits": state.task_visits,
            "execution_path": state.execution_path,
        },
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


def _restore(snapshot: str, state: ExecutionState) -> None:
    value = json.loads(snapshot)
    for key in (
        "data",
        "outputs",
        "variables",
        "context",
        "jump_to",
        "stop",
        "scope_exit",
        "transition_selected",
    ):
        setattr(state, key, value[key])
    state.variables["context"] = state.context
    state.task_visits = value.get("task_visits", {})
    state.execution_path = tuple(
        (ref, visit) for ref, visit in value.get("execution_path", state.execution_path)
    )
    if value["ended"] and not state.flow.ended.is_set():
        state.flow.output = value["end_output"]
        state.flow.context = value["end_context"]
        state.flow.owners = state.branch_owners
        state.flow.ended.set()


class LangGraphScopeExecutor:
    """Lower immutable scopes to native graphs without a public plan format."""

    def __init__(
        self, plan: WorkflowPlan, saver: Any, invocation_id: str, *, resume: bool = False
    ) -> None:
        self.plan = plan
        self.saver = saver
        self.invocation_id = invocation_id
        self.resume = resume
        self.scopes = {scope.reference: scope for scope in plan.scopes}
        self.counters: dict[str, int] = {}

    def checkpoint_config(self, reference: str, occurrence: int) -> dict[str, Any]:
        identity = json.dumps(
            [
                "owa/native-scopes/v1",
                self.invocation_id,
                self.plan.fingerprint,
                reference,
                occurrence,
            ]
        )
        key = hashlib.sha256(identity.encode()).hexdigest()
        return {
            "configurable": {"thread_id": "owa-scope-v1:" + key, "checkpoint_ns": ""},
            # The SDK default of 25 would incorrectly reject ordinary admitted
            # workflow cycles. This is a native execution guard, not a DSL limit.
            "recursion_limit": NATIVE_SCOPE_STEP_LIMIT,
        }

    async def original_input(self) -> Any:
        checkpoint = await self.saver.aget_tuple(self.checkpoint_config("/do", 1))
        if checkpoint is None:
            raise WorkflowExecutionError(
                "native task checkpoint is unavailable for this invocation"
            )
        channels = checkpoint.checkpoint["channel_values"]
        value = json.loads(channels["snapshot"])
        return copy.deepcopy(value["variables"]["workflow"]["input"])

    def _read(self, native: _GraphState, state: ExecutionState, reference: str) -> None:
        _restore(native["snapshot"], state)
        for key, value in json.loads(native["counters"]).items():
            # A completed branch cannot advance a sibling's occurrence cursor
            # during parent replay. The parent checkpoint owns those cursors.
            if key == reference or key.startswith(reference + "/"):
                self.counters[key] = max(self.counters.get(key, 0), value)

    def _write(self, state: ExecutionState, next_node: str = END) -> _GraphState:
        return {
            "snapshot": _snapshot(state),
            "counters": json.dumps(self.counters),
            "next_node": next_node,
        }

    def compile_scope(
        self, scope: ScopePlan, executor: WorkflowExecutor, prototype: ExecutionState
    ) -> Any:
        graph = StateGraph(_GraphState)
        names = {task.name: _node_name(task) for task in scope.tasks}

        def node(task: TaskPlan, following: str) -> Any:
            async def execute(native: _GraphState) -> _GraphState:
                state = executor._copy_state(prototype)
                self._read(native, state, scope.reference)
                if state.flow.ended.is_set():
                    state.stop = True
                    return self._write(state)
                executor._token(state).checkpoint()
                await executor._run_task_item(
                    task.name,
                    task.definition,
                    state,
                    task.reference,
                    execution_reference=task.execution_reference,
                )
                target = END if state.stop or state.scope_exit else following
                if state.jump_to is not None and not (state.stop or state.scope_exit):
                    target = names[state.jump_to]
                state.jump_to = None
                return self._write(state, target)

            return execute

        graph.add_edge(START, _node_name(scope.tasks[0]) if scope.tasks else END)
        for index, task in enumerate(scope.tasks):
            following = _node_name(scope.tasks[index + 1]) if index + 1 < len(scope.tasks) else END
            name = _node_name(task)
            graph.add_node(name, node(task, following))
            graph.add_conditional_edges(
                name, lambda state: state["next_node"], [*names.values(), END]
            )
        return graph.compile(checkpointer=self.saver)

    async def run_scope(
        self,
        executor: WorkflowExecutor,
        task_list: list[Any],
        state: ExecutionState,
        *,
        prefix: str,
        indexed: bool,
    ) -> None:
        try:
            await self._run_native_scope(executor, state, prefix=prefix, indexed=indexed)
        except OwaError:
            raise
        except Exception:
            raise WorkflowExecutionError(
                "native task execution failed", details={"reference": prefix}
            ) from None

    async def _run_native_scope(
        self, executor: WorkflowExecutor, state: ExecutionState, *, prefix: str, indexed: bool
    ) -> None:
        scope = self.scopes.get(prefix)
        if scope is None or scope.indexed != indexed:
            raise WorkflowExecutionError(
                "native scope is absent from the derived plan", details={"reference": prefix}
            )
        occurrence = self.counters.get(prefix, 0) + 1
        self.counters[prefix] = occurrence
        config = self.checkpoint_config(prefix, occurrence)
        graph = self.compile_scope(scope, executor, state)
        native_input: _GraphState | None = self._write(state)
        if prefix != "/do" or self.resume:
            saved = await graph.aget_state(config)
            if saved.values:
                if not saved.next:
                    self._read(cast(_GraphState, saved.values), state, prefix)
                    state.scope_exit = False
                    return
                native_input = None
        try:
            result = await graph.ainvoke(native_input, config=config, durability="sync")
        except Exception as exc:
            # A catch receives the last successful child task output, as it does
            # in the common interpreter. Failed-node partial mutations are local.
            try:
                saved = await graph.aget_state(config)
                if saved.values:
                    self._read(cast(_GraphState, saved.values), state, prefix)
            except Exception:
                pass  # Preserve the original common error if native storage is unavailable.
            if isinstance(exc, OwaError):
                raise
            raise WorkflowExecutionError(
                "native task execution failed", details={"reference": prefix}
            ) from None
        self._read(cast(_GraphState, result), state, prefix)
        state.scope_exit = False
