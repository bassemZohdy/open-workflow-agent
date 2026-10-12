"""Private native graphs: one node per admitted Open Workflow task."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import asdict
from typing import Any, TypedDict, cast

from langgraph.graph import END, START, StateGraph
from open_workflow_agent.effects import (
    EffectIdentity,
    EffectJournal,
    EffectReceipt,
    ReceiptDependency,
)
from open_workflow_agent.errors import InvocationStateError, OwaError, WorkflowExecutionError
from open_workflow_agent.expressions import validate_expression_data
from open_workflow_agent.timing import root_clock_identity
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
    receipt_bindings: str


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


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate native snapshot field")
        result[key] = value
    return result


def _native_json(value: Any) -> Any:
    if not isinstance(value, str):
        raise ValueError("native snapshot must be JSON text")
    decoded = json.loads(value, object_pairs_hook=_json_object)
    validate_expression_data(decoded)
    return decoded


def _json_identity(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def _decode_snapshot(snapshot: str) -> dict[str, Any]:
    value = _native_json(snapshot)
    fields = {
        "data",
        "outputs",
        "variables",
        "context",
        "jump_to",
        "stop",
        "scope_exit",
        "transition_selected",
        "ended",
        "end_output",
        "end_context",
        "task_visits",
        "execution_path",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("native snapshot fields are unavailable")
    if not isinstance(value["outputs"], dict) or not isinstance(value["variables"], dict):
        raise ValueError("native snapshot task dictionaries are invalid")
    if any(
        type(value[key]) is not bool
        for key in ("stop", "scope_exit", "transition_selected", "ended")
    ):
        raise ValueError("native snapshot control flags are invalid")
    if value["jump_to"] is not None and not isinstance(value["jump_to"], str):
        raise ValueError("native snapshot transition is invalid")
    visits, path = value["task_visits"], value["execution_path"]
    if not isinstance(visits, dict) or any(
        not key or type(visit) is not int or visit < 1 for key, visit in visits.items()
    ):
        raise ValueError("native snapshot task visits are invalid")
    if not isinstance(path, list) or any(
        not isinstance(item, list)
        or len(item) != 2
        or not isinstance(item[0], str)
        or not item[0]
        or type(item[1]) is not int
        or item[1] < 1
        for item in path
    ):
        raise ValueError("native snapshot execution path is invalid")
    return cast(dict[str, Any], value)


def _restore(
    snapshot: str, state: ExecutionState, *, decoded: dict[str, Any] | None = None
) -> None:
    # Validate the whole snapshot before changing any live execution state.
    value = _decode_snapshot(snapshot) if decoded is None else decoded
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
    state.task_visits = value["task_visits"]
    state.execution_path = tuple((ref, visit) for ref, visit in value["execution_path"])
    if value["ended"] and not state.flow.ended.is_set():
        state.flow.output = value["end_output"]
        state.flow.context = value["end_context"]
        state.flow.owners = state.branch_owners
        state.flow.ended.set()


class LangGraphScopeExecutor:
    """Lower immutable scopes to native graphs without a public plan format."""

    def __init__(
        self,
        plan: WorkflowPlan,
        saver: Any,
        invocation_id: str,
        *,
        resume: bool = False,
        effect_journal: EffectJournal | None = None,
    ) -> None:
        self.plan = plan
        self.saver = saver
        self.invocation_id = invocation_id
        self.resume = resume
        self.effect_journal = effect_journal
        self.receipts: dict[str, ReceiptDependency] = {}
        self.completed_scopes: dict[tuple[str, int], str] = {}
        self._required_scopes: dict[tuple[str, int], str] = {}
        self.scopes = {scope.reference: scope for scope in plan.scopes}
        self.counters: dict[str, int] = {}
        self.task_references = {
            task.execution_reference or task.reference
            for scope in plan.scopes
            for task in scope.tasks
        }
        source_references = {task.reference for scope in plan.scopes for task in scope.tasks}
        self.policy_references = {
            f"{reference}/{suffix}"
            for reference in self.task_references | source_references
            for suffix in ("@attempt", "@catch-attempt")
        }
        self.event_references = {
            f"{task.execution_reference or task.reference}/@event"
            for scope in plan.scopes
            for task in scope.tasks
            if "listen" in task.definition and isinstance(task.definition.get("foreach"), dict)
        }
        self._definition_json = _json_identity(plan.source)
        self._expected_input_json: str | None = None

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

    async def original_input(self, expected_input: Any) -> Any:
        try:
            validate_expression_data(expected_input)
            self._expected_input_json = _json_identity(expected_input)
            checkpoint = await self.saver.aget_tuple(self.checkpoint_config("/do", 1))
            if checkpoint is None:
                raise ValueError("checkpoint is absent")
            channels = checkpoint.checkpoint["channel_values"]
            value, _ = self._validate_native(channels, "/do", 1)
            stored_input = value["variables"]["workflow"]["input"]
            validate_expression_data(stored_input)
            if json.dumps(stored_input, sort_keys=True, allow_nan=False) != json.dumps(
                expected_input, sort_keys=True, allow_nan=False
            ):
                raise ValueError("original input disagrees")
            await self._validate_saved_scopes()
            return copy.deepcopy(stored_input)
        except Exception:
            raise InvocationStateError(
                "native execution state is unavailable or inconsistent; recovery is required"
            ) from None

    async def record_effect_receipt(self, identity: EffectIdentity, receipt: EffectReceipt) -> None:
        dependency = ReceiptDependency.capture(identity, receipt)
        existing = self.receipts.get(identity.key)
        if existing is not None and existing != dependency:
            raise InvocationStateError("native receipt dependency changed")
        self.receipts[identity.key] = dependency

    @staticmethod
    def _state_digest(native: dict[str, Any]) -> str:
        bindings = _native_json(native["receipt_bindings"])
        value = [
            _native_json(native["snapshot"]),
            _native_json(native["counters"]),
            native["next_node"],
            bindings["receipts"],
            bindings["scopes"],
        ]
        return hashlib.sha256(_json_identity(value).encode()).hexdigest()

    def _validate_receipts(
        self, native: dict[str, Any], counters: dict[str, int], reference: str, occurrence: int
    ) -> None:
        bindings = _native_json(native["receipt_bindings"])
        if (
            not isinstance(bindings, dict)
            or set(bindings) != {"version", "state_digest", "receipts", "scopes"}
            or type(bindings["version"]) is not int
            or bindings["version"] != 2
            or bindings["state_digest"] != self._state_digest(native)
            or not isinstance(bindings["receipts"], list)
        ):
            raise ValueError("native receipt bindings disagree with saved state")
        seen: set[str] = set()
        for value in bindings["receipts"]:
            dependency = ReceiptDependency.from_value(value)
            identity = dependency.identity
            if (
                identity.invocation_id != self.invocation_id
                or identity.workflow_fingerprint != self.plan.fingerprint
                or (
                    identity.task_reference not in self.task_references
                    and not (
                        self.plan.uses_timing
                        and identity
                        == root_clock_identity(self.invocation_id, self.plan.fingerprint)
                    )
                )
                or identity.key in seen
                or self.effect_journal is None
            ):
                raise ValueError("native receipt dependency identity is inconsistent")
            seen.add(identity.key)
            existing = self.receipts.get(identity.key)
            if existing is None:
                dependency.verify(self.effect_journal)
            elif existing != dependency:
                raise ValueError("native receipt dependency changed")
            self.receipts[identity.key] = dependency
        if not isinstance(bindings["scopes"], list):
            raise ValueError("native completed scope dependencies are invalid")
        seen_scopes: set[tuple[str, int]] = set()
        for item in bindings["scopes"]:
            if not isinstance(item, dict) or set(item) != {
                "reference",
                "occurrence",
                "state_digest",
            }:
                raise ValueError("native completed scope dependency fields are invalid")
            ref, visit, digest = item["reference"], item["occurrence"], item["state_digest"]
            if (
                not isinstance(ref, str)
                or ref not in self.scopes
                or type(visit) is not int
                or visit < 1
                or visit > counters.get(ref, 0)
                or (ref, visit) == (reference, occurrence)
                or (ref, visit) in seen_scopes
                or not isinstance(digest, str)
                or not re.fullmatch(r"[a-f0-9]{64}", digest)
            ):
                raise ValueError("native completed scope dependency identity is invalid")
            key = (ref, visit)
            seen_scopes.add(key)
            expected = self._required_scopes.get(key)
            if expected is not None and expected != digest:
                raise ValueError("native completed scope dependencies disagree")
            self._required_scopes[key] = digest
            self.completed_scopes[key] = digest

    def _validate_native(
        self, native: Any, reference: str, occurrence: int
    ) -> tuple[dict[str, Any], dict[str, int]]:
        try:
            if not isinstance(native, dict) or reference not in self.scopes:
                raise ValueError("native scope channels are invalid")
            value = _decode_snapshot(native["snapshot"])
            counters = _native_json(native["counters"])
            if (
                not isinstance(counters, dict)
                or any(
                    key not in self.scopes or type(count) is not int or count < 1
                    for key, count in counters.items()
                )
                or counters.get(reference) != occurrence
            ):
                raise ValueError("native scope occurrence is inconsistent")
            scope = self.scopes[reference]
            if native["next_node"] not in {END, *(_node_name(task) for task in scope.tasks)}:
                raise ValueError("native routing target is inconsistent")
            if (
                any(value[key] for key in ("stop", "scope_exit", "ended"))
                and native["next_node"] != END
            ):
                raise ValueError("native terminal controls disagree with routing")
            if value["jump_to"] is not None and value["jump_to"] not in {
                task.name for task in scope.tasks
            }:
                raise ValueError("native task transition is inconsistent")
            if set(value["task_visits"]) - self.task_references:
                raise ValueError("native task occurrence belongs to another plan")
            for coordinate, (task_reference, visit) in enumerate(value["execution_path"]):
                if task_reference in self.task_references:
                    if visit > value["task_visits"].get(task_reference, 0):
                        raise ValueError("native execution path is ahead of task visits")
                elif task_reference in self.event_references:
                    # An iterator ordinal must refer to an actual immutable
                    # selection before adopting native child execution state.
                    identity = EffectIdentity(
                        self.invocation_id,
                        self.plan.fingerprint,
                        task_reference.removesuffix("/@event"),
                        tuple(value["execution_path"][: coordinate + 1]),
                        "listen:event:v1",
                    )
                    receipt = (
                        self.effect_journal.get(identity)
                        if self.effect_journal is not None
                        else None
                    )
                    if receipt is None or receipt.status != "completed":
                        raise ValueError("native event iterator selection is missing")
                elif task_reference not in self.policy_references:
                    raise ValueError("native execution path belongs to another plan")
            variables = value["variables"]
            workflow = variables["workflow"]
            if not isinstance(workflow, dict) or workflow["id"] != self.invocation_id:
                raise ValueError("native workflow identity is inconsistent")
            if _json_identity(workflow["definition"]) != self._definition_json:
                raise ValueError("native workflow definition is inconsistent")
            if (
                self._expected_input_json is not None
                and _json_identity(workflow["input"]) != self._expected_input_json
            ):
                raise ValueError("native workflow input is inconsistent")
            if _json_identity(variables["context"]) != _json_identity(value["context"]):
                raise ValueError("native context binding is inconsistent")
            self._validate_receipts(native, counters, reference, occurrence)
            return value, cast(dict[str, int], counters)
        except Exception:
            raise InvocationStateError(
                "native execution state is unavailable or inconsistent; recovery is required"
            ) from None

    async def _validate_saved_scopes(self) -> None:
        # Read only this invocation's derived scope keys through the SDK. This
        # traverses stored occurrences, including a child still pending beneath
        # an older parent checkpoint. No checkpoint or occurrence is recreated.
        pending = {(reference, 1) for reference in self.scopes}
        inspected: set[tuple[str, int]] = set()
        observed: dict[tuple[str, int], tuple[str, str]] = {}
        while pending:
            reference, occurrence = pending.pop()
            inspected.add((reference, occurrence))
            checkpoint = await self.saver.aget_tuple(self.checkpoint_config(reference, occurrence))
            if checkpoint is None:
                continue
            channels = checkpoint.checkpoint["channel_values"]
            _, counters = self._validate_native(channels, reference, occurrence)
            observed[(reference, occurrence)] = (
                self._state_digest(channels),
                channels["next_node"],
            )
            candidates = {(reference, occurrence + 1), *counters.items(), *self._required_scopes}
            pending.update(candidates - inspected)
        for key, digest in self._required_scopes.items():
            if observed.get(key) != (digest, END):
                raise ValueError("native completed scope checkpoint is absent or inconsistent")

    def _read(self, native: _GraphState, state: ExecutionState, reference: str) -> None:
        decoded, counters = self._validate_native(native, reference, self.counters[reference])
        _restore(native["snapshot"], state, decoded=decoded)
        for key, value in counters.items():
            # A completed branch cannot advance a sibling's occurrence cursor
            # during parent replay. The parent checkpoint owns those cursors.
            if key == reference or key.startswith(reference + "/"):
                self.counters[key] = max(self.counters.get(key, 0), value)

    def _write(self, state: ExecutionState, next_node: str = END, *, reference: str) -> _GraphState:
        native: _GraphState = {
            "snapshot": _snapshot(state),
            "counters": json.dumps(self.counters),
            "next_node": next_node,
            "receipt_bindings": "",
        }
        bindings = {
            "version": 2,
            "state_digest": "",
            "receipts": [asdict(self.receipts[key]) for key in sorted(self.receipts)],
            "scopes": [
                {"reference": ref, "occurrence": visit, "state_digest": digest}
                for (ref, visit), digest in sorted(self.completed_scopes.items())
                if visit <= self.counters.get(ref, 0)
                and (ref, visit) != (reference, self.counters[reference])
            ],
        }
        native["receipt_bindings"] = json.dumps(bindings, allow_nan=False)
        bindings["state_digest"] = self._state_digest(dict(native))
        native["receipt_bindings"] = json.dumps(bindings, allow_nan=False)
        return native

    def _record_completed_scope(self, native: _GraphState, reference: str, occurrence: int) -> None:
        key = (reference, occurrence)
        digest = self._state_digest(dict(native))
        existing = self.completed_scopes.get(key)
        if existing is not None and existing != digest:
            raise InvocationStateError("native completed scope checkpoint changed")
        self.completed_scopes[key] = digest

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
                    return self._write(state, reference=scope.reference)
                executor._token(state).checkpoint()
                await executor._run_task_item(
                    task.name,
                    task.definition,
                    state,
                    task.reference,
                    execution_reference=task.execution_reference,
                )
                halted = state.stop or state.scope_exit or state.flow.ended.is_set()
                target = END if halted else following
                if state.jump_to is not None and not halted:
                    target = names[state.jump_to]
                state.jump_to = None
                return self._write(state, target, reference=scope.reference)

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
        native_input: _GraphState | None = self._write(state, reference=prefix)
        if prefix != "/do" or self.resume:
            saved = await graph.aget_state(config)
            if saved.values:
                if not saved.next:
                    self._read(cast(_GraphState, saved.values), state, prefix)
                    self._record_completed_scope(
                        cast(_GraphState, saved.values), prefix, occurrence
                    )
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
        self._record_completed_scope(cast(_GraphState, result), prefix, occurrence)
        state.scope_exit = False
