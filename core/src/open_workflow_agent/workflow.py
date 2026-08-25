"""Open Workflow loading, validation, normalization, expressions, and execution."""

from __future__ import annotations

import asyncio
import copy
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import yaml
from jsonschema import Draft202012Validator

from .catalog import CatalogContext, FunctionCatalog
from .errors import (
    ExpressionError,
    UnsupportedWorkflowFeature,
    WorkflowExecutionError,
    WorkflowSchemaError,
    WorkflowSemanticError,
)

DEFAULT_WORKFLOW: dict[str, Any] = {
    "document": {
        "dsl": "1.0.3",
        "namespace": "open-workflow-agent",
        "name": "default-agent",
        "version": "1.0.0",
    },
    "do": [{"respond": {"call": "agent:1.0.0@default"}}],
}

# This is a structural validation gate for the Portable Profile. The official
# upstream schema remains the normative schema; the loader intentionally does
# not add proprietary task or call keywords to it.
PORTABLE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://open-workflow-specification.org/schemas/1.0.3/workflow.yaml",
    "type": "object",
    "required": ["document", "do"],
    "properties": {
        "document": {"type": "object", "required": ["dsl", "namespace", "name", "version"]},
        "do": {"type": "array", "minItems": 1, "items": {"type": "object", "minProperties": 1}},
        "input": {"type": "object"},
        "output": {"type": "object"},
        "use": {"type": "array"},
        "timeout": {"type": "object"},
    },
    "additionalProperties": True,
}

SUPPORTED_TASKS = {"call", "set", "switch", "for", "fork", "try", "wait", "raise"}
SUPPORTED_CALLS = {
    "http",
    "mcp",
    "a2a",
    "openapi",
    "agent:1.0.0@default",
    "llm:1.0.0@default",
}
DISABLED_TASKS = {"run"}


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


def validate_schema(workflow: Mapping[str, Any]) -> None:
    validator = Draft202012Validator(PORTABLE_SCHEMA)
    errors = sorted(validator.iter_errors(workflow), key=lambda error: list(error.path))
    if errors:
        first = errors[0]
        path = "/".join(str(item) for item in first.path) or "root"
        raise WorkflowSchemaError(f"schema validation failed at {path}: {first.message}")
    document = workflow.get("document")
    if not isinstance(document, dict) or document.get("dsl") != "1.0.3":
        raise WorkflowSchemaError("only Open Workflow DSL 1.0.3 is supported")


def validate_capabilities(workflow: Mapping[str, Any]) -> None:
    for reference, definition in _walk_tasks(workflow.get("do", []), prefix="/do"):
        if not isinstance(definition, Mapping):
            raise WorkflowSemanticError(f"task {reference} must be an object")
        task_keys = set(definition)
        if task_keys & DISABLED_TASKS:
            name = next(iter(task_keys & DISABLED_TASKS))
            raise UnsupportedWorkflowFeature(
                f"task '{name}' is disabled", details={"reference": reference}
            )
        known = task_keys & SUPPORTED_TASKS
        if not known:
            raise UnsupportedWorkflowFeature(
                f"no supported task type found at {reference}", details={"keys": sorted(task_keys)}
            )
        if "call" in definition:
            call = definition["call"]
            if isinstance(call, str) and call not in SUPPORTED_CALLS:
                if not call.startswith("agent:") and not call.startswith("llm:"):
                    raise UnsupportedWorkflowFeature(
                        f"call '{call}' is not enabled", details={"reference": reference}
                    )
        for nested_key in ("switch", "for", "fork", "try"):
            if nested_key in definition:
                nested = _nested_task_lists(definition[nested_key], nested_key)
                for task_list in nested:
                    validate_capabilities({"do": task_list})


def compile_workflow(source: str | Path | Mapping[str, Any] | None = None) -> WorkflowPlan:
    workflow = generate_default_workflow() if source is None else load_workflow(source)
    validate_schema(workflow)
    validate_capabilities(workflow)
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
        if expression in (".", "$"):
            return data
        if expression.startswith("@"):
            return variables.get(expression[1:])
        if expression.startswith("."):
            return _path_get(data, expression[1:])
        if expression.startswith("$"):
            return _path_get(data, expression[1:].lstrip("."))
        if expression in variables:
            return variables[expression]
        if expression in {"true", "false", "null"}:
            return {"true": True, "false": False, "null": None}[expression]
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


@dataclass(slots=True)
class ExecutionState:
    data: Any
    outputs: dict[str, Any] = field(default_factory=dict)
    variables: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)


class WorkflowExecutor:
    """Engine-neutral execution of normalized portable plan semantics."""

    def __init__(self, catalog: FunctionCatalog, *, services: Any = None) -> None:
        self.catalog = catalog
        self.services = services
        self.expressions = ExpressionEvaluator()

    async def execute(
        self, plan: WorkflowPlan, input_data: Any, *, metadata: dict[str, Any] | None = None
    ) -> Any:
        state = ExecutionState(data=copy.deepcopy(input_data), context=metadata or {})
        await self._run_tasks(plan.source.get("do", []), state)
        output = plan.source.get("output")
        if isinstance(output, Mapping) and "as" in output:
            return self.expressions.evaluate(output["as"], state.data, variables=state.variables)
        return state.data

    async def _run_tasks(self, task_list: Iterable[Any], state: ExecutionState) -> None:
        for index, item in enumerate(task_list):
            if not isinstance(item, Mapping) or len(item) != 1:
                raise WorkflowExecutionError(f"invalid task at sequence index {index}")
            name, definition = next(iter(item.items()))
            if not isinstance(definition, Mapping):
                raise WorkflowExecutionError(f"task {name} definition must be an object")
            before = copy.deepcopy(state.data)
            try:
                result = await self._run_with_policy(str(name), definition, state)
            except Exception as exc:
                if isinstance(
                    exc, (WorkflowExecutionError, UnsupportedWorkflowFeature, ExpressionError)
                ):
                    raise
                raise WorkflowExecutionError(
                    f"task {name} failed: {exc}", details={"task": name}
                ) from exc
            state.outputs[str(name)] = result
            state.data = self._apply_task_data(definition, result, before, state)

    async def _run_with_policy(
        self, name: str, definition: Mapping[str, Any], state: ExecutionState
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
        for _attempt in range(max(1, attempts)):
            try:
                operation = self._run_task(name, definition, state)
                if timeout_seconds is not None:
                    return await asyncio.wait_for(operation, timeout_seconds)
                return await operation
            except Exception as exc:
                last_error = exc
        assert last_error is not None
        raise last_error

    async def _run_task(
        self, name: str, definition: Mapping[str, Any], state: ExecutionState
    ) -> Any:
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
        if "set" in definition:
            return self.expressions.evaluate(
                definition["set"], task_input, variables=state.variables
            )
        if "call" in definition:
            payload = definition.get("with", task_input)
            payload = self.expressions.evaluate(payload, state.data, variables=state.variables)
            return await self._call(str(definition["call"]), payload, state)
        if "switch" in definition:
            return await self._run_switch(definition["switch"], state)
        if "for" in definition:
            return await self._run_for(definition["for"], state)
        if "fork" in definition:
            return await self._run_fork(definition["fork"], state)
        if "try" in definition:
            try:
                await self._run_tasks(_task_body(definition["try"]), state)
                return state.data
            except Exception:
                catch = definition.get("catch") or definition.get("except")
                if catch is None:
                    raise
                await self._run_tasks(_task_body(catch), state)
                return state.data
        if "wait" in definition:
            wait_value = self.expressions.evaluate(
                definition["wait"], state.data, variables=state.variables
            )
            seconds = _duration_seconds(wait_value) or 0
            await asyncio.sleep(max(0.0, min(seconds, 300.0)))
            return state.data
        if "raise" in definition:
            message = self.expressions.evaluate(
                definition["raise"], state.data, variables=state.variables
            )
            raise WorkflowExecutionError(str(message))
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
            if "otherwise" in entry:
                otherwise = entry["otherwise"]
                continue
            condition = entry.get("when", entry.get("case", entry.get("if")))
            if self.expressions.condition(condition, state.data, variables=state.variables):
                body = entry.get("then", entry.get("do", []))
                await self._run_tasks(_task_body(body), state)
                return state.data
        if otherwise is not None:
            await self._run_tasks(_task_body(otherwise), state)
        return state.data

    async def _run_for(self, definition: Any, state: ExecutionState) -> Any:
        if not isinstance(definition, Mapping):
            raise WorkflowSemanticError("for must be an object")
        values = self.expressions.evaluate(
            definition.get("in", definition.get("over", [])), state.data, variables=state.variables
        )
        if values is None:
            return state.data
        variable = str(definition.get("each", definition.get("as", "item")))
        body = _task_body(definition.get("do", definition.get("body", [])))
        results: list[Any] = []
        for value in values:
            child = ExecutionState(
                copy.deepcopy(state.data),
                dict(state.outputs),
                dict(state.variables),
                dict(state.context),
            )
            child.variables[variable] = value
            await self._run_tasks(body, child)
            results.append(child.data)
        if definition.get("output") == "array":
            return results
        return results[-1] if results else state.data

    async def _run_fork(self, definition: Any, state: ExecutionState) -> Any:
        branches = definition.get("branches", []) if isinstance(definition, Mapping) else definition
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

        results = await asyncio.gather(*(run_branch(branch) for branch in branches))
        if results and all(isinstance(result, Mapping) for result in results):
            merged = dict(state.data) if isinstance(state.data, Mapping) else {}
            for result in results:
                merged.update(result)
            return merged
        return results

    def _apply_task_data(
        self, definition: Mapping[str, Any], result: Any, before: Any, state: ExecutionState
    ) -> Any:
        output = result
        output_spec = definition.get("output")
        if isinstance(output_spec, Mapping) and "as" in output_spec:
            output = self.expressions.evaluate(output_spec["as"], result, variables=state.variables)
        export_spec = definition.get("export")
        if isinstance(export_spec, Mapping) and "as" in export_spec:
            exported = self.expressions.evaluate(
                export_spec["as"], result, variables=state.variables
            )
            return _merge(before, exported)
        if isinstance(output, Mapping) and isinstance(before, Mapping):
            return _merge(before, output)
        return output


def _merge(left: Any, right: Any) -> Any:
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        merged = dict(left)
        merged.update(right)
        return merged
    return right


def _duration_seconds(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        value = value.get("after")
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lower()
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


def _nested_task_lists(value: Any, key: str) -> list[list[Any]]:
    if key == "switch":
        entries = (
            value
            if isinstance(value, list)
            else value.get("cases", [])
            if isinstance(value, Mapping)
            else []
        )
        return [
            _task_body(entry.get("then", entry.get("do", [])))
            for entry in entries
            if isinstance(entry, Mapping)
        ]
    if key == "fork":
        branches = value.get("branches", []) if isinstance(value, Mapping) else value
        return [
            _task_body(branch.get("do", branch) if isinstance(branch, Mapping) else branch)
            for branch in branches
        ]
    if key == "for" and isinstance(value, Mapping):
        return [_task_body(value.get("do", value.get("body", [])))]
    if key == "try" and isinstance(value, Mapping):
        return [_task_body(value)]
    return []


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
