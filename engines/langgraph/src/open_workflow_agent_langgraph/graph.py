"""Private native graphs: one node per admitted Open Workflow task."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import asdict, replace
from importlib import import_module
from typing import Annotated, Any, TypedDict, cast

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from open_workflow_agent.child_rescan import child_rescan_wake, verify_child_rescan_wake
from open_workflow_agent.child_suspension import ChildSuspension
from open_workflow_agent.effects import (
    EffectIdentity,
    EffectJournal,
    EffectReceipt,
    ReceiptDependency,
)
from open_workflow_agent.errors import InvocationStateError, OwaError, WorkflowExecutionError
from open_workflow_agent.execution_authorization import AUTHORITY_OPERATION
from open_workflow_agent.expressions import (
    ExpressionEvaluator,
    ExpressionMode,
    validate_expression_data,
)
from open_workflow_agent.native_extensions import (
    LANGGRAPH_COMMAND,
    MAX_CHANNEL_VALUE_BYTES,
    MAX_CHANNEL_VALUE_TOTAL_BYTES,
    NATIVE_CHANNEL_VARIABLE,
    NativeChannelConfig,
)
from open_workflow_agent.timers import TimerSuspension
from open_workflow_agent.timing import root_clock_identity
from open_workflow_agent.workflow import (
    ExecutionState,
    ScopePlan,
    TaskPlan,
    WorkflowExecutor,
    WorkflowPlan,
)

from .native import NATIVE_SCOPE_STEP_LIMIT

_NATIVE_CHANNEL_UPDATES = "__owa_native_channel_updates__"


def _reduce_native_channels(
    current: dict[str, Any] | None, update: dict[str, Any]
) -> dict[str, Any]:
    """LangGraph reducer for admitted per-channel OWA reducer operations."""
    values = copy.deepcopy(current or {})
    if set(update) != {_NATIVE_CHANNEL_UPDATES}:
        values.update(copy.deepcopy(update))
        return values
    operations = update[_NATIVE_CHANNEL_UPDATES]
    if not isinstance(operations, list):
        raise ValueError("native channel updates are invalid")
    for operation in operations:
        if not isinstance(operation, dict) or set(operation) != {"id", "reducer", "value"}:
            raise ValueError("native channel update is invalid")
        channel_id, reducer, value = operation["id"], operation["reducer"], operation["value"]
        if reducer == "append":
            if not isinstance(values.get(channel_id), list) or not isinstance(value, list):
                raise ValueError("native append channel update is invalid")
            values[channel_id] = [*values[channel_id], *copy.deepcopy(value)]
        elif reducer == "last_value":
            values[channel_id] = copy.deepcopy(value)
        else:
            raise ValueError("native channel reducer is invalid")
    return values


class _GraphState(TypedDict):
    snapshot: str
    counters: str
    next_node: str
    receipt_bindings: str


class _GraphStateWithNativeChannels(TypedDict):
    # Repeat the base fields instead of inheriting `_GraphState`: LangGraph's
    # schema reader must see the Annotated reducer and all state keys together
    # on the concrete schema passed to StateGraph.
    snapshot: str
    counters: str
    next_node: str
    receipt_bindings: str
    native_channels: Annotated[dict[str, Any], _reduce_native_channels]


def _node_name(task: TaskPlan) -> str:
    return "task_" + hashlib.sha256(task.reference.encode()).hexdigest()


def _snapshot(state: ExecutionState) -> str:
    # Live controls, services, expression evaluators and asyncio tasks never enter
    # native channels. JSON text also avoids serializer-specific integer limits
    # for inert document metadata admitted by the official schema.
    value = {
        "data": state.data,
        "outputs": state.outputs,
        "variables": {
            name: value
            for name, value in state.variables.items()
            if name != NATIVE_CHANNEL_VARIABLE
        },
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
    }
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
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
    if not isinstance(value, dict) or frozenset(value) not in {
        frozenset(fields),
    }:
        raise ValueError("native snapshot fields are unavailable")
    if not isinstance(value["outputs"], dict) or not isinstance(value["variables"], dict):
        raise ValueError("native snapshot task dictionaries are invalid")
    if NATIVE_CHANNEL_VARIABLE in value["variables"]:
        raise ValueError("native snapshot contains a transient channel binding")
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
    # A nested graph can execute inside an outer task's live read binding.
    # Restore that lexical binding from the caller, never from persisted data.
    channel_binding = (
        {NATIVE_CHANNEL_VARIABLE: state.variables[NATIVE_CHANNEL_VARIABLE]}
        if NATIVE_CHANNEL_VARIABLE in state.variables
        else {}
    )
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
    state.variables.update(channel_binding)
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
        timer_wake: dict[str, Any] | None = None,
        timer_wakes: dict[str, dict[str, Any]] | None = None,
        child_wakes: dict[str, dict[str, Any]] | None = None,
        child_waits: Any = None,
    ) -> None:
        self.plan = plan
        self.saver = saver
        self.invocation_id = invocation_id
        self.resume = resume
        self.timer_wake = timer_wake
        self.timer_wakes = timer_wakes
        self.child_wakes = child_wakes
        self.child_waits = child_waits
        if child_wakes is not None:
            validate_expression_data(child_wakes)
            if not isinstance(child_wakes, dict) or any(
                not isinstance(key, str)
                or not isinstance(value, dict)
                or set(value)
                != {"child_key", "child_id", "request_digest", "deadline_at", "reason"}
                or value["child_key"] != key
                or not isinstance(value["reason"], str)
                or value["reason"] not in {"child_terminal", "deadline"}
                for key, value in child_wakes.items()
            ):
                raise InvocationStateError("native child wake map is inconsistent")
        if timer_wakes is not None:
            validate_expression_data(timer_wakes)
            if not isinstance(timer_wakes, dict) or any(
                not isinstance(key, str)
                or not isinstance(value, dict)
                or set(value) != {"timer_key", "due_at", "reason"}
                or value["timer_key"] != key
                or type(value["due_at"]) not in {int, float}
                or value["due_at"] <= 0
                or not isinstance(value["reason"], str)
                or value["reason"] not in {"timer_due", "resume_requested"}
                for key, value in timer_wakes.items()
            ):
                raise InvocationStateError("native timer wake map is inconsistent")
        self.effect_journal = effect_journal
        self.receipts: dict[str, ReceiptDependency] = {}
        self.completed_scopes: dict[tuple[str, int], str] = {}
        self._required_scopes: dict[tuple[str, int], str] = {}
        self._resume_interrupts: dict[tuple[str, int, str], tuple[Any, list[Any]]] = {}
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
        self.native_channels: NativeChannelConfig | None = plan.native_channels
        self.native_commands = any(
            item.required and item.capability_id == LANGGRAPH_COMMAND
            for item in plan.native_extensions
        )
        self._channels_by_id = (
            {channel.channel_id: channel for channel in plan.native_channels.channels}
            if plan.native_channels is not None
            else {}
        )
        self._channel_writes = (
            {
                writer: tuple(
                    channel
                    for channel in plan.native_channels.channels
                    if writer in channel.writers
                )
                for writer in {
                    task for channel in plan.native_channels.channels for task in channel.writers
                }
            }
            if plan.native_channels is not None
            else {}
        )
        self._channel_reads = (
            {item.task: item for item in plan.native_channels.reads}
            if plan.native_channels is not None
            else {}
        )

    def _initial_channel_values(self) -> dict[str, Any] | None:
        if self.native_channels is None:
            return None
        return {channel.channel_id: channel.initial for channel in self.native_channels.channels}

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

    def _original_authority_identity(self) -> EffectIdentity | None:
        """Verify the one immutable origin receipt, never a generic scope policy."""
        if self.effect_journal is None:
            return None
        identity = EffectIdentity(
            self.invocation_id,
            self.plan.fingerprint,
            "/do",
            (("/do", 1),),
            AUTHORITY_OPERATION,
        )
        receipt = self.effect_journal.get(identity)
        if receipt is None:
            return None
        invocations = self.effect_journal._invocations
        if invocations is None or receipt.status != "completed":
            raise ValueError("original authority receipt is unavailable")
        # Journal reads already verify the independent original authority row,
        # archive, exact receipt and index; do not repeat that physical proof.
        return identity

    def _is_original_authority(self, identity: EffectIdentity) -> bool:
        expected = EffectIdentity(
            self.invocation_id,
            self.plan.fingerprint,
            "/do",
            (("/do", 1),),
            AUTHORITY_OPERATION,
        )
        return identity == expected and self._original_authority_identity() == expected

    def validate_initial_projection(self, checkpoint: Any, expected_input: Any) -> None:
        """Authenticate only the SDK's unexecuted root initializer projection."""
        try:
            raw = checkpoint.checkpoint
            channels = raw["channel_values"]
            if (
                checkpoint.metadata.get("source") != "input"
                or type(checkpoint.metadata.get("step")) is not int
                or checkpoint.metadata["step"] != -1
                or checkpoint.metadata.get("parents") != {}
                or set(channels) != {START}
                or raw.get("versions_seen") != {"__input__": {}}
                or set(raw["channel_versions"]) != {START}
                or type(raw.get("v")) is not int
                or raw["v"] != 4
            ):
                raise ValueError("initial SDK checkpoint structure changed")
            version = raw["channel_versions"][START]
            if not isinstance(version, str):
                raise ValueError("initial SDK channel format changed")
            sequence, separator, random_suffix = version.partition(".")
            fraction = float(random_suffix)
            # Reviewed memory, SQLite and PostgreSQL savers use this exact
            # random.random() formatting, including small scientific fractions.
            if not (
                sequence == f"{1:032}"
                and separator == "."
                and 0 <= fraction < 1
                and random_suffix == f"{fraction:016}"
            ):
                raise ValueError("initial SDK channel has already advanced")
            initial = channels[START]
            expected_initial_fields = set(_GraphState.__annotations__)
            if self.native_channels is not None:
                expected_initial_fields.add("native_channels")
            if not isinstance(initial, dict) or set(initial) != expected_initial_fields:
                raise ValueError("initial native fields changed")
            self._expected_input_json = _json_identity(expected_input)
            value, counters = self._validate_native(initial, "/do", 1)
            if self.native_channels is not None and _json_identity(
                initial.get("native_channels")
            ) != _json_identity(self._initial_channel_values()):
                raise ValueError("initial native channels differ from the admitted workflow")
            if (
                counters != {"/do": 1}
                or initial["next_node"] != END
                or value["outputs"]
                or value["task_visits"]
                or value["execution_path"]
                or any(value[key] for key in ("stop", "scope_exit", "transition_selected", "ended"))
                or any(value[key] is not None for key in ("jump_to", "end_output", "end_context"))
            ):
                raise ValueError("initial native task state has already advanced")
            data = expected_input
            input_spec = self.plan.execution_source.get("input")
            if isinstance(input_spec, dict) and "from" in input_spec:
                data = ExpressionEvaluator(
                    mode=cast(
                        ExpressionMode,
                        self.plan.execution_source.get("evaluate", {}).get("mode", "strict"),
                    )
                ).evaluate(
                    input_spec["from"],
                    expected_input,
                    variables={
                        key: item for key, item in value["variables"].items() if key != "context"
                    },
                )
            if _json_identity(value["data"]) != _json_identity(data) or _json_identity(
                value["context"]
            ) != _json_identity(data):
                raise ValueError("initial native input projection changed")
            bindings = _native_json(initial["receipt_bindings"])
            expected_receipts = (
                {root_clock_identity(self.invocation_id, self.plan.fingerprint).key}
                if self.plan.uses_timing
                else set()
            )
            original_authority = self._original_authority_identity()
            if original_authority is not None:
                expected_receipts.add(original_authority.key)
            if bindings["scopes"] or set(self.receipts) != expected_receipts:
                raise ValueError("initial native dependencies contain task progress")
            if self.effect_journal is not None:
                indexed = self.effect_journal.connection.execute(
                    "SELECT payload FROM workflow_effect_invocations WHERE invocation_id=? LIMIT 5",
                    (self.invocation_id,),
                ).fetchall()
                if len(indexed) > 3 + (original_authority is not None):
                    raise ValueError("initial invocation already has action evidence")
                admissions = {
                    root_clock_identity(self.invocation_id, self.plan.fingerprint),
                    EffectIdentity(
                        self.invocation_id,
                        self.plan.fingerprint,
                        "/do",
                        (("/do", 1),),
                        "policy:definition-admission:v1",
                    ),
                    EffectIdentity(
                        self.invocation_id,
                        self.plan.fingerprint,
                        "/",
                        (("/", 1),),
                        "policy:initial-delivery:v1",
                    ),
                }
                if original_authority is not None:
                    admissions.add(original_authority)
                for row in indexed:
                    identity = EffectIdentity.from_value(_native_json(row[0]))
                    if identity not in admissions:
                        raise ValueError("initial invocation already has action evidence")
                    receipt = self.effect_journal.get(identity)
                    if receipt is None or receipt.status != "completed":
                        raise ValueError("initial invocation proof is incomplete")
            writes = checkpoint.pending_writes or []
            if not writes:
                return
            root = self.scopes["/do"]
            if not root.tasks:
                raise ValueError("initial SDK route has no admitted task")
            sdk = import_module("langgraph.pregel._algo")
            starter = sdk._xxhash_str(
                bytes.fromhex(raw["id"].replace("-", "")), START, "0", START, "__pregel_pull", START
            )
            expected = {**initial, "branch:to:" + _node_name(root.tasks[0]): None}
            observed: dict[str, Any] = {}
            for task_id, channel, item in writes:
                if task_id != starter or channel not in expected or channel in observed:
                    raise ValueError("initial SDK pending write identity changed")
                observed[channel] = item
            if _json_identity(observed) != _json_identity(expected):
                raise ValueError("initial SDK pending projection changed")
        except Exception:
            raise InvocationStateError(
                "native initial projection is unavailable or inconsistent"
            ) from None

    async def record_effect_receipt(self, identity: EffectIdentity, receipt: EffectReceipt) -> None:
        dependency = ReceiptDependency.capture(identity, receipt)
        existing = self.receipts.get(identity.key)
        if existing is not None and existing != dependency:
            raise InvocationStateError("native receipt dependency changed")
        self.receipts[identity.key] = dependency

    def _timer_interrupt(self, value: Any) -> TimerSuspension:
        """Reconcile SDK interruption evidence without adopting checkpoint state."""
        if not isinstance(value, dict) or set(value) != {"timer", "receipts"}:
            raise InvocationStateError("native timer interruption is inconsistent")
        suspension = TimerSuspension.from_value(value["timer"])
        identity = suspension.identity
        if (
            identity.invocation_id != self.invocation_id
            or identity.workflow_fingerprint != self.plan.fingerprint
            or identity.task_reference not in self.task_references
            or not isinstance(value["receipts"], list)
            or self.effect_journal is None
        ):
            raise InvocationStateError("native timer identity is inconsistent")
        seen: set[str] = set()
        for item in value["receipts"]:
            dependency = ReceiptDependency.from_value(item)
            reference = dependency.identity
            if (
                reference.invocation_id != self.invocation_id
                or reference.workflow_fingerprint != self.plan.fingerprint
                or (
                    reference.task_reference not in self.task_references
                    and reference != root_clock_identity(self.invocation_id, self.plan.fingerprint)
                    and not self._is_original_authority(reference)
                )
                or reference.key in seen
            ):
                raise InvocationStateError("native timer receipt identity is inconsistent")
            seen.add(reference.key)
            dependency.verify(self.effect_journal)
            previous = self.receipts.get(reference.key)
            if previous is not None and previous != dependency:
                raise InvocationStateError("native timer receipt dependency changed")
            self.receipts[reference.key] = dependency
        armed = self.effect_journal.get(identity)
        if (
            identity.key not in seen
            or armed is None
            or armed.status != "completed"
            or not isinstance(armed.output, dict)
            or armed.output.get("due_at") != suspension.due_at
        ):
            raise InvocationStateError("native timer arming evidence is inconsistent")
        return suspension

    def _validate_wake(self, suspension: TimerSuspension) -> dict[str, Any]:
        if self.timer_wakes is not None:
            wake = self.timer_wakes.get(suspension.timer_key)
            if wake is None:
                raise suspension
        else:
            wake = self.timer_wake
        if (
            not isinstance(wake, dict)
            or set(wake) != {"timer_key", "due_at", "reason"}
            or wake["timer_key"] != suspension.timer_key
            or type(wake["due_at"]) not in {int, float}
            or wake["due_at"] != suspension.due_at
            or not isinstance(wake["reason"], str)
            or wake["reason"] not in {"timer_due", "resume_requested"}
        ):
            raise InvocationStateError("native timer wake does not match pending interruption")
        return dict(wake)

    def _child_interrupt(self, value: Any) -> ChildSuspension:
        """Admit immutable common child evidence before adopting native dependencies."""
        if not isinstance(value, dict) or set(value) != {"child", "receipts"}:
            raise InvocationStateError("native child interruption is inconsistent")
        try:
            suspension = ChildSuspension.from_value(value["child"])
            identity = suspension.identity
            if (
                identity.invocation_id != self.invocation_id
                or identity.workflow_fingerprint != self.plan.fingerprint
                or identity.task_reference not in self.task_references
                or not isinstance(value["receipts"], list)
                or self.effect_journal is None
                or self.child_waits is None
            ):
                raise ValueError("child identity is inconsistent")
            registration = self.child_waits.registration(identity)
            admission = replace(identity, operation="policy:child-await:v1")
            if (
                registration is None
                or registration.identity != identity
                or registration.suspension.as_value() != suspension.as_value()
                or registration.receipt.status != "completed"
                or self.effect_journal.get(admission) != registration.receipt
            ):
                raise ValueError("child registration is inconsistent")
            dependencies: dict[str, ReceiptDependency] = {}
            for item in value["receipts"]:
                dependency = ReceiptDependency.from_value(item)
                reference = dependency.identity
                if (
                    reference.invocation_id != self.invocation_id
                    or reference.workflow_fingerprint != self.plan.fingerprint
                    or (
                        reference.task_reference not in self.task_references
                        and reference
                        != root_clock_identity(self.invocation_id, self.plan.fingerprint)
                        and not self._is_original_authority(reference)
                    )
                    or reference.key in dependencies
                ):
                    raise ValueError("child receipt identity is inconsistent")
                dependency.verify(self.effect_journal)
                previous = self.receipts.get(reference.key)
                if previous is not None and previous != dependency:
                    raise ValueError("child receipt dependency changed")
                dependencies[reference.key] = dependency
            if dependencies.get(admission.key) != ReceiptDependency.capture(
                admission, registration.receipt
            ):
                raise ValueError("child admission receipt is absent")
        except Exception:
            raise InvocationStateError(
                "native child interruption evidence is inconsistent"
            ) from None
        self.receipts.update(dependencies)
        return suspension

    def _native_interrupt(self, value: Any) -> TimerSuspension | ChildSuspension:
        if isinstance(value, dict) and "child" in value:
            return self._child_interrupt(value)
        return self._timer_interrupt(value)

    def _validate_native_wake(
        self,
        suspension: TimerSuspension | ChildSuspension,
        *,
        fork_reference: str | None = None,
    ) -> dict[str, Any]:
        if isinstance(suspension, TimerSuspension):
            return self._validate_wake(suspension)
        wake = (self.child_wakes or {}).get(suspension.identity.key)
        if wake is None:
            rescan = (
                child_rescan_wake(
                    suspension,
                    child_waits=self.child_waits,
                    journal=self.effect_journal,
                    dependencies=self.receipts,
                    ready_wakes=self.child_wakes,
                    invocation_id=self.invocation_id,
                    workflow_fingerprint=self.plan.fingerprint,
                    fork_reference=fork_reference,
                )
                if fork_reference is not None and self.effect_journal is not None
                else None
            )
            if rescan is None:
                raise suspension
            return rescan
        if (
            set(wake) != {"child_key", "child_id", "request_digest", "deadline_at", "reason"}
            or wake["child_key"] != suspension.identity.key
            or wake["child_id"] != suspension.child_id
            or wake["request_digest"] != suspension.request_digest
            or type(wake["deadline_at"]) not in {int, float, type(None)}
            or wake["deadline_at"] != suspension.deadline_at
            or wake["reason"] not in {"child_terminal", "deadline"}
            or (wake["reason"] == "deadline" and suspension.deadline_at is None)
        ):
            raise InvocationStateError("native child wake does not match pending interruption")
        try:
            known = self.child_waits.known_wakes(self.invocation_id, self.plan.fingerprint)
            if _json_identity(known.get(suspension.identity.key)) != _json_identity(wake):
                raise ValueError("child wake is not recorded")
        except Exception:
            raise InvocationStateError(
                "native child wake has no verified common decision"
            ) from None
        return dict(wake)

    def _verify_child_rescan(self, value: Any) -> dict[str, Any]:
        try:
            if self.effect_journal is None:
                raise ValueError("native child rescan journal is absent")
            suspension = self._child_interrupt(
                {
                    "child": value["pending"],
                    "receipts": json.loads(
                        json.dumps(
                            [asdict(item) for item in self.receipts.values()], allow_nan=False
                        )
                    ),
                }
            )
            return verify_child_rescan_wake(
                value,
                suspension,
                child_waits=self.child_waits,
                journal=self.effect_journal,
                dependencies=self.receipts,
                invocation_id=self.invocation_id,
                workflow_fingerprint=self.plan.fingerprint,
            )
        except Exception:
            raise InvocationStateError(
                "native child rescan has no verified sibling decision"
            ) from None

    def _known_native_resume(self, value: Any) -> dict[str, Any]:
        if isinstance(value, dict) and value.get("profile") == "owa/child-rescan/v1":
            return self._verify_child_rescan(value)
        if not isinstance(value, dict) or "child_key" not in value:
            return self._known_resume(value)
        dependency = next(
            (
                item
                for item in self.receipts.values()
                if item.identity.operation == "policy:child-await:v1"
                and replace(item.identity, operation="run:workflow").key == value["child_key"]
            ),
            None,
        )
        if dependency is None or self.child_waits is None:
            raise InvocationStateError("native child resume admission reference is absent")
        identity = replace(dependency.identity, operation="run:workflow")
        registration = self.child_waits.registration(identity)
        if registration is None:
            raise InvocationStateError("native child resume registration is absent")
        self._child_interrupt(
            {
                "child": registration.suspension.as_value(),
                "receipts": json.loads(json.dumps([asdict(dependency)], allow_nan=False)),
            }
        )
        try:
            expected = self._validate_native_wake(registration.suspension)
        except ChildSuspension:
            raise InvocationStateError(
                "native child resume history has no authorized wake"
            ) from None
        if _json_identity(value) != _json_identity(expected):
            raise InvocationStateError("native child resume history changed")
        return expected

    def _known_resume(self, value: Any) -> dict[str, Any]:
        if (
            not isinstance(value, dict)
            or set(value) != {"timer_key", "due_at", "reason"}
            or not isinstance(value["timer_key"], str)
        ):
            raise InvocationStateError("native timer resume history is inconsistent")
        dependency = self.receipts.get(value["timer_key"])
        if dependency is None or dependency.identity.operation != "wait:arm:v1":
            raise InvocationStateError("native timer resume arming reference is absent")
        suspension = TimerSuspension(dependency.identity, value["due_at"])
        try:
            expected = self._validate_wake(suspension)
        except TimerSuspension:
            raise InvocationStateError(
                "native timer resume history has no authorized wake"
            ) from None
        if _json_identity(value) != _json_identity(expected):
            raise InvocationStateError("native timer resume history changed")
        return expected

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
        if "native_channels" in native:
            value.insert(3, native["native_channels"])
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
                    and not self._is_original_authority(identity)
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
            channel_values = native.get("native_channels", {})
            expected_channel_ids = set(self._channels_by_id) if reference == "/do" else set()
            if (
                not isinstance(channel_values, dict)
                or set(channel_values) != expected_channel_ids
                or (self.native_channels is not None and "native_channels" not in native)
            ):
                raise ValueError("native channel state does not match the workflow")
            self._validate_channel_values(channel_values)
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

    def _validate_channel_values(self, channel_values: dict[str, Any]) -> None:
        """Enforce deterministic finite channel/checkpoint payloads."""
        total_bytes = 0
        for channel_id, channel_value in channel_values.items():
            validate_expression_data(channel_value)
            channel = self._channels_by_id[channel_id]
            if channel.reducer == "append" and not isinstance(channel_value, list):
                raise ValueError("native append channel state is invalid")
            encoded = json.dumps(
                channel_value,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            if len(encoded) > MAX_CHANNEL_VALUE_BYTES:
                raise ValueError("native channel value exceeds its size limit")
            total_bytes += len(encoded)
            if total_bytes > MAX_CHANNEL_VALUE_TOTAL_BYTES:
                raise ValueError("native channel state exceeds its size limit")

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
            for _task_id, channel, value in checkpoint.pending_writes or ():
                if channel == "__interrupt__":
                    for pending_interrupt in value:
                        self._native_interrupt(pending_interrupt.value)
            observed[(reference, occurrence)] = (
                self._state_digest(channels),
                channels["next_node"],
            )
            candidates = {(reference, occurrence + 1), *counters.items(), *self._required_scopes}
            pending.update(candidates - inspected)
        for key, digest in self._required_scopes.items():
            if observed.get(key) != (digest, END):
                raise ValueError("native completed scope checkpoint is absent or inconsistent")

    def _read(self, native: _GraphState, state: ExecutionState, reference: str) -> dict[str, Any]:
        decoded, counters = self._validate_native(native, reference, self.counters[reference])
        _restore(native["snapshot"], state, decoded=decoded)
        for key, value in counters.items():
            # A completed branch cannot advance a sibling's occurrence cursor
            # during parent replay. The parent checkpoint owns those cursors.
            if key == reference or key.startswith(reference + "/"):
                self.counters[key] = max(self.counters.get(key, 0), value)
        channel_values = native.get("native_channels")
        return copy.deepcopy(channel_values) if isinstance(channel_values, dict) else {}

    def _write(
        self,
        state: ExecutionState,
        next_node: str = END,
        *,
        reference: str,
        native_channels: dict[str, Any] | None = None,
        native_channel_updates: list[dict[str, Any]] | None = None,
    ) -> _GraphState:
        expected_channel_ids = set(self._channels_by_id) if reference == "/do" else set()
        projected_channels = native_channels if native_channels is not None else {}
        if set(projected_channels) != expected_channel_ids:
            raise WorkflowExecutionError(
                "native channel output is invalid or exceeds its size limit"
            )
        if projected_channels:
            try:
                self._validate_channel_values(projected_channels)
            except Exception:
                raise WorkflowExecutionError(
                    "native channel output is invalid or exceeds its size limit"
                ) from None
        stored_channels = (
            {_NATIVE_CHANNEL_UPDATES: native_channel_updates}
            if native_channel_updates is not None
            else projected_channels
        )
        native: dict[str, Any] = {
            "snapshot": _snapshot(state),
            "counters": json.dumps(self.counters),
            "next_node": next_node,
            "receipt_bindings": "",
        }
        if self.native_channels is not None:
            native["native_channels"] = stored_channels
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
        digest_native = dict(native)
        if self.native_channels is not None:
            digest_native["native_channels"] = projected_channels
        bindings["state_digest"] = self._state_digest(digest_native)
        native["receipt_bindings"] = json.dumps(bindings, allow_nan=False)
        return cast(_GraphState, native)

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
        state_schema = (
            _GraphStateWithNativeChannels if self.native_channels is not None else _GraphState
        )
        graph = StateGraph(state_schema)
        names = {task.name: _node_name(task) for task in scope.tasks}

        def routed(update: _GraphState) -> _GraphState | Command[str]:
            if self.native_commands:
                return Command(update=update, goto=update["next_node"])
            return update

        def node(task: TaskPlan, following: str) -> Any:
            async def execute(native: _GraphState) -> _GraphState | Command[str]:
                state = executor._copy_state(prototype)
                channel_values = self._read(native, state, scope.reference)
                if state.flow.ended.is_set():
                    state.stop = True
                    return routed(
                        self._write(
                            state,
                            reference=scope.reference,
                            native_channels=channel_values,
                            native_channel_updates=(
                                []
                                if self.native_channels is not None and scope.reference == "/do"
                                else None
                            ),
                        )
                    )
                executor._token(state).checkpoint()
                pending = self._resume_interrupts.get(
                    (scope.reference, self.counters[scope.reference], _node_name(task))
                )
                if pending is not None:
                    payload, previous_resumes = pending
                    # SDK dynamic interrupts match by call index. Reproduce the
                    # acknowledged prefix before common replay can suspend for a
                    # different timer in this same native node.
                    for previous in previous_resumes:
                        expected = self._known_native_resume(previous)
                        marker = (
                            {"profile": "owa/child-rescan/v1"}
                            if expected.get("profile") == "owa/child-rescan/v1"
                            else (
                                {"child_key": expected["child_key"]}
                                if "child_key" in expected
                                else {"timer_key": expected["timer_key"]}
                            )
                        )
                        if _json_identity(interrupt(marker)) != (_json_identity(expected)):
                            raise InvocationStateError("native interruption resume history changed")
                    suspension = self._native_interrupt(payload)
                    if _json_identity(interrupt(payload)) != _json_identity(
                        self._validate_native_wake(
                            suspension,
                            fork_reference=task.execution_reference or task.reference,
                        )
                    ):
                        raise InvocationStateError(
                            "native interruption resume does not match its wake"
                        )
                read = self._channel_reads.get(task.reference)
                if read is not None:
                    if NATIVE_CHANNEL_VARIABLE in state.variables:
                        raise InvocationStateError("native channel variable collided at runtime")
                    state.variables[NATIVE_CHANNEL_VARIABLE] = {
                        channel_id: copy.deepcopy(channel_values[channel_id])
                        for channel_id in read.channels
                    }
                try:
                    try:
                        await executor._run_task_item(
                            task.name,
                            task.definition,
                            state,
                            task.reference,
                            execution_reference=task.execution_reference,
                        )
                    except (TimerSuspension, ChildSuspension) as suspension:
                        kind = "child" if isinstance(suspension, ChildSuspension) else "timer"
                        payload = {
                            kind: suspension.as_value(),
                            "receipts": json.loads(
                                json.dumps(
                                    [asdict(self.receipts[key]) for key in sorted(self.receipts)],
                                    allow_nan=False,
                                )
                            ),
                        }
                        self._native_interrupt(payload)
                        interrupt(payload)
                        # A validated wake must make the common timer consumable.
                        # Never treat an unconsumed suspension as a task result.
                        raise InvocationStateError(
                            "native interruption wake did not complete its wait"
                        ) from None
                finally:
                    if read is not None:
                        state.variables.pop(NATIVE_CHANNEL_VARIABLE, None)

                channel_updates: list[dict[str, Any]] = []
                for channel in self._channel_writes.get(task.reference, ()):
                    value = copy.deepcopy(state.data)
                    try:
                        validate_expression_data(value)
                    except Exception:
                        raise WorkflowExecutionError(
                            "native channel output is invalid or exceeds its size limit",
                            details={"task": task.reference},
                        ) from None
                    if channel.reducer == "append":
                        if not isinstance(value, list):
                            raise WorkflowExecutionError(
                                "native channel output is invalid or exceeds its size limit",
                                details={"task": task.reference},
                            )
                        channel_values[channel.channel_id] = [
                            *channel_values[channel.channel_id],
                            *value,
                        ]
                    else:
                        channel_values[channel.channel_id] = value
                    channel_updates.append(
                        {
                            "id": channel.channel_id,
                            "reducer": channel.reducer,
                            "value": value,
                        }
                    )
                halted = state.stop or state.scope_exit or state.flow.ended.is_set()
                target = END if halted else following
                if state.jump_to is not None and not halted:
                    target = names[state.jump_to]
                state.jump_to = None
                return routed(
                    self._write(
                        state,
                        target,
                        reference=scope.reference,
                        native_channels=channel_values,
                        native_channel_updates=(
                            channel_updates
                            if self.native_channels is not None and scope.reference == "/do"
                            else None
                        ),
                    )
                )

            return execute

        graph.add_edge(START, _node_name(scope.tasks[0]) if scope.tasks else END)
        for index, task in enumerate(scope.tasks):
            following = _node_name(scope.tasks[index + 1]) if index + 1 < len(scope.tasks) else END
            name = _node_name(task)
            graph.add_node(
                name,
                node(task, following),
                input_schema=state_schema,
                destinations=(*names.values(), END) if self.native_commands else None,
            )
            if not self.native_commands:
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
        native_input: _GraphState | None = self._write(
            state,
            reference=prefix,
            native_channels=self._initial_channel_values() if prefix == "/do" else None,
        )
        if prefix != "/do" or self.resume:
            saved = await graph.aget_state(config)
            if saved.values:
                if not saved.next and not saved.interrupts:
                    self._read(cast(_GraphState, saved.values), state, prefix)
                    self._record_completed_scope(
                        cast(_GraphState, saved.values), prefix, occurrence
                    )
                    state.scope_exit = False
                    return
                native_input = None
                if saved.interrupts:
                    if len(saved.interrupts) != 1:
                        raise InvocationStateError("native scope has ambiguous interruptions")
                    suspension = self._native_interrupt(saved.interrupts[0].value)
                    pending_tasks = [task for task in saved.tasks if task.interrupts]
                    if len(pending_tasks) != 1:
                        raise InvocationStateError("native pending timer task is ambiguous")
                    pending_task = pending_tasks[0]
                    if pending_task.name not in {_node_name(task) for task in scope.tasks}:
                        raise InvocationStateError(
                            "native pending timer task belongs to another scope"
                        )
                    pending_plan = next(
                        task for task in scope.tasks if _node_name(task) == pending_task.name
                    )
                    native_input = Command(
                        resume=self._validate_native_wake(
                            suspension,
                            fork_reference=pending_plan.execution_reference
                            or pending_plan.reference,
                        )
                    )
                    checkpoint = await self.saver.aget_tuple(config)
                    if checkpoint is None:
                        raise InvocationStateError("native pending timer checkpoint is absent")
                    previous_resumes: list[Any] = []
                    for task_id, channel, value in checkpoint.pending_writes or ():
                        if task_id == pending_task.id and channel == "__resume__":
                            previous_resumes = value if isinstance(value, list) else [value]
                    for previous in previous_resumes:
                        self._known_native_resume(previous)
                    self._resume_interrupts[(prefix, occurrence, pending_task.name)] = (
                        saved.interrupts[0].value,
                        previous_resumes,
                    )
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
        saved = await graph.aget_state(config)
        if saved.interrupts:
            if len(saved.interrupts) != 1:
                raise InvocationStateError("native scope has ambiguous interruptions")
            raise self._native_interrupt(saved.interrupts[0].value)
        self._read(cast(_GraphState, result), state, prefix)
        self._record_completed_scope(cast(_GraphState, result), prefix, occurrence)
        state.scope_exit = False
