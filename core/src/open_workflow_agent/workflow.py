"""Open Workflow loading, validation, normalization, expressions, and execution."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

import yaml
from jsonschema import Draft202012Validator

from .catalog import CatalogContext, FunctionCatalog
from .errors import (
    ExpressionError,
    InvocationCancelled,
    ToolError,
    UnsupportedWorkflowFeature,
    WorkflowExecutionError,
    WorkflowSchemaError,
    WorkflowSemanticError,
)
from .external_catalog import parse_catalog_function_reference
from .lifecycle import LifecycleControl
from .observability import EventSink, NullEventSink, WorkflowEvent

DEFAULT_WORKFLOW: dict[str, Any] = {
    "document": {
        "dsl": "1.0.3",
        "namespace": "open-workflow-agent",
        "name": "default-agent",
        "version": "1.0.0",
    },
    "do": [{"respond": {"call": "agent:1.0.0@default"}}],
}

OFFICIAL_SCHEMA_RELATIVE_PATH = Path("resources/open-workflow/1.0.3/workflow.yaml")

SUPPORTED_TASKS = (
    "do",
    "call",
    "set",
    "switch",
    "for",
    "fork",
    "try",
    "wait",
    "listen",
    "emit",
    "run",
    "raise",
)
SUPPORTED_PROTOCOL_CALLS = ("http", "mcp", "a2a", "openapi")
SUPPORTED_FUNCTION_CALLS = ("agent:1.0.0@default", "llm:1.0.0@default")
SUPPORTED_CALLS = {*SUPPORTED_PROTOCOL_CALLS, *SUPPORTED_FUNCTION_CALLS}
DISABLED_TASKS: set[str] = set()


class _NoCancellationToken:
    def checkpoint(self) -> None:
        return None

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    async def await_operation(self, operation: Any) -> Any:
        return await operation


def load_workflow(source: str | Path | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(source, Mapping):
        return copy.deepcopy(dict(source))
    path = Path(source)
    try:
        raw = path.read_text(encoding="utf-8")
        loaded = yaml.safe_load(raw)
    except OSError as exc:
        raise WorkflowSchemaError(f"unable to read workflow: {path}") from exc
    except yaml.YAMLError as exc:
        raise WorkflowSchemaError(f"invalid workflow YAML: {exc}") from exc
    if not isinstance(loaded, dict):
        raise WorkflowSchemaError("workflow root must be an object")
    return loaded


def generate_default_workflow() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_WORKFLOW)


@lru_cache(maxsize=1)
def _official_schema() -> dict[str, Any]:
    configured = os.getenv("OWA_SCHEMA_PATH")
    candidates = ((Path(configured),) if configured else ()) + (
        Path(__file__).resolve().parent / "_resources/open-workflow/1.0.3/workflow.yaml",
        Path(__file__).resolve().parents[3] / OFFICIAL_SCHEMA_RELATIVE_PATH,
        Path.cwd() / OFFICIAL_SCHEMA_RELATIVE_PATH,
    )
    for path in candidates:
        if not path.is_file():
            continue
        try:
            schema = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise WorkflowSchemaError(f"unable to load official schema: {path}") from exc
        if not isinstance(schema, dict):
            raise WorkflowSchemaError(f"official schema must be an object: {path}")
        return cast(dict[str, Any], schema)
    raise WorkflowSchemaError(
        "official Open Workflow 1.0.3 schema is unavailable; expected "
        f"{OFFICIAL_SCHEMA_RELATIVE_PATH}"
    )


def validate_schema(workflow: Mapping[str, Any]) -> None:
    validator = Draft202012Validator(_official_schema())
    errors = sorted(validator.iter_errors(workflow), key=lambda error: list(error.path))
    if errors:
        first = errors[0]
        path = "/".join(str(item) for item in first.path) or "root"
        raise WorkflowSchemaError(f"schema validation failed at {path}: {first.message}")
    document = workflow.get("document")
    if not isinstance(document, dict) or document.get("dsl") != "1.0.3":
        raise WorkflowSchemaError("only Open Workflow DSL 1.0.3 is supported")


def validate_capabilities(
    workflow: Mapping[str, Any], *, trusted_catalogs: Mapping[str, Any] | None = None
) -> None:
    _validate_catalog_capability(workflow, trusted_catalogs=trusted_catalogs)
    use = workflow.get("use")
    catalogs = use.get("catalogs") if isinstance(use, Mapping) else None
    _validate_schedule_capability(workflow)
    for reference, definition in _walk_tasks(workflow.get("do", []), prefix="/do"):
        if not isinstance(definition, Mapping):
            raise WorkflowSemanticError(f"task {reference} must be an object")
        task_keys = set(definition)
        if task_keys & DISABLED_TASKS:
            name = next(iter(task_keys & DISABLED_TASKS))
            raise UnsupportedWorkflowFeature(
                f"task '{name}' is disabled", details={"reference": reference}
            )
        known = task_keys.intersection(SUPPORTED_TASKS)
        if not known:
            raise UnsupportedWorkflowFeature(
                f"no supported task type found at {reference}", details={"keys": sorted(task_keys)}
            )
        if "call" in definition:
            call = definition["call"]
            if not isinstance(call, str) or (
                call not in SUPPORTED_CALLS
                and not _is_trusted_catalog_call(call, catalogs, trusted_catalogs)
            ):
                raise UnsupportedWorkflowFeature(
                    f"call '{call}' is not enabled", details={"reference": reference}
                )
            if call == "mcp":
                with_value = definition.get("with")
                transport = with_value.get("transport") if isinstance(with_value, Mapping) else None
                if isinstance(transport, Mapping) and "stdio" in transport:
                    raise UnsupportedWorkflowFeature(
                        "mcp stdio transport is not enabled", details={"reference": reference}
                    )
        for task_list in _nested_task_lists(definition):
            nested: dict[str, Any] = {"do": task_list}
            if isinstance(catalogs, Mapping):
                nested["use"] = {"catalogs": catalogs}
            validate_capabilities(nested, trusted_catalogs=trusted_catalogs)
        if "emit" in definition:
            _validate_emit_capability(definition["emit"], reference)
        if "listen" in definition:
            _validate_listen_capability(definition["listen"], reference)
        if "run" in definition:
            _validate_run_capability(definition["run"], reference)


def _validate_catalog_capability(
    workflow: Mapping[str, Any], *, trusted_catalogs: Mapping[str, Any] | None = None
) -> None:
    use = workflow.get("use")
    catalogs = use.get("catalogs") if isinstance(use, Mapping) else None
    if not isinstance(catalogs, Mapping) or not catalogs:
        return
    if trusted_catalogs is None:
        raise UnsupportedWorkflowFeature(
            "external workflow catalogs require deployment trust configuration",
            details={"catalogs": sorted(str(name) for name in catalogs)},
        )
    missing = sorted(str(name) for name in catalogs if str(name) not in trusted_catalogs)
    if missing:
        raise UnsupportedWorkflowFeature(
            "external workflow catalog is not deployment-trusted",
            details={"catalogs": missing},
        )


def _is_trusted_catalog_call(
    call: str, catalogs: Any, trusted_catalogs: Mapping[str, Any] | None
) -> bool:
    reference = parse_catalog_function_reference(call)
    return bool(
        reference is not None
        and reference.catalog != "default"
        and isinstance(catalogs, Mapping)
        and reference.catalog in catalogs
        and trusted_catalogs is not None
        and reference.catalog in trusted_catalogs
    )


def _validate_schedule_capability(workflow: Mapping[str, Any]) -> None:
    schedule = workflow.get("schedule")
    if schedule is None:
        return
    if not isinstance(schedule, Mapping):
        raise WorkflowSemanticError("workflow schedule must be an object")
    unsupported = sorted(set(schedule) & {"cron", "on", "read"})
    if unsupported:
        raise UnsupportedWorkflowFeature(
            "schedule features are unsupported in the bounded profile",
            details={"unsupported": unsupported},
        )
    configured = [key for key in ("after", "every") if key in schedule]
    if len(configured) != 1:
        raise WorkflowSemanticError("schedule requires exactly one of after or every")
    duration = _duration_seconds(schedule[configured[0]])
    if duration is None or duration <= 0:
        raise WorkflowSemanticError("schedule duration must be greater than zero")


def compile_workflow(
    source: str | Path | Mapping[str, Any] | None = None,
    *,
    trusted_catalogs: Mapping[str, Any] | None = None,
) -> WorkflowPlan:
    workflow = generate_default_workflow() if source is None else load_workflow(source)
    validate_schema(workflow)
    validate_capabilities(workflow, trusted_catalogs=trusted_catalogs)
    return normalize_workflow(workflow)


@dataclass(frozen=True, slots=True)
class TaskPlan:
    name: str
    reference: str
    kind: str
    definition_json: str

    @property
    def definition(self) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads(self.definition_json))


@dataclass(frozen=True, slots=True)
class WorkflowPlan:
    namespace: str
    name: str
    version: str
    dsl: str
    source_json: str
    tasks: tuple[TaskPlan, ...]

    @property
    def source(self) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads(self.source_json))

    @property
    def fingerprint(self) -> str:
        import hashlib

        return hashlib.sha256(self.source_json.encode("utf-8")).hexdigest()


def normalize_workflow(workflow: Mapping[str, Any]) -> WorkflowPlan:
    document = workflow["document"]
    tasks: list[TaskPlan] = []
    for index, item in enumerate(workflow["do"]):
        if not isinstance(item, Mapping) or len(item) != 1:
            raise WorkflowSemanticError(f"task at /do/{index} must have exactly one name")
        task_name, definition = next(iter(item.items()))
        if not isinstance(definition, Mapping):
            raise WorkflowSemanticError(f"task {task_name} definition must be an object")
        kind = next(
            (candidate for candidate in SUPPORTED_TASKS if candidate in definition), "unknown"
        )
        tasks.append(
            TaskPlan(
                name=str(task_name),
                reference=f"/do/{index}/{task_name}",
                kind=kind,
                definition_json=json.dumps(definition, sort_keys=True, separators=(",", ":")),
            )
        )
    source_json = json.dumps(workflow, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return WorkflowPlan(
        namespace=str(document["namespace"]),
        name=str(document["name"]),
        version=str(document["version"]),
        dsl=str(document["dsl"]),
        source_json=source_json,
        tasks=tuple(tasks),
    )


class ExpressionEvaluator:
    """Small trusted-workflow expression evaluator for the jq portable subset."""

    _template = re.compile(r"\$\{\s*(.*?)\s*\}")

    def evaluate(
        self, expression: Any, data: Any, *, variables: Mapping[str, Any] | None = None
    ) -> Any:
        variables = variables or {}
        if not isinstance(expression, str):
            if isinstance(expression, Mapping):
                return {
                    key: self.evaluate(value, data, variables=variables)
                    for key, value in expression.items()
                }
            if isinstance(expression, list):
                return [self.evaluate(item, data, variables=variables) for item in expression]
            return expression
        text = expression.strip()
        if text.startswith("${") and text.endswith("}"):
            text = text[2:-1].strip()
        if self._template.fullmatch(expression):
            return self._evaluate_atom(text, data, variables)
        if "${" in expression:
            return self._template.sub(
                lambda match: str(self._evaluate_atom(match.group(1).strip(), data, variables)),
                expression,
            )
        if text.startswith(".") or text.startswith("$") or text.startswith("@"):
            return self._evaluate_atom(text, data, variables)
        return expression

    def condition(
        self, expression: Any, data: Any, *, variables: Mapping[str, Any] | None = None
    ) -> bool:
        variables = variables or {}
        if not isinstance(expression, str):
            return bool(self.evaluate(expression, data, variables=variables))
        text = expression.strip()
        if text.startswith("${") and text.endswith("}"):
            text = text[2:-1].strip()
        for operator in (" or ", " and "):
            if operator in text:
                parts = text.split(operator)
                values = [self.condition(part, data, variables=variables) for part in parts]
                return any(values) if operator.strip() == "or" else all(values)
        if text.startswith("not "):
            return not self.condition(text[4:], data, variables=variables)
        for operator in ("==", "!=", ">=", "<=", ">", "<"):
            if operator in text:
                left, right = text.split(operator, 1)
                lhs = self._evaluate_atom(left.strip(), data, variables)
                rhs = self._evaluate_atom(right.strip(), data, variables)
                return bool(
                    {
                        "==": lhs == rhs,
                        "!=": lhs != rhs,
                        ">=": lhs >= rhs,
                        "<=": lhs <= rhs,
                        ">": lhs > rhs,
                        "<": lhs < rhs,
                    }[operator]
                )
        return bool(self._evaluate_atom(text, data, variables))

    def _evaluate_atom(self, expression: str, data: Any, variables: Mapping[str, Any]) -> Any:
        expression = expression.strip()
        while _is_wrapped(expression, "(", ")"):
            expression = expression[1:-1].strip()
        if expression in (".", "$"):
            return data
        if expression.startswith("@"):
            return variables.get(expression[1:])
        if expression.startswith(".") and len(_split_top_level(expression, "+")) == 1:
            return _path_get(data, expression[1:])
        if expression.startswith("$"):
            variable_path = expression[1:].lstrip(".")
            variable_name, _, remainder = variable_path.partition(".")
            if variable_name in variables:
                return (
                    _path_get(variables[variable_name], remainder)
                    if remainder
                    else variables[variable_name]
                )
            return _path_get(data, variable_path)
        if expression in variables:
            return variables[expression]
        if expression in {"true", "false", "null"}:
            return {"true": True, "false": False, "null": None}[expression]
        addition = _split_top_level(expression, "+")
        if len(addition) > 1:
            values = [self._evaluate_atom(part, data, variables) for part in addition]
            result = values[0]
            for value in values[1:]:
                if isinstance(result, list) and isinstance(value, list):
                    result = result + value
                elif isinstance(result, dict) and isinstance(value, dict):
                    result = {**result, **value}
                elif result is None:
                    result = value
                elif value is None:
                    continue
                else:
                    result = result + value
            return result
        if expression.startswith("{") and expression.endswith("}"):
            return {
                key.strip(" '\""): self._evaluate_atom(value, data, variables)
                for item in _split_top_level(expression[1:-1], ",")
                if item.strip()
                for key, value in [_split_mapping_item(item)]
            }
        if expression.startswith("[") and expression.endswith("]"):
            return [
                self._evaluate_atom(item, data, variables)
                for item in _split_top_level(expression[1:-1], ",")
                if item.strip()
            ]
        try:
            return json.loads(expression)
        except json.JSONDecodeError:
            return expression.strip("'\"")


def _path_get(value: Any, path: str) -> Any:
    if not path:
        return value
    parts = [part for part in re.split(r"\.|\[|\]", path) if part]
    current = value
    for part in parts:
        if isinstance(current, Mapping):
            current = current.get(part)
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes)):
            try:
                current = current[int(part)]
            except (IndexError, ValueError):
                return None
        else:
            return None
    return current


def _is_wrapped(value: str, opening: str, closing: str) -> bool:
    if not (value.startswith(opening) and value.endswith(closing)):
        return False
    depth = 0
    quote: str | None = None
    for index, character in enumerate(value):
        if quote:
            if character == quote and (index == 0 or value[index - 1] != "\\"):
                quote = None
            continue
        if character in {"'", '"'}:
            quote = character
        elif character == opening:
            depth += 1
        elif character == closing:
            depth -= 1
            if depth == 0 and index != len(value) - 1:
                return False
    return depth == 0 and quote is None


def _split_top_level(value: str, separator: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    for index, character in enumerate(value):
        if quote:
            if character == quote and (index == 0 or value[index - 1] != "\\"):
                quote = None
            continue
        if character in {"'", '"'}:
            quote = character
        elif character in "([{":
            depth += 1
        elif character in ")]}":
            depth -= 1
        elif character == separator and depth == 0:
            parts.append(value[start:index].strip())
            start = index + 1
    parts.append(value[start:].strip())
    return parts


def _split_mapping_item(value: str) -> tuple[str, str]:
    parts = _split_top_level(value, ":")
    if len(parts) < 2:
        raise ExpressionError(f"invalid object expression: {value}")
    return parts[0], ":".join(parts[1:])


@dataclass(slots=True)
class ExecutionState:
    data: Any
    outputs: dict[str, Any] = field(default_factory=dict)
    variables: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    jump_to: str | None = None
    stop: bool = False


class WorkflowExecutor:
    """Engine-neutral execution of normalized portable plan semantics."""

    def __init__(
        self,
        catalog: FunctionCatalog,
        *,
        services: Any = None,
        event_sink: EventSink | None = None,
    ) -> None:
        self.catalog = catalog
        self.services = services
        self.expressions = ExpressionEvaluator()
        self.event_sink = event_sink or NullEventSink()

    async def execute(
        self, plan: WorkflowPlan, input_data: Any, *, metadata: dict[str, Any] | None = None
    ) -> Any:
        workflow = plan.source
        metadata = dict(metadata or {})
        metadata.setdefault("_lifecycle", LifecycleControl())
        event_context = {
            "invocation_id": metadata.get("invocation_id"),
            "session_id": metadata.get("session_id"),
            "workflow_name": plan.name,
            "workflow_version": plan.version,
            "engine": metadata.get("engine"),
            "parent_invocation_id": metadata.get("parent_invocation_id"),
            "parent_task_reference": metadata.get("parent_task_reference"),
        }
        started = time.perf_counter()
        self._emit("WorkflowStarted", {**event_context, "status": "running"})
        context = metadata
        state = ExecutionState(
            data=copy.deepcopy(input_data),
            context=context,
            variables={"context": context, "input": copy.deepcopy(input_data)},
        )
        try:
            self._validate_data_schema(workflow.get("input"), input_data, "workflow input")
            initial_data = input_data
            input_spec = workflow.get("input")
            if isinstance(input_spec, Mapping) and "from" in input_spec:
                initial_data = self.expressions.evaluate(input_spec["from"], input_data)
                state.data = copy.deepcopy(initial_data)
            workflow_timeout = _duration_seconds(workflow.get("timeout"))
            if workflow_timeout is None:
                await self._run_tasks(plan.source.get("do", []), state)
            else:
                try:
                    await asyncio.wait_for(
                        self._run_tasks(plan.source.get("do", []), state), workflow_timeout
                    )
                except TimeoutError as exc:
                    raise WorkflowExecutionError(
                        "workflow timed out",
                        details={"timeout_seconds": workflow_timeout},
                    ) from exc
        except InvocationCancelled as exc:
            self._set_invocation_status(state, "cancelled")
            self._emit(
                "WorkflowCancelled",
                {
                    **event_context,
                    "duration": time.perf_counter() - started,
                    "status": "cancelled",
                    "reason": exc.details.get("reason", "cancelled"),
                    "error": _error_details(exc),
                },
            )
            raise
        except Exception as exc:
            self._set_invocation_status(state, "faulted")
            self._emit(
                "WorkflowFaulted",
                {
                    **event_context,
                    "duration": time.perf_counter() - started,
                    "status": "faulted",
                    "error": _error_details(exc),
                },
            )
            raise
        output = plan.source.get("output")
        if isinstance(output, Mapping) and "as" in output:
            result = self.expressions.evaluate(output["as"], state.data, variables=state.variables)
        else:
            result = state.data
        self._validate_data_schema(output, result, "workflow output")
        self._emit(
            "WorkflowCompleted",
            {
                **event_context,
                "duration": time.perf_counter() - started,
                "status": "completed",
            },
        )
        self._set_invocation_status(state, "completed")
        return result

    async def _run_tasks(
        self, task_list: Iterable[Any], state: ExecutionState, *, prefix: str = "/do"
    ) -> None:
        tasks = list(task_list)
        index = 0
        while index < len(tasks):
            self._token(state).checkpoint()
            item = tasks[index]
            if not isinstance(item, Mapping) or len(item) != 1:
                raise WorkflowExecutionError(f"invalid task at sequence index {index}")
            name, definition = next(iter(item.items()))
            if not isinstance(definition, Mapping):
                raise WorkflowExecutionError(f"task {name} definition must be an object")
            before = copy.deepcopy(state.data)
            reference = f"{prefix}/{index}/{name}"
            state.variables["_task_reference"] = reference
            state.variables["_task_name"] = str(name)
            task_started = time.perf_counter()
            self._emit(
                "TaskStarted",
                {**self._task_event(reference, name, state), "status": "running"},
            )
            try:
                result = await self._run_with_policy(str(name), definition, state, reference)
            except InvocationCancelled as exc:
                self._emit(
                    "TaskCancelled",
                    {
                        **self._task_event(reference, name, state),
                        "duration": time.perf_counter() - task_started,
                        "status": "cancelled",
                        "reason": exc.details.get("reason", "cancelled"),
                        "error": _error_details(exc),
                    },
                )
                raise
            except Exception as exc:
                if isinstance(exc, WorkflowExecutionError):
                    exc.details.setdefault("instance", reference)
                self._emit(
                    "TaskFaulted",
                    {
                        **self._task_event(reference, name, state),
                        "duration": time.perf_counter() - task_started,
                        "status": "faulted",
                        "error": _error_details(exc),
                    },
                )
                if isinstance(
                    exc,
                    (
                        WorkflowExecutionError,
                        WorkflowSemanticError,
                        UnsupportedWorkflowFeature,
                        ExpressionError,
                        ToolError,
                    ),
                ):
                    raise
                raise WorkflowExecutionError(
                    f"task {name} failed: {exc}", details={"task": name}
                ) from exc
            state.outputs[str(name)] = result
            state.data = self._apply_task_data(definition, result, before, state)
            self._emit(
                "TaskCompleted",
                {
                    **self._task_event(reference, name, state),
                    "duration": time.perf_counter() - task_started,
                    "status": "completed",
                },
            )
            if state.jump_to is None and not state.stop:
                self._apply_transition(definition.get("then"), state)
            if state.stop:
                return
            if state.jump_to is not None:
                target = state.jump_to
                state.jump_to = None
                target_index = next(
                    (
                        candidate
                        for candidate, task in enumerate(tasks)
                        if isinstance(task, Mapping) and target in task
                    ),
                    None,
                )
                if target_index is None:
                    raise WorkflowExecutionError(
                        f"task transition target not found: {target}",
                        details={"task": str(name)},
                    )
                index = target_index
                continue
            index += 1

    async def _run_with_policy(
        self,
        name: str,
        definition: Mapping[str, Any],
        state: ExecutionState,
        reference: str,
    ) -> Any:
        retry = definition.get("retry")
        attempts = 1
        if isinstance(retry, Mapping):
            attempts = int(retry.get("max_attempts", retry.get("limit", 0))) + 1
        elif retry is not None:
            attempts = int(retry) + 1
        timeout = definition.get("timeout")
        timeout_seconds = _duration_seconds(timeout)
        last_error: Exception | None = None
        for attempt in range(max(1, attempts)):
            try:
                self._token(state).checkpoint()
                self._emit(
                    "TaskProgress",
                    {
                        **self._task_event(reference, name, state),
                        "status": "running",
                        "attempt": attempt + 1,
                        "progress": {"phase": "executing"},
                    },
                )
                operation = self._run_task(name, definition, state)
                if timeout_seconds is not None:
                    try:
                        return await asyncio.wait_for(
                            self._await_with_cancellation(operation, state), timeout_seconds
                        )
                    except TimeoutError as exc:
                        raise WorkflowExecutionError(
                            f"task {name} timed out",
                            details={"task": name, "timeout_seconds": timeout_seconds},
                        ) from exc
                return await self._await_with_cancellation(operation, state)
            except Exception as exc:
                last_error = exc
                if isinstance(exc, InvocationCancelled):
                    raise
                if attempt + 1 < max(1, attempts):
                    self._emit(
                        "TaskRetried",
                        {
                            **self._task_event(reference, name, state),
                            "status": "retrying",
                            "attempt": attempt + 1,
                            "error": _error_details(exc),
                        },
                    )
        assert last_error is not None
        raise last_error

    async def _run_task(
        self, name: str, definition: Mapping[str, Any], state: ExecutionState
    ) -> Any:
        if "if" in definition and not self.expressions.condition(
            definition["if"], state.data, variables=state.variables
        ):
            return state.data
        task_input = state.data
        input_spec = definition.get("input")
        if isinstance(input_spec, Mapping) and "from" in input_spec:
            task_input = self.expressions.evaluate(
                input_spec["from"], state.data, variables=state.variables
            )
        elif input_spec is not None and not isinstance(input_spec, Mapping):
            task_input = self.expressions.evaluate(
                input_spec, state.data, variables=state.variables
            )
        self._validate_data_schema(input_spec, task_input, f"task {name} input")
        state.variables["input"] = copy.deepcopy(task_input)
        if "set" in definition:
            return self.expressions.evaluate(
                definition["set"], task_input, variables=state.variables
            )
        if "call" in definition:
            payload = definition.get("with", task_input)
            payload = self.expressions.evaluate(payload, state.data, variables=state.variables)
            if str(definition["call"]) in {"http", "mcp", "a2a", "openapi"} and isinstance(
                payload, Mapping
            ):
                payload = _resolve_protocol_endpoints(payload, state.data, state.variables)
                payload = {
                    **payload,
                    "operation_id": payload.get("operation_id")
                    or payload.get("operationId")
                    or self._operation_id(state),
                }
            return await self._call(str(definition["call"]), payload, state)
        if "emit" in definition:
            return await self._run_emit(definition["emit"], state)
        if "listen" in definition:
            return await self._run_listen(definition["listen"], state)
        if "run" in definition:
            return await self._run_subworkflow(definition["run"], state)
        if "switch" in definition:
            return await self._run_switch(definition["switch"], state)
        if "for" in definition:
            return await self._run_for(definition, state)
        if "fork" in definition:
            return await self._run_fork(definition["fork"], state)
        if "do" in definition:
            await self._run_tasks(_task_body(definition["do"]), state)
            return state.data
        if "try" in definition:
            catch = definition.get("catch") or definition.get("except")
            if catch is None:
                await self._run_tasks(_task_body(definition["try"]), state)
                return state.data
            catch_retry = catch.get("retry") if isinstance(catch, Mapping) else None
            attempts = _retry_attempts(catch_retry)
            for attempt in range(attempts):
                try:
                    await self._run_tasks(_task_body(definition["try"]), state)
                    return state.data
                except Exception as exc:
                    if isinstance(exc, InvocationCancelled):
                        raise
                    error = _error_details(exc)
                    state.variables["error"] = error
                    if not _catch_matches(catch, error, state, self.expressions):
                        raise
                    if attempt + 1 < attempts and _retry_allowed(
                        catch_retry, state, self.expressions
                    ):
                        delay = _retry_delay(catch_retry, attempt)
                        if delay:
                            await self._token(state).sleep(delay)
                        self._emit(
                            "TaskRetried",
                            {
                                **self._task_event(
                                    str(state.variables.get("_task_reference", "unknown-task")),
                                    state.variables.get("_task_name", name),
                                    state,
                                ),
                                "status": "retrying",
                                "attempt": attempt + 1,
                                "error": _error_details(exc),
                            },
                        )
                        continue
                    as_name = catch.get("as", "error")
                    if isinstance(as_name, str):
                        state.variables[as_name] = error
                    await self._run_tasks(_task_body(catch.get("do", [])), state)
                    self._apply_transition(catch.get("then"), state)
                    return state.data
        if "wait" in definition:
            wait_value = self.expressions.evaluate(
                definition["wait"], state.data, variables=state.variables
            )
            seconds = max(0.0, min(_duration_seconds(wait_value) or 0, 300.0))
            self._set_invocation_status(state, "waiting")
            task_reference = str(state.variables.get("_task_reference", "unknown-task"))
            wait_event = {
                **self._task_event(task_reference, name, state),
                "status": "waiting",
                "progress": {"phase": "waiting", "seconds": seconds},
            }
            self._emit("TaskProgress", wait_event)
            self._emit("TaskWaiting", wait_event)
            self._emit("WorkflowWaiting", wait_event)
            control = self._control(state)
            resumed = False
            if control is None:
                await asyncio.sleep(seconds)
            else:
                resumed = await control.wait_or_resume(seconds)
            self._token(state).checkpoint()
            if control is not None and resumed:
                state.variables["resume_input"] = copy.deepcopy(control.resume_input)
            self._set_invocation_status(state, "running")
            resume_event = {
                **self._task_event(task_reference, name, state),
                "status": "running",
                "reason": "resume_requested" if resumed else "wait_elapsed",
                "progress": {"phase": "resumed"},
            }
            self._emit("TaskProgress", resume_event)
            self._emit("WorkflowResumed", resume_event)
            return state.data
        if "raise" in definition:
            raise_value = definition["raise"]
            if isinstance(raise_value, Mapping):
                error = raise_value.get("error", raise_value)
                if isinstance(error, Mapping):
                    message = error.get("detail") or error.get("title") or error.get("type")
                    details = dict(error)
                else:
                    message = error
                    details = {}
            else:
                message = raise_value
                details = {}
            message = self.expressions.evaluate(message, state.data, variables=state.variables)
            raise WorkflowExecutionError(str(message), details=details)
        raise UnsupportedWorkflowFeature(f"unsupported task {name}")

    async def _call(self, call: str, payload: Any, state: ExecutionState) -> Any:
        if call in {"http", "mcp", "a2a", "openapi"}:
            if self.services is None:
                raise WorkflowExecutionError(f"protocol service unavailable: {call}")
            return await self.services.call_protocol(call, payload)
        if self.services is None:
            raise WorkflowExecutionError("runtime services unavailable for catalog call")
        context = CatalogContext(
            model=self.services.model,
            agent_instruction=getattr(self.services, "agent_instruction", ""),
            services=self.services,
            metadata=state.context,
        )
        return await self.catalog.call(call, payload, context)

    async def _run_switch(self, switch: Any, state: ExecutionState) -> Any:
        entries = (
            switch
            if isinstance(switch, list)
            else switch.get("cases", [])
            if isinstance(switch, Mapping)
            else []
        )
        otherwise: Any = None
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            case = entry
            if len(entry) == 1:
                candidate = next(iter(entry.values()))
                if isinstance(candidate, Mapping):
                    case = candidate
            if "otherwise" in entry:
                otherwise = entry["otherwise"]
                continue
            condition = case.get("when", case.get("case", case.get("if")))
            if condition is None or self.expressions.condition(
                condition, state.data, variables=state.variables
            ):
                body = case.get("do")
                if body is not None:
                    await self._run_tasks(_task_body(body), state)
                self._apply_transition(case.get("then"), state)
                return state.data
        if otherwise is not None:
            await self._run_tasks(_task_body(otherwise), state)
        return state.data

    async def _run_for(self, definition: Any, state: ExecutionState) -> Any:
        if not isinstance(definition, Mapping):
            raise WorkflowSemanticError("for must be an object")
        configuration = definition.get("for", definition)
        if not isinstance(configuration, Mapping):
            raise WorkflowSemanticError("for configuration must be an object")
        values = self.expressions.evaluate(
            configuration.get("in", configuration.get("over", [])),
            state.data,
            variables=state.variables,
        )
        if values is None:
            return state.data
        variable = str(configuration.get("each", configuration.get("as", "item")))
        body = _task_body(definition.get("do", definition.get("body", [])))
        results: list[Any] = []
        for index, value in enumerate(values):
            child = ExecutionState(
                copy.deepcopy(state.data),
                dict(state.outputs),
                dict(state.variables),
                dict(state.context),
            )
            child.variables[variable] = value
            child.variables["index"] = index
            await self._run_tasks(body, child)
            state.data = child.data
            results.append(child.data)
        if definition.get("output") == "array":
            return results
        return results[-1] if results else state.data

    async def _run_fork(self, definition: Any, state: ExecutionState) -> Any:
        compete = False
        if isinstance(definition, Mapping):
            branches = definition.get("branches", [])
            compete = bool(definition.get("compete", False))
        else:
            branches = definition
        if not isinstance(branches, list):
            raise WorkflowSemanticError("fork branches must be a list")

        async def run_branch(branch: Any) -> Any:
            child = ExecutionState(
                copy.deepcopy(state.data),
                dict(state.outputs),
                dict(state.variables),
                dict(state.context),
            )
            await self._run_tasks(
                _task_body(branch.get("do", branch) if isinstance(branch, Mapping) else branch),
                child,
            )
            return child.data

        if compete:
            branch_tasks = [asyncio.create_task(run_branch(branch)) for branch in branches]
            if not branch_tasks:
                return state.data
            done, pending = await asyncio.wait(branch_tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            return next(iter(done)).result()

        results = await asyncio.gather(*(run_branch(branch) for branch in branches))
        if results and all(isinstance(result, Mapping) for result in results):
            merged = dict(state.data) if isinstance(state.data, Mapping) else {}
            for result in results:
                merged.update(result)
            return merged
        return results

    async def _run_subworkflow(self, definition: Any, state: ExecutionState) -> Any:
        if self.services is None or not hasattr(self.services, "workflow_catalog"):
            raise WorkflowExecutionError("workflow catalog is unavailable for run task")
        runner = getattr(self.services, "workflow_runner", None)
        if runner is None:
            raise WorkflowExecutionError("workflow runner is unavailable for run task")
        if not isinstance(definition, Mapping):
            raise WorkflowSemanticError("run configuration must be an object")
        reference = definition.get("workflow")
        if not isinstance(reference, Mapping):
            raise WorkflowSemanticError("run.workflow must be an object")
        plan = self.services.workflow_catalog.resolve(reference)
        child_input = reference.get("input", state.data)
        child_input = self.expressions.evaluate(child_input, state.data, variables=state.variables)
        task_reference = str(state.variables.get("_task_reference", "unknown-task"))
        return await runner(plan, child_input, state.context, task_reference)

    async def _run_emit(self, definition: Any, state: ExecutionState) -> Any:
        if self.services is None or not hasattr(self.services, "event_bus"):
            raise WorkflowExecutionError("event service unavailable for emit task")
        if not isinstance(definition, Mapping):
            raise WorkflowSemanticError("emit configuration must be an object")
        event = definition.get("event")
        properties = event.get("with") if isinstance(event, Mapping) else None
        if not isinstance(properties, Mapping):
            raise WorkflowSemanticError("emit event.with must be an object")
        evaluated = self.expressions.evaluate(properties, state.data, variables=state.variables)
        if not isinstance(evaluated, Mapping):
            raise WorkflowSemanticError("emit event.with must evaluate to an object")
        try:
            envelope = await self.services.event_bus.publish(
                evaluated,
                default_source=self._default_event_source(state),
            )
        except ValueError as exc:
            raise WorkflowSemanticError(str(exc)) from exc
        task_reference = str(state.variables.get("_task_reference", "unknown-task"))
        task_name = state.variables.get("_task_name", "emit")
        self._emit(
            "EventEmitted",
            {
                **self._task_event(task_reference, task_name, state),
                "status": "completed",
                "event_id": envelope.id,
                "event_name": envelope.type,
            },
        )
        return state.data

    async def _run_listen(self, definition: Any, state: ExecutionState) -> Any:
        if self.services is None or not hasattr(self.services, "event_bus"):
            raise WorkflowExecutionError("event service unavailable for listen task")
        if not isinstance(definition, Mapping):
            raise WorkflowSemanticError("listen configuration must be an object")
        if not isinstance(definition.get("to"), Mapping):
            raise WorkflowSemanticError("listen.to must be an object")
        strategy = self.expressions.evaluate(
            definition["to"], state.data, variables=state.variables
        )
        if not isinstance(strategy, Mapping):
            raise WorkflowSemanticError("listen.to must evaluate to an object")
        read = definition.get("read", "data")
        if read not in {"data", "envelope", "raw"}:
            raise WorkflowSemanticError(f"unsupported listen read mode: {read}")
        task_reference = str(state.variables.get("_task_reference", "unknown-task"))
        task_name = state.variables.get("_task_name", "listen")
        self._set_invocation_status(state, "waiting")
        waiting_event = {
            **self._task_event(task_reference, task_name, state),
            "status": "waiting",
            "progress": {"phase": "listening"},
        }
        self._emit("TaskProgress", waiting_event)
        self._emit("TaskWaiting", waiting_event)
        self._emit("WorkflowWaiting", waiting_event)
        envelope = await self._await_with_cancellation(
            self.services.event_bus.receive(strategy), state
        )
        self._token(state).checkpoint()
        state.variables["event"] = envelope.as_dict()
        self._set_invocation_status(state, "running")
        event_context = {
            **self._task_event(task_reference, task_name, state),
            "status": "completed",
            "event_id": envelope.id,
            "event_name": envelope.type,
        }
        self._emit("EventReceived", event_context)
        self._emit(
            "TaskProgress",
            {
                **event_context,
                "status": "running",
                "reason": "event_received",
                "progress": {"phase": "event_received"},
            },
        )
        self._emit(
            "WorkflowResumed",
            {
                **self._task_event(task_reference, task_name, state),
                "status": "running",
                "reason": "event_received",
                "event_id": envelope.id,
                "event_name": envelope.type,
            },
        )
        if read == "data":
            return envelope.data
        if read == "envelope":
            return envelope.as_dict()
        return envelope.raw()

    @staticmethod
    def _default_event_source(state: ExecutionState) -> str:
        name = state.context.get("workflow_name", "workflow")
        version = state.context.get("workflow_version", "1.0.0")
        return f"urn:open-workflow-agent:{name}:{version}"

    @staticmethod
    def _apply_transition(transition: Any, state: ExecutionState) -> None:
        if transition is None or transition == "continue":
            return
        if transition in {"end", "exit"}:
            state.stop = True
            return
        if isinstance(transition, str):
            state.jump_to = transition

    def _apply_task_data(
        self, definition: Mapping[str, Any], result: Any, before: Any, state: ExecutionState
    ) -> Any:
        output = result
        output_spec = definition.get("output")
        if isinstance(output_spec, Mapping) and "as" in output_spec:
            output = self.expressions.evaluate(output_spec["as"], result, variables=state.variables)
        self._validate_data_schema(output_spec, output, "task output")
        export_spec = definition.get("export")
        if isinstance(export_spec, Mapping) and "as" in export_spec:
            exported = self.expressions.evaluate(
                export_spec["as"], result, variables=state.variables
            )
            state.context = _merge(state.context, exported)
            state.variables["context"] = state.context
            self._validate_data_schema(export_spec, exported, "task export")
        if isinstance(output, Mapping) and isinstance(before, Mapping):
            return dict(output)
        return output

    def _validate_data_schema(self, specification: Any, value: Any, label: str) -> None:
        if not isinstance(specification, Mapping):
            return
        schema = specification.get("schema")
        if not isinstance(schema, Mapping):
            return
        document = schema.get("document")
        if not isinstance(document, Mapping):
            return
        errors = sorted(
            Draft202012Validator(document).iter_errors(value),
            key=lambda error: list(error.path),
        )
        if errors:
            first = errors[0]
            path = "/".join(str(item) for item in first.path) or "root"
            raise WorkflowSemanticError(
                f"{label} schema validation failed at {path}: {first.message}"
            )

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        self.event_sink.emit(WorkflowEvent(event_type=event_type, **payload))

    def _control(self, state: ExecutionState) -> LifecycleControl | None:
        control = state.context.get("_lifecycle")
        return control if isinstance(control, LifecycleControl) else None

    def _token(self, state: ExecutionState) -> Any:
        control = self._control(state)
        if control is None:
            return _NoCancellationToken()
        return control.token

    async def _await_with_cancellation(self, operation: Any, state: ExecutionState) -> Any:
        control = self._control(state)
        if control is None:
            return await operation
        return await control.token.await_operation(operation)

    def _set_invocation_status(self, state: ExecutionState, status: str) -> None:
        handle = state.context.get("_invocation_handle")
        if handle is not None and self.services is not None:
            self.services.invocations.update(handle, status=status)

    def _task_event(self, reference: str, name: Any, state: ExecutionState) -> dict[str, Any]:
        context = state.context
        return {
            "invocation_id": context.get("invocation_id"),
            "session_id": context.get("session_id"),
            "workflow_name": context.get("workflow_name"),
            "workflow_version": context.get("workflow_version"),
            "task_name": str(name),
            "task_reference": reference,
            "engine": context.get("engine"),
            "operation_id": self._operation_id(state),
            "parent_invocation_id": context.get("parent_invocation_id"),
            "parent_task_reference": context.get("parent_task_reference"),
        }

    @staticmethod
    def _operation_id(state: ExecutionState) -> str:
        invocation = state.context.get("invocation_id", "unknown-invocation")
        reference = state.variables.get("_task_reference", "unknown-task")
        return f"{invocation}:{reference}"


def _merge(left: Any, right: Any) -> Any:
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        merged = dict(left)
        merged.update(right)
        return merged
    return right


def _validate_emit_capability(definition: Any, reference: str) -> None:
    if not isinstance(definition, Mapping):
        raise WorkflowSemanticError(f"emit configuration must be an object at {reference}")
    event = definition.get("event")
    if not isinstance(event, Mapping) or not isinstance(event.get("with"), Mapping):
        raise WorkflowSemanticError(f"emit requires event.with at {reference}")


def _validate_listen_capability(definition: Any, reference: str) -> None:
    if not isinstance(definition, Mapping):
        raise WorkflowSemanticError(f"listen configuration must be an object at {reference}")
    strategy = definition.get("to")
    if not isinstance(strategy, Mapping) or set(strategy) != {"one"}:
        raise UnsupportedWorkflowFeature(
            "listen currently supports only the one-event strategy",
            details={"reference": reference, "supported": ["one"]},
        )
    selected = strategy.get("one")
    if not isinstance(selected, Mapping) or not isinstance(selected.get("with"), Mapping):
        raise WorkflowSemanticError(f"listen.one.with is required at {reference}")
    if "foreach" in definition:
        raise UnsupportedWorkflowFeature(
            "listen foreach iteration is not enabled", details={"reference": reference}
        )


def _validate_run_capability(definition: Any, reference: str) -> None:
    if not isinstance(definition, Mapping):
        raise WorkflowSemanticError(f"run configuration must be an object at {reference}")
    if "shell" in definition or "script" in definition:
        raise UnsupportedWorkflowFeature(
            "run shell and script execution are disabled",
            details={"reference": reference},
        )
    workflow = definition.get("workflow")
    if not isinstance(workflow, Mapping):
        raise WorkflowSemanticError(f"run.workflow is required at {reference}")
    required = {"namespace", "name", "version"}
    if not required.issubset(workflow):
        raise WorkflowSemanticError(
            f"run.workflow requires namespace, name, and version at {reference}"
        )


def _resolve_protocol_endpoints(
    payload: Mapping[str, Any], data: Any, variables: Mapping[str, Any]
) -> dict[str, Any]:
    endpoint_keys = {"endpoint", "server", "uri", "url"}

    def resolve(value: Any, key: str | None = None) -> Any:
        if isinstance(value, Mapping):
            return {name: resolve(item, str(name)) for name, item in value.items()}
        if isinstance(value, list):
            return [resolve(item, key) for item in value]
        if key not in endpoint_keys or not isinstance(value, str):
            return value

        def replace(match: re.Match[str]) -> str:
            expression = match.group(1).strip()
            lookup = expression[1:] if expression.startswith("$") else expression
            resolved = _path_get(data, lookup.lstrip("."))
            if resolved is None and lookup in variables:
                resolved = variables[lookup]
            if resolved is None:
                raise WorkflowSemanticError(
                    f"unable to resolve protocol endpoint template: {{{expression}}}"
                )
            return str(resolved)

        return re.sub(r"\{([^{}]+)\}", replace, value)

    return cast(dict[str, Any], resolve(payload))


def _retry_attempts(value: Any) -> int:
    if value is None:
        return 1
    if isinstance(value, Mapping):
        limit = value.get("limit", value.get("max_attempts", 0))
        if isinstance(limit, Mapping):
            limit = limit.get("attempt", {}).get("count", 0)
            if isinstance(limit, Mapping):
                limit = limit.get("count", 0)
        try:
            return max(1, int(limit) + 1)
        except (TypeError, ValueError):
            return 1
    try:
        return max(1, int(value) + 1)
    except (TypeError, ValueError):
        return 1


def _catch_matches(
    catch: Mapping[str, Any],
    error: Mapping[str, Any],
    state: ExecutionState,
    expressions: ExpressionEvaluator,
) -> bool:
    errors = catch.get("errors")
    filters = errors.get("with") if isinstance(errors, Mapping) else None
    if isinstance(filters, Mapping):
        error_details: Mapping[str, Any] = (
            error["details"] if isinstance(error.get("details"), Mapping) else {}
        )
        values = {
            "type": error_details.get("type", error.get("code")),
            "status": error_details.get("status", error.get("status_code")),
            "instance": error_details.get("instance"),
            "title": error_details.get("title", error.get("code")),
            "detail": error_details.get("detail", error.get("message")),
        }
        if any(values.get(key) != value for key, value in filters.items()):
            return False
    if catch.get("when") is not None and not expressions.condition(
        catch["when"], state.data, variables=state.variables
    ):
        return False
    if catch.get("exceptWhen") is not None and expressions.condition(
        catch["exceptWhen"], state.data, variables=state.variables
    ):
        return False
    return True


def _retry_allowed(retry: Any, state: ExecutionState, expressions: ExpressionEvaluator) -> bool:
    if not isinstance(retry, Mapping):
        return True
    if retry.get("when") is not None and not expressions.condition(
        retry["when"], state.data, variables=state.variables
    ):
        return False
    if retry.get("exceptWhen") is not None and expressions.condition(
        retry["exceptWhen"], state.data, variables=state.variables
    ):
        return False
    return True


def _retry_delay(retry: Any, attempt: int) -> float | None:
    if not isinstance(retry, Mapping):
        return None
    delay = _duration_seconds(retry.get("delay"))
    if delay is None:
        return None
    delay_seconds: float = float(delay)
    backoff = retry.get("backoff")
    if isinstance(backoff, Mapping):
        if "exponential" in backoff:
            return delay_seconds * (2.0**attempt)
        if "linear" in backoff:
            return delay_seconds * (attempt + 1)
    return delay_seconds


def _error_details(error: Exception) -> dict[str, Any]:
    if hasattr(error, "as_dict"):
        return cast(dict[str, Any], error.as_dict())
    return {"code": "workflow_execution_error", "message": str(error), "details": {}}


def _duration_seconds(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        if "after" in value:
            return _duration_seconds(value["after"])
        units = {
            "days": 86_400,
            "hours": 3_600,
            "minutes": 60,
            "seconds": 1,
            "milliseconds": 0.001,
        }
        if any(unit in value for unit in units):
            return sum(float(value.get(unit, 0)) * multiplier for unit, multiplier in units.items())
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lower()
    iso = re.fullmatch(
        r"p(?:(?P<days>[0-9]+(?:\.[0-9]+)?)d)?(?:t(?:(?P<hours>[0-9]+(?:\.[0-9]+)?)h)?(?:(?P<minutes>[0-9]+(?:\.[0-9]+)?)m)?(?:(?P<seconds>[0-9]+(?:\.[0-9]+)?)s)?)?",
        text,
    )
    if iso and any(iso.group(name) for name in ("days", "hours", "minutes", "seconds")):
        return sum(
            float(iso.group(name) or 0) * multiplier
            for name, multiplier in (
                ("days", 86_400),
                ("hours", 3_600),
                ("minutes", 60),
                ("seconds", 1),
            )
        )
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*(ms|s|m|h)?", text)
    if not match:
        raise WorkflowSemanticError(f"invalid duration: {value}")
    number = float(match.group(1))
    return (
        number / 1000
        if match.group(2) == "ms"
        else number * 60
        if match.group(2) == "m"
        else number * 3600
        if match.group(2) == "h"
        else number
    )


def _task_body(value: Any) -> list[Any]:
    if isinstance(value, Mapping) and "do" in value:
        value = value["do"]
    if isinstance(value, list):
        return value
    if isinstance(value, Mapping):
        return [value]
    return []


def _nested_task_lists(definition: Mapping[str, Any]) -> list[list[Any]]:
    nested: list[list[Any]] = []
    if isinstance(definition.get("do"), list):
        nested.append(definition["do"])
    for_key = definition.get("for")
    if isinstance(for_key, Mapping) and isinstance(definition.get("do"), list):
        nested.append(definition["do"])
    fork = definition.get("fork")
    branches = fork.get("branches", []) if isinstance(fork, Mapping) else []
    if isinstance(branches, list):
        for branch in branches:
            nested.append(_task_body(branch))
    try_tasks = definition.get("try")
    if isinstance(try_tasks, list):
        nested.append(try_tasks)
    catch = definition.get("catch")
    if isinstance(catch, Mapping) and isinstance(catch.get("do"), list):
        nested.append(catch["do"])
    switch = definition.get("switch")
    entries = switch if isinstance(switch, list) else []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        case = next(iter(entry.values())) if len(entry) == 1 else entry
        if isinstance(case, Mapping) and case.get("do") is not None:
            nested.append(_task_body(case["do"]))
    return nested


def _walk_tasks(task_list: Any, *, prefix: str) -> Iterable[tuple[str, Any]]:
    if not isinstance(task_list, list):
        return
    for index, item in enumerate(task_list):
        if not isinstance(item, Mapping):
            yield f"{prefix}/{index}", item
            continue
        for name, definition in item.items():
            reference = f"{prefix}/{index}/{name}"
            yield reference, definition
