"""Open Workflow loading, validation, normalization, expressions, and execution."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import math
import os
import re
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, NoReturn, cast
from uuid import uuid4

import yaml
from jsonschema import Draft202012Validator

from ._version import __version__
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
from .expressions import ExpressionEvaluator as ExpressionEvaluator
from .expressions import ExpressionMode, validate_expression_data
from .external_catalog import parse_catalog_function_reference
from .lifecycle import LifecycleControl
from .observability import EventSink, NullEventSink, WorkflowEvent
from .workflow_profile import SCHEMA_SHA256, WORKFLOW_DSL

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


class _WorkflowLoader(yaml.SafeLoader):
    """Reject ambiguous duplicate keys instead of silently overwriting tasks."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        self.flatten_mapping(node)
        result: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise WorkflowSchemaError("workflow mapping keys must be unique strings")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def _validate_json_value(
    value: Any, *, depth: int = 0, parents: frozenset[int] = frozenset()
) -> None:
    if depth > 64 or id(value) in parents:
        raise WorkflowSchemaError("workflow must be acyclic JSON data with depth at most 64")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise WorkflowSchemaError("workflow mapping keys must be strings")
            _validate_json_value(item, depth=depth + 1, parents=parents | {id(value)})
    elif isinstance(value, list):
        for item in value:
            _validate_json_value(item, depth=depth + 1, parents=parents | {id(value)})
    elif value is None or isinstance(value, (str, bool, int)):
        return
    elif isinstance(value, float) and math.isfinite(value):
        return
    else:
        raise WorkflowSchemaError("workflow values must be finite JSON data")


def load_workflow(source: str | Path | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(source, Mapping):
        _validate_json_value(source)
        return copy.deepcopy(dict(source))
    path = Path(source)
    try:
        raw = path.read_text(encoding="utf-8")
        loaded = yaml.load(raw, Loader=_WorkflowLoader)
    except OSError as exc:
        raise WorkflowSchemaError(f"unable to read workflow: {path}") from exc
    except (yaml.YAMLError, RecursionError) as exc:
        raise WorkflowSchemaError("invalid workflow YAML") from exc
    if not isinstance(loaded, dict):
        raise WorkflowSchemaError("workflow root must be an object")
    _validate_json_value(loaded)
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
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != SCHEMA_SHA256:
                raise WorkflowSchemaError("official schema does not match the reviewed SHA-256 pin")
            schema = yaml.safe_load(raw)
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
    _validate_json_value(workflow)
    validator = Draft202012Validator(_official_schema())
    errors = sorted(validator.iter_errors(workflow), key=lambda error: tuple(map(str, error.path)))
    if errors:
        first = errors[0]
        path = "/".join(str(item) for item in first.path) or "root"
        raise WorkflowSchemaError(f"schema validation failed at {path}: {first.message}")
    document = workflow.get("document")
    if not isinstance(document, dict) or document.get("dsl") != WORKFLOW_DSL:
        raise WorkflowSchemaError("only Open Workflow DSL 1.0.3 is supported")


def validate_capabilities(
    workflow: Mapping[str, Any],
    *,
    trusted_catalogs: Mapping[str, Any] | None = None,
    _prefix: str = "/do",
    _indexed: bool = True,
) -> None:
    _validate_definition_profile(workflow)
    _validate_catalog_capability(workflow, trusted_catalogs=trusted_catalogs)
    use = workflow.get("use")
    catalogs = use.get("catalogs") if isinstance(use, Mapping) else None
    _validate_schedule_capability(workflow)
    scope_names = {
        _pointer_name(reference.rsplit("/", 1)[-1])
        for reference, _ in _walk_tasks(workflow.get("do", []), prefix=_prefix, indexed=_indexed)
    }
    seen: set[str] = set()
    for reference, definition in _walk_tasks(
        workflow.get("do", []), prefix=_prefix, indexed=_indexed
    ):
        name = _pointer_name(reference.rsplit("/", 1)[-1])
        if name in seen:
            raise WorkflowSemanticError(
                "duplicate task name in scope", details={"reference": reference}
            )
        seen.add(name)
        if not isinstance(definition, Mapping):
            raise WorkflowSemanticError(f"task {reference} must be an object")
        _validate_data_profile(definition, reference)
        _validate_transitions(definition, reference, scope_names)
        if isinstance(definition.get("timeout"), str):
            _unsupported("named timeout", f"{reference}/timeout")
        raised = definition.get("raise")
        if isinstance(raised, Mapping) and isinstance(raised.get("error"), str):
            _unsupported("named error", f"{reference}/raise/error")
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
            if call in SUPPORTED_PROTOCOL_CALLS:
                _validate_protocol_authentication(definition.get("with"), f"{reference}/with")
            if call == "mcp":
                with_value = definition.get("with")
                transport = with_value.get("transport") if isinstance(with_value, Mapping) else None
                if isinstance(transport, Mapping) and "stdio" in transport:
                    raise UnsupportedWorkflowFeature(
                        "mcp stdio transport is not enabled", details={"reference": reference}
                    )
        for suffix, task_list in _task_scopes(definition):
            nested: dict[str, Any] = {"do": task_list}
            if isinstance(catalogs, Mapping):
                nested["use"] = {"catalogs": catalogs}
            validate_capabilities(
                nested,
                trusted_catalogs=trusted_catalogs,
                _prefix=f"{reference}/{suffix}",
                _indexed=not suffix.startswith("fork/branches/"),
            )
        if "emit" in definition:
            _validate_emit_capability(definition["emit"], reference)
        if "listen" in definition:
            _validate_listen_capability(definition["listen"], reference)
            if "foreach" in definition:
                _unsupported("listen foreach iteration", f"{reference}/foreach")
        if "run" in definition:
            _validate_run_capability(definition["run"], reference)
    if _prefix == "/do":
        validate_expression_profile(workflow)


def validate_expression_profile(workflow: Mapping[str, Any]) -> None:
    """Compile workflow-owned expression slots without interpreting literal code/schema."""
    evaluator = ExpressionEvaluator(
        mode=cast(ExpressionMode, workflow.get("evaluate", {}).get("mode", "strict"))
    )
    globals_ = {"workflow", "runtime"}
    ambient = globals_ | {"context", "event", "resume_input"}

    def check(value: Any, scope: set[str], reference: str) -> None:
        try:
            evaluator.validate(value, variables=scope)
        except ExpressionError as exc:
            raise ExpressionError(exc.message, details={"reference": reference}) from exc

    def binding(name: str, reference: str) -> str:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) or name in {
            "ENV",
            "input",
            "output",
            "context",
            "task",
            "workflow",
            "runtime",
            "authorization",
            "secrets",
            "event",
            "resume_input",
            "_task_reference",
            "_task_name",
        }:
            raise WorkflowSemanticError(
                "invalid or reserved expression binding", details={"reference": reference}
            )
        return name

    def data_slot(
        value: Mapping[str, Any], section: str, slot: str, scope: set[str], reference: str
    ) -> None:
        declaration = value.get(section)
        if isinstance(declaration, Mapping) and slot in declaration:
            check(declaration[slot], scope, f"{reference}/{section}/{slot}")

    def tasks(task_list: Any, scope: set[str], prefix: str, *, indexed: bool = True) -> None:
        for reference, definition in _walk_tasks(task_list, prefix=prefix, indexed=indexed):
            raw_scope = scope | {"task"}
            body_scope = raw_scope | {"input"}
            data_slot(definition, "input", "from", raw_scope, reference)
            data_slot(definition, "output", "as", body_scope, reference)
            data_slot(definition, "export", "as", body_scope | {"output"}, reference)
            check(definition.get("if"), raw_scope, f"{reference}/if")
            for key in ("set", "with", "wait"):
                if key in definition:
                    check(definition[key], body_scope, f"{reference}/{key}")
            loop = definition.get("for")
            loop_scope = scope
            if isinstance(loop, Mapping):
                item = binding(str(loop.get("each", "item")), f"{reference}/for/each")
                index = binding(str(loop.get("at", "index")), f"{reference}/for/at")
                if item == index:
                    raise WorkflowSemanticError(
                        "loop item and index bindings must differ", details={"reference": reference}
                    )
                loop_scope = scope | {item, index}
                check(loop.get("in"), body_scope, f"{reference}/for/in")
            check(definition.get("while"), body_scope | loop_scope, f"{reference}/while")
            for case_index, case in enumerate(definition.get("switch", [])):
                if isinstance(case, Mapping):
                    for name, case_definition in case.items():
                        if isinstance(case_definition, Mapping):
                            check(
                                case_definition.get("when"),
                                body_scope,
                                f"{reference}/switch/{case_index}/{_pointer_token(name)}/when",
                            )
            for key, member in (("emit", "event"), ("listen", "to"), ("raise", "error")):
                declaration = definition.get(key)
                if isinstance(declaration, Mapping):
                    check(declaration.get(member), body_scope, f"{reference}/{key}/{member}")
            run = definition.get("run")
            if isinstance(run, Mapping):
                for kind, configuration in run.items():
                    if not isinstance(configuration, Mapping):
                        continue
                    for slot in ("input", "stdin", "environment", "arguments"):
                        if slot in configuration:
                            check(configuration[slot], body_scope, f"{reference}/run/{kind}/{slot}")
            catch = definition.get("catch")
            catch_scope = scope
            if isinstance(catch, Mapping):
                name = binding(str(catch.get("as", "error")), f"{reference}/catch/as")
                catch_scope = scope | {name}
                for slot in ("when", "exceptWhen"):
                    check(catch.get(slot), body_scope | catch_scope, f"{reference}/catch/{slot}")
                retry = catch.get("retry")
                if isinstance(retry, Mapping):
                    for slot in ("when", "exceptWhen"):
                        check(
                            retry.get(slot),
                            body_scope | catch_scope,
                            f"{reference}/catch/retry/{slot}",
                        )
            for suffix, nested in _task_scopes(definition):
                child_scope = loop_scope if suffix == "do" and isinstance(loop, Mapping) else scope
                if suffix == "catch/do":
                    child_scope = catch_scope
                tasks(
                    nested,
                    child_scope,
                    f"{reference}/{suffix}",
                    indexed=not suffix.startswith("fork/branches/"),
                )

    data_slot(workflow, "input", "from", globals_, "")
    data_slot(workflow, "output", "as", globals_ | {"context"}, "")
    tasks(workflow.get("do", []), ambient, "/do")


def _unsupported(feature: str, reference: str) -> NoReturn:
    raise UnsupportedWorkflowFeature(
        f"{feature} is not supported by the current OWA profile",
        details={"reference": reference},
    )


def _validate_protocol_authentication(value: Any, reference: str) -> None:
    """Workflow authentication declarations are not deployment security profiles.

    Inspect protocol configuration, not application payloads, so a body field
    named authentication remains ordinary data. The common protocol client
    uses deployment-owned authentication and cannot interpret inline OWS
    endpoint policies; accepting them would silently make unauthenticated calls.
    """
    if not isinstance(value, Mapping):
        return
    for key, item in value.items():
        path = f"{reference}/{key}"
        if key == "authentication":
            _unsupported("workflow protocol authentication", path)
        if key not in {"body", "parameters", "arguments", "input", "message", "headers"}:
            _validate_protocol_authentication(item, path)


def _validate_definition_profile(workflow: Mapping[str, Any]) -> None:
    evaluation = workflow.get("evaluate", {})
    if isinstance(evaluation, Mapping):
        if evaluation.get("language", "jq") != "jq":
            _unsupported("expression language", "/evaluate/language")
        if evaluation.get("mode", "strict") not in {"strict", "loose"}:
            _unsupported("expression mode", "/evaluate/mode")
    use = workflow.get("use", {})
    if isinstance(use, Mapping):
        for key in (
            "authentications",
            "errors",
            "extensions",
            "functions",
            "retries",
            "secrets",
            "timeouts",
        ):
            if use.get(key):
                _unsupported(f"reusable {key}", f"/use/{key}")
    _validate_data_profile(workflow, "")
    timeout = workflow.get("timeout")
    if isinstance(timeout, str):
        _unsupported("named timeout", "/timeout")


def _validate_data_profile(definition: Mapping[str, Any], reference: str) -> None:
    for name in ("input", "output", "export"):
        spec = definition.get(name)
        schema = spec.get("schema") if isinstance(spec, Mapping) else None
        if not isinstance(schema, Mapping):
            continue
        path = f"{reference}/{name}/schema"
        if "resource" in schema:
            _unsupported("external data schema", f"{path}/resource")
        if schema.get("format", "json") not in {"json", "json:2020-12"}:
            _unsupported("data schema format", f"{path}/format")
        document = schema.get("document")
        if not isinstance(document, (Mapping, bool)):
            _unsupported("non-JSON data schema", f"{path}/document")
        # External references are not fetched by the inline validator. Local
        # fragments remain available without opening another network boundary.
        _validate_schema_references(document, f"{path}/document")
        try:
            Draft202012Validator.check_schema(document)
        except Exception as exc:
            raise WorkflowSemanticError(
                "invalid inline data schema", details={"reference": path}
            ) from exc


def _validate_schema_references(value: Any, reference: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            path = f"{reference}/{key}"
            if (
                key in {"$ref", "$dynamicRef"}
                and isinstance(item, str)
                and not item.startswith("#")
            ):
                _unsupported("external schema reference", path)
            _validate_schema_references(item, path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _validate_schema_references(item, f"{reference}/{index}")


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


async def resolve_and_compile_workflow(
    source: str | Path | Mapping[str, Any] | None = None,
    *,
    trusted_catalogs: Mapping[str, Any] | None = None,
    resolver: Any,
    catalog: FunctionCatalog,
) -> WorkflowPlan:
    """Resolve deployment-trusted catalogs before building the internal plan."""

    workflow = generate_default_workflow() if source is None else load_workflow(source)
    validate_schema(workflow)
    await resolver.resolve_workflow(workflow, catalog)
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
                reference=f"/do/{index}/{_pointer_token(str(task_name))}",
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


def _date_time_descriptor() -> dict[str, Any]:
    current = datetime.now(UTC)
    delta = current - datetime(1970, 1, 1, tzinfo=UTC)
    seconds = delta.days * 86400 + delta.seconds
    return {
        "iso8601": current.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "epoch": {"seconds": seconds, "milliseconds": seconds * 1000 + delta.microseconds // 1000},
    }


def _pointer_token(name: str) -> str:
    return name.replace("~", "~0").replace("/", "~1")


def _pointer_name(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def _validate_transitions(definition: Mapping[str, Any], reference: str, names: set[str]) -> None:
    def validate(target: Any, path: str) -> None:
        if target is not None and target not in {"continue", "exit", "end"} and target not in names:
            raise WorkflowSemanticError(
                "transition target must belong to the current scope",
                details={"reference": path, "target": target},
            )

    validate(definition.get("then"), f"{reference}/then")
    catch = definition.get("catch")
    if isinstance(catch, Mapping):
        validate(catch.get("then"), f"{reference}/catch/then")
    defaults = 0
    cases_seen: set[str] = set()
    for index, entry in enumerate(definition.get("switch", [])):
        for name, case in entry.items():
            path = f"{reference}/switch/{index}/{_pointer_token(name)}"
            if name in cases_seen:
                raise WorkflowSemanticError(
                    "duplicate switch case name", details={"reference": path}
                )
            cases_seen.add(name)
            defaults += "when" not in case
            validate(case.get("then"), f"{path}/then")
    if defaults > 1:
        raise WorkflowSemanticError(
            "switch must have at most one default case",
            details={"reference": f"{reference}/switch"},
        )


@dataclass(frozen=True, slots=True)
class _SkippedTask:
    data: Any


@dataclass(slots=True)
class _FlowControl:
    ended: asyncio.Event = field(default_factory=asyncio.Event)
    owners: tuple[asyncio.Task[Any], ...] = ()
    output: Any = None
    context: Any = None


@dataclass(slots=True)
class ExecutionState:
    data: Any
    outputs: dict[str, Any] = field(default_factory=dict)
    variables: dict[str, Any] = field(default_factory=dict)
    context: Any = field(default_factory=dict)
    jump_to: str | None = None
    stop: bool = False
    expressions: ExpressionEvaluator | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    scope_exit: bool = False
    transition_selected: bool = False
    flow: _FlowControl = field(default_factory=_FlowControl)
    branch_owners: tuple[asyncio.Task[Any], ...] = ()


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

    def _expressions(self, state: ExecutionState) -> ExpressionEvaluator:
        return state.expressions or self.expressions

    def _copy_state(self, state: ExecutionState) -> ExecutionState:
        child = ExecutionState(
            data=copy.deepcopy(state.data),
            outputs=copy.deepcopy(state.outputs),
            variables=copy.deepcopy(state.variables),
            context=copy.deepcopy(state.context),
            expressions=self._expressions(state),
            metadata=dict(state.metadata),
            flow=state.flow,
            branch_owners=state.branch_owners,
        )
        child.variables["context"] = child.context
        return child

    async def execute(
        self, plan: WorkflowPlan, input_data: Any, *, metadata: dict[str, Any] | None = None
    ) -> Any:
        workflow = plan.source
        metadata = dict(metadata or {})
        metadata.setdefault("_lifecycle", LifecycleControl())
        metadata.setdefault("invocation_id", str(uuid4()))
        metadata.setdefault("workflow_name", plan.name)
        metadata.setdefault("workflow_version", plan.version)
        event_context = {
            key: metadata.get(key)
            for key in (
                "invocation_id",
                "session_id",
                "workflow_name",
                "workflow_version",
                "engine",
                "parent_invocation_id",
                "parent_task_reference",
            )
        }
        started = time.perf_counter()
        self._emit("WorkflowStarted", {**event_context, "status": "running"})
        state = ExecutionState(
            data=copy.deepcopy(input_data),
            metadata=metadata,
            variables={
                "runtime": {
                    "name": "open-workflow-agent",
                    "version": __version__,
                    "metadata": {"engine": metadata.get("engine")},
                },
                "workflow": {
                    "id": metadata["invocation_id"],
                    "definition": workflow,
                    "input": copy.deepcopy(input_data),
                    "startedAt": _date_time_descriptor(),
                },
            },
            expressions=ExpressionEvaluator(
                mode=cast(ExpressionMode, workflow.get("evaluate", {}).get("mode", "strict"))
            ),
        )
        try:
            validate_expression_data(input_data)
            self._validate_data_schema(workflow.get("input"), input_data, "workflow input")
            input_spec = workflow.get("input")
            if isinstance(input_spec, Mapping) and "from" in input_spec:
                state.data = self._expressions(state).evaluate(
                    input_spec["from"], input_data, variables=state.variables
                )
            state.context = copy.deepcopy(state.data)
            state.variables["context"] = state.context
            workflow_timeout = _duration_seconds(workflow.get("timeout"))
            if workflow_timeout is None:
                await self._run_tasks(workflow.get("do", []), state)
            else:
                try:
                    await asyncio.wait_for(
                        self._run_tasks(workflow.get("do", []), state), workflow_timeout
                    )
                except TimeoutError as exc:
                    raise WorkflowExecutionError(
                        "workflow timed out", details={"timeout_seconds": workflow_timeout}
                    ) from exc
            output = workflow.get("output")
            output_variables = {
                key: value
                for key, value in state.variables.items()
                if key in {"context", "workflow", "runtime"}
            }
            result = (
                self._expressions(state).evaluate(
                    output["as"], state.data, variables=output_variables
                )
                if isinstance(output, Mapping) and "as" in output
                else copy.deepcopy(state.data)
            )
            validate_expression_data(result)
            self._validate_data_schema(output, result, "workflow output")
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
        self,
        task_list: Iterable[Any],
        state: ExecutionState,
        *,
        prefix: str = "/do",
        indexed: bool = True,
    ) -> None:
        tasks = list(task_list)
        index = 0
        while index < len(tasks):
            if state.flow.ended.is_set():
                state.stop = True
                return
            self._token(state).checkpoint()
            item = tasks[index]
            if not isinstance(item, Mapping) or len(item) != 1:
                raise WorkflowExecutionError(f"invalid task at sequence index {index}")
            name, definition = next(iter(item.items()))
            if not isinstance(definition, Mapping):
                raise WorkflowExecutionError(f"task {name} definition must be an object")
            task_prefix = f"{prefix}/{index}" if indexed else prefix
            reference = f"{task_prefix}/{_pointer_token(str(name))}"
            local = self._copy_state(state)
            for key in ("input", "output", "task"):
                local.variables.pop(key, None)
            local.variables.update(
                {
                    "_task_reference": reference,
                    "_task_name": str(name),
                    "task": {
                        "name": str(name),
                        "reference": reference,
                        "definition": copy.deepcopy(definition),
                        "input": copy.deepcopy(state.data),
                        "startedAt": _date_time_descriptor(),
                    },
                }
            )
            task_started = time.perf_counter()
            self._emit(
                "TaskStarted", {**self._task_event(reference, name, local), "status": "running"}
            )
            try:
                result = await self._run_with_policy(str(name), definition, local, reference)
                skipped = isinstance(result, _SkippedTask)
                output = (
                    copy.deepcopy(result.data)
                    if skipped
                    else self._apply_task_data(definition, result, local)
                )
            except asyncio.CancelledError:
                if state.flow.ended.is_set():
                    self._emit(
                        "TaskCancelled",
                        {
                            **self._task_event(reference, name, local),
                            "duration": time.perf_counter() - task_started,
                            "status": "cancelled",
                            "reason": "workflow_end",
                        },
                    )
                raise
            except InvocationCancelled as exc:
                self._emit(
                    "TaskCancelled",
                    {
                        **self._task_event(reference, name, local),
                        "duration": time.perf_counter() - task_started,
                        "status": "cancelled",
                        "reason": exc.details.get("reason", "cancelled"),
                        "error": _error_details(exc),
                    },
                )
                raise
            except Exception as exc:
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
                    exc.details.setdefault("instance", reference)
                self._emit(
                    "TaskFaulted",
                    {
                        **self._task_event(reference, name, local),
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
                    f"task {name} failed", details={"task": str(name)}
                ) from exc
            local.outputs[str(name)] = copy.deepcopy(output)
            state.outputs = local.outputs
            state.data = copy.deepcopy(output)
            state.context = copy.deepcopy(local.context)
            state.variables["context"] = state.context
            for key in ("event", "resume_input"):
                if key in local.variables:
                    state.variables[key] = copy.deepcopy(local.variables[key])
            state.jump_to, state.stop = local.jump_to, local.stop
            state.scope_exit = local.scope_exit
            state.transition_selected = local.transition_selected
            self._emit(
                "TaskCompleted",
                {
                    **self._task_event(reference, name, local),
                    "duration": time.perf_counter() - task_started,
                    "status": "completed",
                },
            )
            if not state.transition_selected and not state.stop:
                self._apply_transition(definition.get("then"), state)
            if state.stop:
                return
            if state.scope_exit:
                state.scope_exit = False
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
                        f"task transition target not found: {target}", details={"task": str(name)}
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
        raw_input = copy.deepcopy(state.data)
        for attempt in range(max(1, attempts)):
            state.data = copy.deepcopy(raw_input)
            state.variables.pop("input", None)
            state.variables.pop("output", None)
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
        task_reference = str(state.variables.get("_task_reference", "/do"))
        if "if" in definition and not self._expressions(state).condition(
            definition["if"], state.data, variables=state.variables
        ):
            return _SkippedTask(copy.deepcopy(state.data))
        raw_input = copy.deepcopy(state.data)
        input_spec = definition.get("input")
        self._validate_data_schema(input_spec, raw_input, f"task {name} input")
        task_input = (
            self._expressions(state).evaluate(
                input_spec["from"], raw_input, variables=state.variables
            )
            if isinstance(input_spec, Mapping) and "from" in input_spec
            else raw_input
        )
        state.data = copy.deepcopy(task_input)
        state.variables["input"] = copy.deepcopy(task_input)
        if "set" in definition:
            return self._expressions(state).evaluate(
                definition["set"], task_input, variables=state.variables
            )
        if "call" in definition:
            payload = (
                self._expressions(state).evaluate(
                    definition["with"], task_input, variables=state.variables
                )
                if "with" in definition
                else copy.deepcopy(task_input)
            )
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
            await self._run_tasks(
                _task_body(definition["do"]),
                state,
                prefix=f"{task_reference}/do",
            )
            return state.data
        if "try" in definition:
            catch = definition.get("catch") or definition.get("except")
            if catch is None:
                await self._run_tasks(
                    _task_body(definition["try"]),
                    state,
                    prefix=f"{task_reference}/try",
                )
                return state.data
            catch_retry = catch.get("retry") if isinstance(catch, Mapping) else None
            attempts = _retry_attempts(catch_retry)
            for attempt in range(attempts):
                try:
                    await self._run_tasks(
                        _task_body(definition["try"]),
                        state,
                        prefix=f"{task_reference}/try",
                    )
                    return state.data
                except Exception as exc:
                    if isinstance(exc, InvocationCancelled):
                        raise
                    error = _error_details(exc)
                    state.variables["error"] = error
                    if not _catch_matches(catch, error, state, self._expressions(state)):
                        raise
                    if attempt + 1 < attempts and _retry_allowed(
                        catch_retry, state, self._expressions(state)
                    ):
                        delay = _retry_delay(catch_retry, attempt)
                        if delay:
                            await self._token(state).sleep(delay)
                        failed_reference = str(
                            error.get("details", {}).get("instance", task_reference)
                        )
                        self._emit(
                            "TaskRetried",
                            {
                                **self._task_event(
                                    failed_reference,
                                    _pointer_name(failed_reference.rsplit("/", 1)[-1]),
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
                        state.variables[as_name] = _catch_error_value(error)
                    await self._run_tasks(
                        _task_body(catch.get("do", [])),
                        state,
                        prefix=f"{task_reference}/catch/do",
                    )
                    self._apply_transition(catch.get("then"), state)
                    return state.data
        if "wait" in definition:
            wait_value = self._expressions(state).evaluate(
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
            message = self._expressions(state).evaluate(
                message, state.data, variables=state.variables
            )
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
            metadata=state.metadata,
        )
        return await self.catalog.call(call, payload, context)

    async def _run_switch(self, switch: Any, state: ExecutionState) -> Any:
        default = None
        for entry in switch:
            case = next(iter(entry.values()))
            if "when" not in case:
                default = case
                continue
            if self._expressions(state).condition(
                case["when"], state.data, variables=state.variables
            ):
                self._apply_transition(case.get("then"), state)
                return state.data
        if default is not None:
            self._apply_transition(default.get("then"), state)
        return state.data

    async def _run_for(self, definition: Any, state: ExecutionState) -> Any:
        if not isinstance(definition, Mapping):
            raise WorkflowSemanticError("for must be an object")
        configuration = definition.get("for", definition)
        if not isinstance(configuration, Mapping):
            raise WorkflowSemanticError("for configuration must be an object")
        task_input = copy.deepcopy(state.data)
        values = self._expressions(state).evaluate(
            configuration.get("in", configuration.get("over", [])),
            state.data,
            variables=state.variables,
        )
        if not isinstance(values, list):
            raise WorkflowSemanticError("for.in must evaluate to an array")
        variable = str(configuration.get("each", configuration.get("as", "item")))
        body = _task_body(definition.get("do", definition.get("body", [])))
        results: list[Any] = []
        for index, value in enumerate(values):
            child = self._copy_state(state)
            child.variables[variable] = copy.deepcopy(value)
            child.variables[str(configuration.get("at", "index"))] = index
            if "while" in definition and not self._expressions(child).condition(
                definition["while"], task_input, variables=child.variables
            ):
                break
            await self._run_tasks(body, child, prefix=self._nested_prefix(child, "do"))
            state.data = child.data
            state.context = copy.deepcopy(child.context)
            state.variables["context"] = state.context
            results.append(child.data)
            if child.stop:
                state.stop = True
                break
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

        async def run_branch(index: int, branch: Any) -> Any:
            child = self._copy_state(state)
            owner = asyncio.current_task()
            assert owner is not None
            child.branch_owners += (owner,)
            await self._run_tasks(
                _task_body(branch),
                child,
                prefix=self._nested_prefix(child, f"fork/branches/{index}"),
                indexed=False,
            )
            if state.flow.ended.is_set() and owner in state.flow.owners:
                state.flow.output = copy.deepcopy(child.data)
                state.flow.context = copy.deepcopy(child.context)
            return child.data

        branch_tasks = [
            asyncio.create_task(run_branch(index, branch)) for index, branch in enumerate(branches)
        ]
        ending = asyncio.create_task(state.flow.ended.wait())
        joined = None if compete else asyncio.gather(*branch_tasks)
        try:
            if compete and not branch_tasks:
                return state.data
            watched: set[asyncio.Future[Any]]
            if compete:
                watched = set(branch_tasks)
            else:
                assert joined is not None
                watched = {joined}
            done, _ = await asyncio.wait(watched | {ending}, return_when=asyncio.FIRST_COMPLETED)
            if state.flow.ended.is_set():
                for branch_task in branch_tasks:
                    if branch_task not in state.flow.owners:
                        branch_task.cancel()
                terminal_results = await asyncio.gather(*branch_tasks, return_exceptions=True)
                for branch_task, terminal_result in zip(
                    branch_tasks, terminal_results, strict=True
                ):
                    if branch_task in state.flow.owners and isinstance(
                        terminal_result, BaseException
                    ):
                        raise terminal_result
                state.stop = True
                state.context = copy.deepcopy(state.flow.context)
                state.variables["context"] = state.context
                return copy.deepcopy(state.flow.output)
            if compete:
                return next(task for task in branch_tasks if task in done).result()
            assert joined is not None
            results = joined.result()
        finally:
            for pending in (*branch_tasks, ending):
                if not pending.done():
                    pending.cancel()
            await asyncio.gather(*branch_tasks, ending, return_exceptions=True)
            if joined is not None:
                await asyncio.gather(joined, return_exceptions=True)
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
        child_input = (
            self._expressions(state).evaluate(
                reference["input"], state.data, variables=state.variables
            )
            if "input" in reference
            else copy.deepcopy(state.data)
        )
        task_reference = str(state.variables.get("_task_reference", "unknown-task"))
        return await runner(plan, child_input, state.metadata, task_reference)

    async def _run_emit(self, definition: Any, state: ExecutionState) -> Any:
        if self.services is None or not hasattr(self.services, "event_bus"):
            raise WorkflowExecutionError("event service unavailable for emit task")
        if not isinstance(definition, Mapping):
            raise WorkflowSemanticError("emit configuration must be an object")
        event = definition.get("event")
        properties = event.get("with") if isinstance(event, Mapping) else None
        if not isinstance(properties, Mapping):
            raise WorkflowSemanticError("emit event.with must be an object")
        evaluated = self._expressions(state).evaluate(
            properties, state.data, variables=state.variables
        )
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
        strategy = self._expressions(state).evaluate(
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
        name = state.metadata.get("workflow_name", "workflow")
        version = state.metadata.get("workflow_version", "1.0.0")
        return f"urn:open-workflow-agent:{name}:{version}"

    @staticmethod
    def _apply_transition(transition: Any, state: ExecutionState) -> None:
        if transition is None:
            return
        state.transition_selected = True
        if transition == "continue":
            return
        if transition == "exit":
            state.scope_exit = True
            return
        if transition == "end":
            state.stop = True
            if not state.flow.ended.is_set():
                state.flow.output = copy.deepcopy(state.data)
                state.flow.context = copy.deepcopy(state.context)
                state.flow.owners = state.branch_owners
                state.flow.ended.set()
            return
        if isinstance(transition, str):
            state.jump_to = transition

    def _apply_task_data(
        self, definition: Mapping[str, Any], result: Any, state: ExecutionState
    ) -> Any:
        validate_expression_data(result)
        task = state.variables.get("task")
        if isinstance(task, dict):
            task["output"] = copy.deepcopy(result)
        output_spec = definition.get("output")
        output = (
            self._expressions(state).evaluate(output_spec["as"], result, variables=state.variables)
            if isinstance(output_spec, Mapping) and "as" in output_spec
            else copy.deepcopy(result)
        )
        self._validate_data_schema(output_spec, output, "task output")
        state.variables["output"] = copy.deepcopy(output)
        export_spec = definition.get("export")
        exported = (
            self._expressions(state).evaluate(export_spec["as"], output, variables=state.variables)
            if isinstance(export_spec, Mapping) and "as" in export_spec
            else copy.deepcopy(state.context)
        )
        self._validate_data_schema(export_spec, exported, "task export")
        state.context = copy.deepcopy(exported)
        state.variables["context"] = state.context
        return copy.deepcopy(output)

    def _validate_data_schema(self, specification: Any, value: Any, label: str) -> None:
        if not isinstance(specification, Mapping):
            return
        schema = specification.get("schema")
        if not isinstance(schema, Mapping):
            return
        document = schema.get("document")
        if not isinstance(document, (Mapping, bool)):
            return
        errors = sorted(
            Draft202012Validator(document).iter_errors(value),
            key=lambda error: tuple(str(part) for part in error.path),
        )
        if errors:
            first = errors[0]
            path = "/".join(str(item) for item in first.path) or "root"
            raise WorkflowSemanticError(
                f"{label} schema validation failed at {path}",
                details={
                    "path": path,
                    "validator": str(first.validator),
                    "type": "https://open-workflow-specification.org/spec/1.0.0/errors/validation",
                    "status": 400,
                },
            )

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        self.event_sink.emit(WorkflowEvent(event_type=event_type, **payload))

    def _control(self, state: ExecutionState) -> LifecycleControl | None:
        control = state.metadata.get("_lifecycle")
        return control if isinstance(control, LifecycleControl) else None

    @staticmethod
    def _nested_prefix(state: ExecutionState, suffix: str) -> str:
        parent = str(state.variables.get("_task_reference", "/do"))
        return f"{parent}/{suffix}"

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
        handle = state.metadata.get("_invocation_handle")
        if handle is not None and self.services is not None:
            self.services.invocations.update(handle, status=status)

    def _task_event(self, reference: str, name: Any, state: ExecutionState) -> dict[str, Any]:
        context = state.metadata
        return {
            "invocation_id": context.get("invocation_id"),
            "session_id": context.get("session_id"),
            "workflow_name": context.get("workflow_name"),
            "workflow_version": context.get("workflow_version"),
            "task_name": str(name),
            "task_reference": reference,
            "engine": context.get("engine"),
            "operation_id": f"{context.get('invocation_id', 'unknown-invocation')}:{reference}",
            "parent_invocation_id": context.get("parent_invocation_id"),
            "parent_task_reference": context.get("parent_task_reference"),
        }

    @staticmethod
    def _operation_id(state: ExecutionState) -> str:
        invocation = state.metadata.get("invocation_id", "unknown-invocation")
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
    if "correlate" in selected:
        _unsupported("event correlation", f"{reference}/listen/to/one/correlate")
    if "foreach" in definition:
        raise UnsupportedWorkflowFeature(
            "listen foreach iteration is not enabled", details={"reference": reference}
        )


def _validate_run_capability(definition: Any, reference: str) -> None:
    if not isinstance(definition, Mapping):
        raise WorkflowSemanticError(f"run configuration must be an object at {reference}")
    if definition.get("await", True) is not True:
        _unsupported("background run", f"{reference}/run/await")
    if "return" in definition:
        _unsupported("run return selection", f"{reference}/run/return")
    if "container" in definition:
        _unsupported("deployment-disabled container execution", f"{reference}/run/container")
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


def _catch_error_value(error: Mapping[str, Any]) -> dict[str, Any]:
    """Expose OWS error fields to catch handlers without changing API errors."""

    value = dict(error)
    details = value.pop("details", None)
    if isinstance(details, Mapping):
        value.update(details)
    return value


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


def _task_scopes(definition: Mapping[str, Any]) -> Iterable[tuple[str, list[Any]]]:
    for key in ("do", "try"):
        tasks = definition.get(key)
        if isinstance(tasks, list):
            yield key, tasks
    catch = definition.get("catch")
    if isinstance(catch, Mapping) and isinstance(catch.get("do"), list):
        yield "catch/do", catch["do"]
    fork = definition.get("fork")
    if isinstance(fork, Mapping) and isinstance(fork.get("branches"), list):
        for index, branch in enumerate(fork["branches"]):
            yield f"fork/branches/{index}", _task_body(branch)


def _walk_tasks(task_list: Any, *, prefix: str, indexed: bool = True) -> Iterable[tuple[str, Any]]:
    if not isinstance(task_list, list):
        return
    for index, item in enumerate(task_list):
        if not isinstance(item, Mapping):
            yield f"{prefix}/{index}", item
            continue
        for name, definition in item.items():
            task_prefix = f"{prefix}/{index}" if indexed else prefix
            reference = f"{task_prefix}/{_pointer_token(str(name))}"
            yield reference, definition
