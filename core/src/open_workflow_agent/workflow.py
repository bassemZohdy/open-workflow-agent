"""Open Workflow loading, validation, normalization, expressions, and execution."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import math
import os
import random
import re
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Any, NoReturn, Protocol, cast
from uuid import uuid4

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

if TYPE_CHECKING:
    from .a2a_client import A2ACardSelection
    from .external_recovery import HttpExternalActionRecovery

from ._version import __version__
from .catalog import CatalogContext, FunctionCatalog
from .child_suspension import ChildSuspension
from .definitions import definition_at_pointer, resolve_reusable_components
from .effects import (
    ChildInvocationDependency,
    EffectIdentity,
    EffectJournal,
    EffectReceipt,
    effect_request_digest,
)
from .errors import (
    AgentError,
    ExpressionError,
    InvocationCancelled,
    InvocationStateConflict,
    KnowledgeError,
    MemoryError,
    ModelError,
    OwaError,
    ScheduleValidationError,
    ToolError,
    UnsupportedWorkflowFeature,
    WorkflowExecutionError,
    WorkflowSchemaError,
    WorkflowSemanticError,
    _A2AOutcomeUncertain,
    _McpOutcomeUncertain,
)
from .event_matching import candidate_matcher, event_projection
from .events import EventEnvelope, EventProgress, EventRegistration
from .expressions import ExpressionEvaluator as ExpressionEvaluator
from .expressions import ExpressionMode, validate_expression_data
from .external_catalog import ResolvedCatalogFunction, parse_catalog_function_reference
from .forks import ForkDecision, ForkIdentity
from .lifecycle import LifecycleControl
from .observability import EventSink, NullEventSink, WorkflowEvent
from .ownership import ExecutionLease
from .persistence import ExecutionHandle
from .protocols import MCP_PROTOCOL_VERSION, PROTOCOL_DATA_FIELDS
from .resources import SchemaResource, materialize_schema_resources
from .resources import schema_registry as build_schema_registry
from .retry import RetryPolicy
from .timers import TimerSuspension
from .timing import DeadlineAnchor, RetrySchedule, clock_started_at, root_clock_identity
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
_SCHEMA_VALIDATION_CACHE_LIMIT = 32
_SCHEMA_VALIDATION_CACHE_BYTES = 1_048_576
_SCHEMA_VALIDATION_SUCCESSES: OrderedDict[tuple[type[Any], str, str], None] = OrderedDict()
_SCHEMA_VALIDATION_CACHE_LOCK = Lock()


@dataclass(slots=True)
class _DeadlineScope:
    deadline: asyncio.Timeout
    absolute_due: float | None = None
    unresolved_reference: str | None = None


_ACTIVE_TIMEOUTS: ContextVar[tuple[_DeadlineScope, ...]] = ContextVar(
    "owa_active_timeouts", default=()
)
_RECONCILE_EXPIRED_RETRY: ContextVar[bool] = ContextVar(
    "owa_reconcile_expired_retry", default=False
)

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


class _EffectRecoveryRequired(WorkflowExecutionError):
    """Private recovery failure that cannot safely enter task retry/catch routing."""


class _NoCancellationToken:
    def checkpoint(self) -> None:
        return None

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    async def await_operation(self, operation: Any) -> Any:
        return await operation


class _JsonBooleanLoader(yaml.SafeLoader):
    """Use YAML 1.2 booleans; OWS's `on` is an ordinary string key."""


_JsonBooleanLoader.yaml_implicit_resolvers = {
    key: [rule for rule in rules if rule[0] != "tag:yaml.org,2002:bool"]
    for key, rules in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_JsonBooleanLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)


class _WorkflowLoader(_JsonBooleanLoader):
    """Reject ambiguous duplicate keys instead of silently overwriting tasks."""

    def construct_document(self, node: yaml.Node) -> Any:
        budget = [0, 0]

        def check(value: yaml.Node, depth: int, parents: frozenset[int]) -> None:
            if depth > 64 or id(value) in parents:
                raise WorkflowSchemaError(
                    "workflow must be acyclic JSON data with depth at most 64"
                )
            budget[0] += 1
            if isinstance(value, yaml.ScalarNode):
                try:
                    budget[1] += len(value.value.encode("utf-8")) + 2
                except UnicodeEncodeError:
                    raise WorkflowSchemaError("workflow scalar must be valid UTF-8") from None
            else:
                budget[1] += 2
            if budget[0] > 100000 or budget[1] > 16000000:
                raise WorkflowSchemaError("workflow YAML expansion exceeds its node or byte limit")
            active = parents | {id(value)}
            if isinstance(value, yaml.MappingNode):
                for key, item in value.value:
                    check(key, depth + 1, active)
                    check(item, depth + 1, active)
            elif isinstance(value, yaml.SequenceNode):
                for item in value.value:
                    check(item, depth + 1, active)

        check(node, 0, frozenset())
        return super().construct_document(node)

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
    value: Any,
    *,
    depth: int = 0,
    parents: frozenset[int] = frozenset(),
    _budget: list[int] | None = None,
) -> None:
    budget = [0, 0] if _budget is None else _budget
    budget[0] += 1
    if isinstance(value, (Mapping, list)):
        budget[1] += 2
    else:
        try:
            budget[1] += len(json.dumps(value, ensure_ascii=False).encode("utf-8")) + 1
        except (TypeError, ValueError, UnicodeEncodeError):
            raise WorkflowSchemaError(
                "workflow value cannot be serialized as finite UTF-8 JSON"
            ) from None
    if budget[0] > 100000 or budget[1] > 16000000:
        raise WorkflowSchemaError("workflow JSON expansion exceeds its node or byte limit")
    if depth > 64 or id(value) in parents:
        raise WorkflowSchemaError("workflow must be acyclic JSON data with depth at most 64")
    if isinstance(value, Mapping):
        for key, item in value.items():
            budget[0] += 1
            if not isinstance(key, str):
                raise WorkflowSchemaError("workflow mapping keys must be strings")
            try:
                budget[1] += len(json.dumps(key, ensure_ascii=False).encode("utf-8")) + 2
            except UnicodeEncodeError:
                raise WorkflowSchemaError("workflow keys must be valid UTF-8 strings") from None
            _validate_json_value(
                item, depth=depth + 1, parents=parents | {id(value)}, _budget=budget
            )
    elif isinstance(value, list):
        for item in value:
            _validate_json_value(
                item, depth=depth + 1, parents=parents | {id(value)}, _budget=budget
            )
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
    except OSError as exc:
        raise WorkflowSchemaError(f"unable to read workflow: {path}") from exc
    return load_workflow_text(raw)


def load_json_value(raw: str) -> Any:
    """Load JSON-compatible YAML without ambiguous keys or native objects."""
    try:
        if len(raw.encode("utf-8")) > 16000000:
            raise WorkflowSchemaError("workflow source exceeds its byte limit")
    except UnicodeEncodeError:
        raise WorkflowSchemaError("workflow source must be valid UTF-8") from None
    try:
        loaded = yaml.load(raw, Loader=_WorkflowLoader)
    except (yaml.YAMLError, RecursionError, ValueError):
        raise WorkflowSchemaError("invalid workflow YAML") from None
    _validate_json_value(loaded)
    return loaded


def load_workflow_text(raw: str) -> dict[str, Any]:
    """Parse workflow/resource text with the same safe JSON and ambiguity rules."""
    loaded = load_json_value(raw)
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
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != SCHEMA_SHA256:
                raise WorkflowSchemaError("official schema does not match the reviewed SHA-256 pin")
            schema = yaml.load(raw, Loader=_JsonBooleanLoader)
        except (OSError, yaml.YAMLError) as exc:
            raise WorkflowSchemaError(f"unable to load official schema: {path}") from exc
        if not isinstance(schema, dict):
            raise WorkflowSchemaError(f"official schema must be an object: {path}")
        return cast(dict[str, Any], schema)
    raise WorkflowSchemaError(
        "official Open Workflow 1.0.3 schema is unavailable; expected "
        f"{OFFICIAL_SCHEMA_RELATIVE_PATH}"
    )


def _schema_validation_cache_key(
    workflow: Mapping[str, Any], schema: Mapping[str, Any]
) -> tuple[type[Any], str, str] | None:
    """Memoize only exact built-in JSON values, never coercible Python data."""

    def strict(value: Any) -> bool:
        if type(value) is dict:
            return all(type(key) is str and strict(item) for key, item in value.items())
        if type(value) is list:
            return all(strict(item) for item in value)
        return value is None or type(value) in (str, bool, int, float)

    if not strict(workflow):
        return None
    source = json.dumps(workflow, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(source.encode("utf-8")) > _SCHEMA_VALIDATION_CACHE_BYTES:
        return None
    official = json.dumps(schema, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return Draft202012Validator, official, source


def validate_schema(workflow: Mapping[str, Any]) -> None:
    _validate_json_value(workflow)
    schema = _official_schema()
    key = _schema_validation_cache_key(workflow, schema)
    with _SCHEMA_VALIDATION_CACHE_LOCK:
        cached = key is not None and key in _SCHEMA_VALIDATION_SUCCESSES
        if cached and key is not None:
            _SCHEMA_VALIDATION_SUCCESSES.move_to_end(key)
    if not cached:
        validator = Draft202012Validator(schema)
        errors = sorted(
            validator.iter_errors(workflow), key=lambda error: tuple(map(str, error.path))
        )
        if errors:
            first = errors[0]
            path = "/".join(str(item) for item in first.path) or "root"
            raise WorkflowSchemaError(f"schema validation failed at {path}: {first.message}")
    document = workflow.get("document")
    if not isinstance(document, dict) or document.get("dsl") != WORKFLOW_DSL:
        raise WorkflowSchemaError("only Open Workflow DSL 1.0.3 is supported")
    if key is not None:
        with _SCHEMA_VALIDATION_CACHE_LOCK:
            _SCHEMA_VALIDATION_SUCCESSES[key] = None
            _SCHEMA_VALIDATION_SUCCESSES.move_to_end(key)
            while len(_SCHEMA_VALIDATION_SUCCESSES) > _SCHEMA_VALIDATION_CACHE_LIMIT:
                _SCHEMA_VALIDATION_SUCCESSES.popitem(last=False)


def validate_capabilities(
    workflow: Mapping[str, Any],
    *,
    trusted_catalogs: Mapping[str, Any] | None = None,
    _prefix: str = "/do",
    _indexed: bool = True,
    _resource_preflight: bool = False,
    _schema_registry: Registry[Any] | None = None,
    _mcp_stdio: bool = False,
) -> None:
    _validate_definition_profile(workflow, _resource_preflight, _schema_registry)
    _validate_catalog_capability(workflow, trusted_catalogs=trusted_catalogs)
    use = workflow.get("use")
    catalogs = use.get("catalogs") if isinstance(use, Mapping) else None
    functions = use.get("functions", {}) if isinstance(use, Mapping) else {}
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
        _validate_data_profile(definition, reference, _resource_preflight, _schema_registry)
        _validate_transitions(definition, reference, scope_names)
        if isinstance(definition.get("timeout"), str):
            _unsupported("named timeout", f"{reference}/timeout")
        catch = definition.get("catch")
        if isinstance(catch, Mapping) and isinstance(catch.get("retry"), Mapping):
            RetryPolicy.from_value(catch["retry"], duration=_duration_seconds)
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
                and call not in functions
                and not _is_trusted_catalog_call(call, catalogs, trusted_catalogs)
            ):
                raise UnsupportedWorkflowFeature(
                    f"call '{call}' is not enabled", details={"reference": reference}
                )
            if call in SUPPORTED_PROTOCOL_CALLS:
                _validate_protocol_authentication(definition.get("with"), f"{reference}/with")
            if call == "mcp":
                with_value = definition.get("with")
                protocol_version = (
                    with_value.get("protocolVersion") if isinstance(with_value, Mapping) else None
                )
                if (
                    protocol_version is not None
                    and protocol_version != MCP_PROTOCOL_VERSION
                    and not (
                        isinstance(protocol_version, str)
                        and protocol_version.strip().startswith("${")
                    )
                ):
                    raise UnsupportedWorkflowFeature(
                        "MCP protocol version is not enabled", details={"reference": reference}
                    )
                transport = with_value.get("transport") if isinstance(with_value, Mapping) else None
                if isinstance(transport, Mapping) and "stdio" in transport and not _mcp_stdio:
                    raise UnsupportedWorkflowFeature(
                        "mcp stdio transport is not enabled", details={"reference": reference}
                    )
        for suffix, task_list in _task_scopes(definition):
            nested: dict[str, Any] = {"do": task_list}
            if isinstance(use, Mapping):
                nested["use"] = dict(use)
            validate_capabilities(
                nested,
                trusted_catalogs=trusted_catalogs,
                _prefix=f"{reference}/{suffix}",
                _indexed=not suffix.startswith("fork/branches/"),
                _resource_preflight=_resource_preflight,
                _schema_registry=_schema_registry,
                _mcp_stdio=_mcp_stdio,
            )
        if "emit" in definition:
            _validate_emit_capability(definition["emit"], reference)
        if "listen" in definition:
            _validate_listen_capability(definition["listen"], reference)
        if "run" in definition:
            _validate_run_capability(definition["run"], reference)
    if _prefix == "/do":
        for name, definition in functions.items():
            validate_capabilities(
                {"do": [{name: definition}], "use": dict(cast(Mapping[str, Any], use))},
                trusted_catalogs=trusted_catalogs,
                _prefix="/use/functions",
                _indexed=False,
                _resource_preflight=_resource_preflight,
                _schema_registry=_schema_registry,
                _mcp_stdio=_mcp_stdio,
            )
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
            "_execution_reference",
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
            iterator = definition.get("foreach") if "listen" in definition else None
            iterator_scope = scope
            if isinstance(iterator, Mapping):
                item = binding(str(iterator.get("item", "item")), f"{reference}/foreach/item")
                index = binding(str(iterator.get("at", "index")), f"{reference}/foreach/at")
                if item == index:
                    raise WorkflowSemanticError(
                        "subscription item and index bindings must differ",
                        details={"reference": reference},
                    )
                iterator_scope = body_scope | {item, index}
                data_slot(iterator, "output", "as", iterator_scope, f"{reference}/foreach")
                data_slot(
                    iterator,
                    "export",
                    "as",
                    iterator_scope | {"output"},
                    f"{reference}/foreach",
                )
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
                if suffix == "foreach/do":
                    child_scope = iterator_scope
                tasks(
                    nested,
                    child_scope,
                    f"{reference}/{suffix}",
                    indexed=not suffix.startswith("fork/branches/"),
                )

    data_slot(workflow, "input", "from", globals_, "")
    data_slot(workflow, "output", "as", globals_ | {"context"}, "")
    schedule = workflow.get("schedule")
    if isinstance(schedule, Mapping) and "on" in schedule:
        check(schedule["on"], globals_ | {"context", "event"}, "/schedule/on")
    tasks(workflow.get("do", []), ambient, "/do")
    for name, definition in workflow.get("use", {}).get("functions", {}).items():
        tasks([{name: definition}], ambient, "/use/functions", indexed=False)


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


def _validate_definition_profile(
    workflow: Mapping[str, Any],
    resource_preflight: bool = False,
    registry: Registry[Any] | None = None,
) -> None:
    evaluation = workflow.get("evaluate", {})
    if isinstance(evaluation, Mapping):
        if evaluation.get("language", "jq") != "jq":
            _unsupported("expression language", "/evaluate/language")
        if evaluation.get("mode", "strict") not in {"strict", "loose"}:
            _unsupported("expression mode", "/evaluate/mode")
    use = workflow.get("use", {})
    if isinstance(use, Mapping):
        for name in use.get("functions", {}):
            if (
                name in SUPPORTED_CALLS | {"grpc", "asyncapi"}
                or parse_catalog_function_reference(name) is not None
            ):
                raise WorkflowSemanticError(
                    "reusable functions cannot shadow protocol or catalog calls",
                    details={"reference": f"/use/functions/{_pointer_token(name)}"},
                )
        for key in (
            "authentications",
            "extensions",
            "secrets",
        ):
            if use.get(key):
                _unsupported(f"reusable {key}", f"/use/{key}")
    _validate_data_profile(workflow, "", resource_preflight, registry)
    timeout = workflow.get("timeout")
    if isinstance(timeout, str):
        _unsupported("named timeout", "/timeout")


def _validate_data_profile(
    definition: Mapping[str, Any],
    reference: str,
    resource_preflight: bool = False,
    registry: Registry[Any] | None = None,
) -> None:
    for name in ("input", "output", "export"):
        spec = definition.get(name)
        schema = spec.get("schema") if isinstance(spec, Mapping) else None
        if not isinstance(schema, Mapping):
            continue
        path = f"{reference}/{name}/schema"
        if schema.get("format", "json") not in {"json", "json:2020-12"}:
            _unsupported("data schema format", f"{path}/format")
        if "resource" in schema:
            if not resource_preflight:
                _unsupported("external data schema", f"{path}/resource")
            from .resources import resource_uri

            resource_uri(schema["resource"])
            continue
        document = schema.get("document")
        if not isinstance(document, (Mapping, bool)):
            _unsupported("non-JSON data schema", f"{path}/document")
        # External references are not fetched by the inline validator. Local
        # fragments remain available without opening another network boundary.
        try:
            Draft202012Validator.check_schema(document)
        except Exception as exc:
            raise WorkflowSemanticError(
                "invalid inline data schema", details={"reference": path}
            ) from exc
        _validate_schema_references(document, f"{path}/document", registry, resource_preflight)


def _validate_schema_references(
    value: Any,
    reference: str,
    registry: Registry[Any] | None = None,
    allow_external: bool = False,
) -> None:
    # Traverse schema resources, rather than arbitrary JSON values: const,
    # examples and defaults may contain ordinary data fields named $ref.
    locations: dict[int, str] = {}

    def locate(item: Any, path: str) -> None:
        locations[id(item)] = path
        if isinstance(item, Mapping):
            for key, child in item.items():
                locate(child, f"{path}/{_pointer_token(str(key))}")
        elif isinstance(item, list):
            for index, child in enumerate(item):
                locate(child, f"{path}/{index}")

    locate(value, reference)
    root = DRAFT202012.create_resource(value)
    resolver = (registry or Registry()).resolver_with_root(root)

    def walk(resource: Resource[Any], current: Any) -> None:
        document = resource.contents
        if not isinstance(document, Mapping):
            return
        path = locations[id(document)]
        if (
            document.get("$schema", "https://json-schema.org/draft/2020-12/schema").rstrip("#")
            != "https://json-schema.org/draft/2020-12/schema"
        ):
            _unsupported("inline schema dialect", f"{path}/$schema")
        for key in ("$ref", "$dynamicRef"):
            item = document.get(key)
            if not isinstance(item, str):
                continue
            try:
                target = current.lookup(item).contents
                if not isinstance(target, (Mapping, bool)):
                    raise ValueError("reference target is not a schema")
            except Exception:
                if not item.startswith("#"):
                    if allow_external:
                        continue
                    _unsupported("external schema reference", f"{path}/{key}")
                raise WorkflowSemanticError(
                    "unresolved or invalid inline schema reference",
                    details={"reference": f"{path}/{key}"},
                ) from None
        for child in resource.subresources():
            walk(child, current.in_subresource(child))

    walk(root, resolver)


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
    from .schedule_profile import parse_schedule

    schedule = workflow.get("schedule")
    if schedule is None:
        return
    try:
        parse_schedule(schedule)
    except ScheduleValidationError as exc:
        raise WorkflowSemanticError(exc.message, details={"reference": "/schedule"}) from exc


def compile_workflow(
    source: str | Path | Mapping[str, Any] | None = None,
    *,
    trusted_catalogs: Mapping[str, Any] | None = None,
) -> WorkflowPlan:
    workflow = generate_default_workflow() if source is None else load_workflow(source)
    validate_schema(workflow)
    validate_capabilities(resolve_reusable_components(workflow), trusted_catalogs=trusted_catalogs)
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
    enabled = bool(getattr(getattr(resolver, "schemas", None), "enabled", False))
    validate_capabilities(
        resolve_reusable_components(workflow),
        trusted_catalogs=trusted_catalogs,
        _resource_preflight=enabled,
    )
    imports, resources = await resolve_workflow_dependencies(workflow, resolver, catalog)
    effective = (
        materialize_schema_resources(resolve_reusable_components(workflow))
        if resources
        else resolve_reusable_components(workflow)
    )
    validate_capabilities(
        effective,
        trusted_catalogs=trusted_catalogs,
        _schema_registry=build_schema_registry(resources),
    )
    return normalize_workflow(workflow, imports=imports, resources=resources)


async def resolve_workflow_dependencies(
    workflow: Mapping[str, Any], resolver: Any, catalog: FunctionCatalog
) -> tuple[tuple[ResolvedCatalogFunction, ...], tuple[SchemaResource, ...]]:
    enabled = bool(getattr(getattr(resolver, "schemas", None), "enabled", False))
    resolve_functions = getattr(resolver, "resolve_functions", None)
    if callable(resolve_functions):
        imports = (
            await resolve_functions(workflow, catalog, _resource_preflight=True)
            if enabled
            else await resolve_functions(workflow, catalog)
        )
    else:
        await resolver.resolve_workflow(workflow, catalog)
        imports = ()
    resources = await resolver.schemas.resolve(workflow, imports) if enabled else ()
    if resources:
        imports = tuple(
            replace(
                item,
                execution_definition_json=json.dumps(
                    materialize_schema_resources(
                        {"do": [{item.reference: item.execution_definition}]}
                    )["do"][0][item.reference],
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                ),
            )
            for item in imports
        )
    return tuple(imports), tuple(resources)


@dataclass(frozen=True, slots=True)
class TaskPlan:
    name: str
    reference: str
    kind: str
    definition_json: str
    execution_reference: str | None = None

    @property
    def definition(self) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads(self.definition_json))


@dataclass(frozen=True, slots=True)
class ScopePlan:
    reference: str
    indexed: bool
    tasks: tuple[TaskPlan, ...]


@dataclass(frozen=True, slots=True)
class WorkflowPlan:
    namespace: str
    name: str
    version: str
    dsl: str
    source_json: str
    tasks: tuple[TaskPlan, ...]
    scopes: tuple[ScopePlan, ...] = ()
    execution_json: str | None = None
    imports: tuple[ResolvedCatalogFunction, ...] = ()
    resources: tuple[SchemaResource, ...] = ()

    @property
    def source(self) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads(self.source_json))

    @property
    def execution_source(self) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads(self.execution_json or self.source_json))

    @property
    def uses_timing(self) -> bool:
        if self.execution_source.get("timeout") is not None:
            return True
        for scope in self.scopes:
            for task in scope.tasks:
                definition = task.definition
                catch = definition.get("catch")
                if definition.get("timeout") is not None or (
                    isinstance(catch, Mapping) and catch.get("retry") is not None
                ):
                    return True
        return False

    @property
    def uses_durable_events(self) -> bool:
        return any(
            "listen" in task.definition or "emit" in task.definition
            for scope in self.scopes
            for task in scope.tasks
        )

    @property
    def fingerprint(self) -> str:
        import hashlib

        content = self.source_json
        if self.imports or self.resources:
            content = json.dumps(
                {
                    "source": self.source,
                    "imports": [
                        {
                            "reference": item.reference,
                            "resource": item.resource_uri,
                            "digest": item.digest,
                            "definition": item.definition,
                            "executionDefinition": item.execution_definition,
                        }
                        for item in self.imports
                    ],
                    **(
                        {
                            "resources": [
                                {"uri": item.uri, "digest": item.digest, "document": item.document}
                                for item in self.resources
                            ]
                        }
                        if self.resources
                        else {}
                    ),
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        return hashlib.sha256(content.encode("utf-8")).hexdigest()


def normalize_workflow(
    workflow: Mapping[str, Any],
    *,
    imports: tuple[ResolvedCatalogFunction, ...] = (),
    resources: tuple[SchemaResource, ...] = (),
) -> WorkflowPlan:
    _validate_json_value(workflow)
    effective = resolve_reusable_components(workflow)
    if resources:
        effective = materialize_schema_resources(effective)
    functions = effective.get("use", {}).get("functions", {})
    imported_functions = {item.reference: item.execution_definition for item in imports}
    document = workflow["document"]
    scopes: list[ScopePlan] = []

    def scope(
        task_list: list[Any],
        reference: str,
        *,
        indexed: bool = True,
        source_prefix: str | None = None,
        stack: tuple[str, ...] = (),
        source_override: str | None = None,
    ) -> ScopePlan:
        tasks: list[TaskPlan] = []
        children: list[tuple[str, list[Any], str, bool, tuple[str, ...], str | None]] = []
        for index, item in enumerate(task_list):
            if not isinstance(item, Mapping) or len(item) != 1:
                raise WorkflowSemanticError(
                    f"task at {reference}/{index} must have exactly one name"
                )
            task_name, definition = next(iter(item.items()))
            if not isinstance(definition, Mapping):
                raise WorkflowSemanticError(f"task {task_name} definition must be an object")
            prefix = f"{reference}/{index}" if indexed else reference
            task_reference = f"{prefix}/{_pointer_token(str(task_name))}"
            source_start = f"{source_prefix}/{index}" if indexed else source_prefix
            source_reference = (
                f"{source_start}/{_pointer_token(str(task_name))}"
                if source_prefix is not None
                else task_reference
            )
            if source_override is not None:
                source_reference = source_override
            kind = next(
                (
                    candidate
                    for candidate in SUPPORTED_TASKS
                    if candidate in definition and (candidate != "do" or "for" not in definition)
                ),
                "unknown",
            )
            tasks.append(
                TaskPlan(
                    name=str(task_name),
                    reference=source_reference,
                    kind=kind,
                    definition_json=json.dumps(definition, sort_keys=True, separators=(",", ":")),
                    execution_reference=task_reference
                    if source_reference != task_reference
                    else None,
                )
            )
            children.extend(
                (
                    f"{task_reference}/{suffix}",
                    nested,
                    f"{source_reference}/{suffix}",
                    not suffix.startswith("fork/branches/"),
                    stack,
                    None,
                )
                for suffix, nested in _task_scopes(definition)
            )
            call = definition.get("call")
            if isinstance(call, str) and call in functions:
                if call in stack or len(stack) >= 32:
                    raise WorkflowSemanticError(
                        "recursive reusable function references are not enabled",
                        details={"reference": f"{source_reference}/call"},
                    )
                children.append(
                    (
                        f"{task_reference}/call",
                        [{call: functions[call]}],
                        "/use/functions",
                        False,
                        (*stack, call),
                        None,
                    )
                )
            elif isinstance(call, str) and call in imported_functions:
                children.append(
                    (
                        f"{task_reference}/call",
                        [{call: imported_functions[call]}],
                        reference,
                        False,
                        stack,
                        source_reference,
                    )
                )
        result = ScopePlan(reference=reference, indexed=indexed, tasks=tuple(tasks))
        scopes.append(result)
        if sum(len(item.tasks) for item in scopes) > 10000:
            raise WorkflowSemanticError("reusable task expansion exceeds 10000 tasks")
        for (
            child_reference,
            nested,
            child_source,
            child_indexed,
            child_stack,
            child_override,
        ) in children:
            scope(
                nested,
                child_reference,
                indexed=child_indexed,
                source_prefix=child_source,
                stack=child_stack,
                source_override=child_override,
            )
        return result

    root = scope(effective["do"], "/do")
    source_json = json.dumps(workflow, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return WorkflowPlan(
        namespace=str(document["namespace"]),
        name=str(document["name"]),
        version=str(document["version"]),
        dsl=str(document["dsl"]),
        source_json=source_json,
        tasks=root.tasks,
        scopes=tuple(scopes),
        execution_json=json.dumps(
            effective, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ),
        imports=imports,
        resources=resources,
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


class ScopeExecutor(Protocol):
    async def run_scope(
        self,
        executor: WorkflowExecutor,
        task_list: list[Any],
        state: ExecutionState,
        *,
        prefix: str,
        indexed: bool,
    ) -> None: ...


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
    scope_executor: ScopeExecutor | None = None
    task_visits: dict[str, int] = field(default_factory=dict)
    execution_path: tuple[tuple[str, int], ...] = ()


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
        self._policy_clock: Callable[[], float] = time.monotonic
        self._absolute_clock: Callable[[], float] = time.time
        self._retry_random: Callable[[float, float], float] = random.uniform

    async def _record_policy(
        self,
        state: ExecutionState,
        identity: EffectIdentity,
        payload: Any,
        create: Callable[[], Any],
    ) -> Any:
        journal = getattr(self.services, "effect_journal", None)
        if _RECONCILE_EXPIRED_RETRY.get() and (journal is None or journal.get(identity) is None):
            raise WorkflowExecutionError("retry budget expired before new policy work")
        if journal is None:
            return create()
        try:
            receipt = journal.record_value(
                identity,
                effect_request_digest(identity.operation, payload),
                create,
                execution_lease=self._execution_lease(state),
            )
        except InvocationStateConflict:
            raise
        except WorkflowExecutionError as exc:
            raise _EffectRecoveryRequired(exc.message, details=exc.details) from exc
        await self._notify_receipt(state, identity, receipt)
        return copy.deepcopy(receipt.output)

    async def _retry_clock(
        self,
        state: ExecutionState,
        execution_path: tuple[tuple[str, int], ...],
        policy: RetryPolicy,
    ) -> float:
        clock_state = self._copy_state(state)
        clock_state.execution_path = execution_path
        raw = await self._record_policy(
            clock_state,
            self._effect_identity(clock_state, "policy:retry-clock:v1"),
            {
                "duration": policy.duration,
                "attempt_duration": policy.attempt_duration,
                "count": policy.max_retries,
            },
            lambda: {"profile": "owa/timing/v1", "started_at": self._absolute_clock()},
        )
        return clock_started_at(raw)

    async def _run_retry_attempt(
        self,
        definition: Mapping[str, Any],
        state: ExecutionState,
        policy: RetryPolicy,
        attempt: int,
        started: float | None,
    ) -> None:
        timeout: float | None = None
        absolute_due: float | None = None
        admitted = False
        if attempt:
            identity = self._effect_identity(state, "policy:retry-attempt:v1")
            journal = getattr(self.services, "effect_journal", None)
            admitted = journal is not None and journal.get(identity) is not None
            remaining = (
                policy.duration - (self._absolute_clock() - started)
                if started is not None and policy.duration is not None
                else None
            )
            if not admitted and remaining is not None and remaining <= 0:
                raise WorkflowExecutionError("retry budget expired before another attempt")
            raw = await self._record_policy(
                state,
                identity,
                {"attempt": attempt},
                lambda: {"profile": "owa/timing/v1", "started_at": self._absolute_clock()},
            )
            attempt_started = clock_started_at(raw)
            timeout = policy.attempt_duration
            if timeout is not None:
                absolute_due = attempt_started + timeout
                timeout -= self._absolute_clock() - attempt_started
            if remaining is not None:
                aggregate_due = cast(float, started) + cast(float, policy.duration)
                absolute_due = (
                    min(absolute_due, aggregate_due) if absolute_due is not None else aggregate_due
                )
                timeout = min(timeout, remaining) if timeout is not None else remaining
        reconcile = admitted and timeout is not None and timeout <= 0
        token = _RECONCILE_EXPIRED_RETRY.set(_RECONCILE_EXPIRED_RETRY.get() or reconcile)
        try:
            with self._operation_visit_scope(state):
                await self._run_timed(
                    lambda: self._run_tasks(
                        _task_body(definition["try"]),
                        state,
                        prefix=self._nested_prefix(state, "try"),
                    ),
                    None if reconcile else timeout,
                    "retry attempt timed out",
                    {
                        "instance": state.variables.get("_task_reference"),
                        "retry_attempt": attempt,
                        "timeout_seconds": timeout,
                    },
                    absolute_due=absolute_due,
                    state=state,
                )
        finally:
            _RECONCILE_EXPIRED_RETRY.reset(token)

    async def _deadline_remaining(
        self,
        state: ExecutionState,
        seconds: float | None,
        *,
        workflow: bool = False,
    ) -> float | None:
        if seconds is None:
            state.metadata["_latest_deadline_at"] = None
            return None
        if workflow:
            started = cast(float, state.metadata["_policy_started_at"])
            state.metadata["_latest_deadline_at"] = started + seconds
            return max(0.0, started + seconds - self._absolute_clock())
        identity = self._effect_identity(state, "policy:task-deadline:v1")
        raw = await self._record_policy(
            state,
            identity,
            {"duration": seconds},
            lambda: {
                "profile": "owa/deadline/v1",
                "started_at": self._absolute_clock(),
                "duration": seconds,
            },
        )
        try:
            anchor = DeadlineAnchor.from_value(raw, seconds)
            state.metadata["_latest_deadline_at"] = anchor.started_at + anchor.duration
            return anchor.remaining(self._absolute_clock())
        except WorkflowExecutionError as exc:
            raise _EffectRecoveryRequired(exc.message) from exc

    async def _run_timed(
        self,
        operation: Callable[[], Awaitable[Any]],
        seconds: float | None,
        message: str,
        details: dict[str, Any],
        *,
        absolute_due: float | None = None,
        state: ExecutionState | None = None,
    ) -> Any:
        if seconds is None:
            return await operation()
        timeout_details = {
            **details,
            "type": "https://serverlessworkflow.io/spec/1.0.0/errors/timeout",
            "status": 408,
            "instance": details.get("instance", "/do"),
        }

        def timeout_error() -> WorkflowExecutionError:
            error = WorkflowExecutionError(message, details=timeout_details)
            error.status_code = 408
            return error

        async def cancel_deadline_children() -> None:
            if state is None:
                return
            callback = state.metadata.get("_cancel_child_scopes")
            handle = state.metadata.get("_invocation_handle")
            if callback is not None and isinstance(handle, ExecutionHandle):
                reference = str(state.variables.get("_execution_reference", "/do"))
                await callback(handle, (reference,), state.execution_path, reason="deadline")

        if seconds <= 0:
            await cancel_deadline_children()
            raise timeout_error()
        started = self._policy_clock()
        deadline = asyncio.timeout(seconds)
        scope = _DeadlineScope(deadline, absolute_due=absolute_due)
        token = _ACTIVE_TIMEOUTS.set((*_ACTIVE_TIMEOUTS.get(), scope))

        def timed_out() -> WorkflowExecutionError:
            if scope.unresolved_reference is not None:
                return _EffectRecoveryRequired(
                    "workflow action exceeded a deadline with an unresolved execution intent",
                    details={"instance": scope.unresolved_reference},
                )
            return timeout_error()

        try:
            try:
                async with deadline:
                    result = await operation()
            except TimeoutError as exc:
                if not deadline.expired():
                    raise
                await cancel_deadline_children()
                raise timed_out() from exc
            if deadline.expired() or self._policy_clock() - started >= seconds:
                await cancel_deadline_children()
                raise timed_out()
            return result
        finally:
            _ACTIVE_TIMEOUTS.reset(token)

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
            scope_executor=state.scope_executor,
            task_visits=dict(state.task_visits),
            execution_path=state.execution_path,
        )
        child.variables["context"] = child.context
        return child

    async def execute(
        self,
        plan: WorkflowPlan,
        input_data: Any,
        *,
        metadata: dict[str, Any] | None = None,
        scope_executor: ScopeExecutor | None = None,
    ) -> Any:
        workflow = plan.execution_source
        metadata = dict(metadata or {})
        metadata["_workflow_fingerprint"] = plan.fingerprint
        metadata["_workflow_source"] = plan.source
        metadata["_source_references"] = {
            task.execution_reference or task.reference: task.reference
            for scope in plan.scopes
            for task in scope.tasks
        }
        metadata["_functions"] = workflow.get("use", {}).get("functions", {})
        metadata["_imports"] = {item.reference: item for item in plan.imports}
        metadata["_schema_registry"] = build_schema_registry(plan.resources)
        metadata["_catalog_tasks"] = {
            task.execution_reference: metadata["_imports"][task.name]
            for scope in plan.scopes
            for task in scope.tasks
            if task.execution_reference is not None and task.name in metadata["_imports"]
        }
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
            scope_executor=scope_executor,
            variables={
                "runtime": {
                    "name": "open-workflow-agent",
                    "version": __version__,
                    "metadata": {"engine": metadata.get("engine")},
                },
                "workflow": {
                    "id": metadata["invocation_id"],
                    "definition": plan.source,
                    "input": copy.deepcopy(input_data),
                    "startedAt": _date_time_descriptor(),
                },
            },
            expressions=ExpressionEvaluator(
                mode=cast(ExpressionMode, workflow.get("evaluate", {}).get("mode", "strict"))
            ),
        )
        try:
            if plan.uses_timing:
                clock = await self._record_policy(
                    state,
                    root_clock_identity(str(metadata["invocation_id"]), plan.fingerprint),
                    {"profile": "owa/timing/v1"},
                    lambda: {"profile": "owa/timing/v1", "started_at": self._absolute_clock()},
                )
                state.metadata["_policy_started_at"] = clock_started_at(clock)
            if plan.resources:
                if self.services is None:
                    raise UnsupportedWorkflowFeature(
                        "captured schema resources require deployment trust"
                    )
                self.services.external_catalogs.schemas.verify(plan.resources)
            validate_expression_data(input_data)
            self._validate_data_schema(workflow.get("input"), input_data, "workflow input", state)
            input_spec = workflow.get("input")
            if isinstance(input_spec, Mapping) and "from" in input_spec:
                state.data = self._expressions(state).evaluate(
                    input_spec["from"], input_data, variables=state.variables
                )
            state.context = copy.deepcopy(state.data)
            state.variables["context"] = state.context
            workflow_timeout = _duration_seconds(workflow.get("timeout"))
            remaining_workflow_timeout = await self._deadline_remaining(
                state, workflow_timeout, workflow=True
            )
            await self._run_timed(
                lambda: self._run_tasks(workflow.get("do", []), state),
                remaining_workflow_timeout,
                "workflow timed out",
                {"timeout_seconds": workflow_timeout},
                absolute_due=state.metadata.get("_latest_deadline_at"),
                state=state,
            )
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
            self._validate_data_schema(output, result, "workflow output", state)
            self._set_invocation_status(state, "completed", output=result, error=None)
        except InvocationCancelled as exc:
            if getattr(self._control(state), "interruption_reason", None) in {
                "host_drain",
                "ownership_lost",
            }:
                # Execution interruption leaves the durable invocation open
                # for another owner; it carries no business cancel authority.
                raise
            self._set_invocation_status(state, "cancelled", output=None, error=_error_details(exc))
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
            self._set_invocation_status(state, "faulted", output=None, error=_error_details(exc))
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
        if state.scope_executor is not None:
            await state.scope_executor.run_scope(self, tasks, state, prefix=prefix, indexed=indexed)
            return
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
            original = state.metadata.get("_source_references", {}).get(reference, reference)
            await self._run_task_item(
                str(name), definition, state, original, execution_reference=reference
            )
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

    async def _run_task_item(
        self,
        name: str,
        definition: Mapping[str, Any],
        state: ExecutionState,
        reference: str,
        *,
        execution_reference: str | None = None,
    ) -> None:
        resolved_reference = execution_reference or reference
        visit = state.task_visits.get(resolved_reference, 0) + 1
        state.task_visits[resolved_reference] = visit
        local = self._copy_state(state)
        local.execution_path += ((resolved_reference, visit),)
        imported = local.metadata.get("_catalog_tasks", {}).get(execution_reference or reference)
        for key in ("input", "output", "task"):
            local.variables.pop(key, None)
        local.variables.update(
            {
                "_task_reference": reference,
                "_execution_reference": execution_reference or reference,
                "_task_name": str(name),
                "task": {
                    "name": str(name),
                    "reference": reference,
                    "definition": copy.deepcopy(
                        imported.definition
                        if isinstance(imported, ResolvedCatalogFunction)
                        else definition_at_pointer(
                            local.variables.get("workflow", {}).get("definition", {}), reference
                        )
                        or definition
                    ),
                    "input": copy.deepcopy(state.data),
                    "startedAt": _date_time_descriptor(),
                },
            }
        )
        task_started = time.perf_counter()
        self._emit("TaskStarted", {**self._task_event(reference, name, local), "status": "running"})
        try:
            result = await self._run_with_policy(str(name), definition, local, reference)
            skipped = isinstance(result, _SkippedTask)
            output = (
                copy.deepcopy(result.data)
                if skipped
                else self._apply_task_data(definition, result, local)
            )
        except asyncio.CancelledError:
            interruption = getattr(self._control(local), "interruption_reason", None)
            if interruption in {"host_drain", "ownership_lost"}:
                self._emit(
                    "TaskProgress",
                    {
                        **self._task_event(reference, name, local),
                        "status": "running",
                        "reason": interruption,
                        "progress": {"phase": "interrupted"},
                    },
                )
            elif state.flow.ended.is_set():
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
            interruption = getattr(self._control(local), "interruption_reason", None)
            if interruption in {"host_drain", "ownership_lost"}:
                self._emit(
                    "TaskProgress",
                    {
                        **self._task_event(reference, name, local),
                        "status": "running",
                        "reason": interruption,
                        "progress": {"phase": "interrupted"},
                    },
                )
                raise
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
            if isinstance(exc, OwaError) and exc._operation_id is None:
                exc._operation_id = self._operation_id(local)
            if isinstance(exc, OwaError):
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
            if isinstance(exc, OwaError):
                raise
            failure = WorkflowExecutionError(f"task {name} failed", details={"task": str(name)})
            failure._operation_id = self._operation_id(local)
            if execution_reference is not None and execution_reference != reference:
                failure.details["instance"] = reference
            raise failure from exc
        local.outputs[str(name)] = copy.deepcopy(output)
        state.outputs = local.outputs
        state.task_visits = local.task_visits
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
        remaining_timeout = await self._deadline_remaining(state, timeout_seconds)
        last_error: Exception | None = None
        raw_input = copy.deepcopy(state.data)
        execution_path = state.execution_path
        for attempt in range(max(1, attempts)):
            state.execution_path = execution_path + ((f"{reference}/@attempt", attempt + 1),)
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
                with self._operation_visit_scope(state):
                    return await self._run_timed(
                        lambda: self._await_with_cancellation(
                            self._run_task(name, definition, state), state
                        ),
                        remaining_timeout,
                        f"task {name} timed out",
                        {"task": name, "timeout_seconds": timeout_seconds, "instance": reference},
                        absolute_due=state.metadata.get("_latest_deadline_at"),
                        state=state,
                    )
            except Exception as exc:
                last_error = exc
                if isinstance(
                    exc, (InvocationCancelled, InvocationStateConflict, _EffectRecoveryRequired)
                ):
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
        self._validate_data_schema(input_spec, raw_input, f"task {name} input", state)
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
            functions = state.metadata.get("_functions", {})
            call = definition["call"]
            imported = state.metadata.get("_imports", {}).get(call)
            if call in functions or isinstance(imported, ResolvedCatalogFunction):
                child = self._copy_state(state)
                child.data = copy.deepcopy(payload)
                await self._run_tasks(
                    [
                        {
                            call: imported.execution_definition
                            if imported is not None
                            else functions[call]
                        }
                    ],
                    child,
                    prefix=self._nested_prefix(state, "call"),
                    indexed=False,
                )
                state.context = copy.deepcopy(child.context)
                state.variables["context"] = state.context
                state.stop = child.stop
                for key in ("event", "resume_input"):
                    if key in child.variables:
                        state.variables[key] = copy.deepcopy(child.variables[key])
                return child.data
            if str(definition["call"]) in {"http", "mcp", "a2a", "openapi"} and isinstance(
                payload, Mapping
            ):
                payload = _resolve_protocol_endpoints(payload, state.data, state.variables)
                payload = {
                    **payload,
                    "operation_id": self._protocol_operation_id(
                        str(definition["call"]), payload, state
                    ),
                }
            return await self._call(str(definition["call"]), payload, state)
        if "emit" in definition:
            return await self._run_emit(definition["emit"], state)
        if "listen" in definition:
            return await self._run_listen(definition["listen"], state, definition.get("foreach"))
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
                prefix=self._nested_prefix(state, "do"),
            )
            return state.data
        if "try" in definition:
            catch = definition.get("catch") or definition.get("except")
            if catch is None:
                await self._run_tasks(
                    _task_body(definition["try"]),
                    state,
                    prefix=self._nested_prefix(state, "try"),
                )
                return state.data
            catch_retry = catch.get("retry") if isinstance(catch, Mapping) else None
            retry_policy = RetryPolicy.from_value(catch_retry, duration=_duration_seconds)
            retry_started: float | None = None
            attempt = 0
            execution_path = state.execution_path
            while True:
                state.execution_path = execution_path + (
                    (f"{task_reference}/@catch-attempt", attempt + 1),
                )
                try:
                    await self._run_retry_attempt(
                        definition, state, retry_policy, attempt, retry_started
                    )
                    return state.data
                except Exception as exc:
                    if isinstance(
                        exc, (InvocationCancelled, InvocationStateConflict, _EffectRecoveryRequired)
                    ):
                        raise
                    error = _error_details(exc)
                    catch_error = _catch_error_value(
                        error, status_code=exc.status_code if isinstance(exc, OwaError) else 500
                    )
                    as_name = catch.get("as", "error")
                    if isinstance(as_name, str):
                        state.variables[as_name] = catch_error
                    if not _catch_matches(catch, catch_error, state, self._expressions(state)):
                        raise
                    schedule_identity = self._effect_identity(state, "policy:retry-schedule:v1")
                    journal = getattr(self.services, "effect_journal", None)
                    scheduled = journal is not None and journal.get(schedule_identity) is not None
                    if retry_policy.allows_retry(attempt) and (
                        scheduled or _retry_allowed(catch_retry, state, self._expressions(state))
                    ):
                        if retry_started is None:
                            retry_started = await self._retry_clock(
                                state, execution_path, retry_policy
                            )
                        remaining = (
                            retry_policy.duration - (self._absolute_clock() - retry_started)
                            if retry_policy.duration is not None
                            else None
                        )
                        retry_ready = scheduled or remaining is None or remaining > 0
                        if retry_ready:

                            def choose_schedule(
                                retry_index: int = attempt, budget: float | None = remaining
                            ) -> dict[str, Any]:
                                delay = retry_policy.delay_for(retry_index, self._retry_random)
                                scheduled_at = self._absolute_clock()
                                return {
                                    "profile": "owa/retry-schedule/v1",
                                    "retry": retry_index + 1,
                                    "delay": delay,
                                    "due_at": scheduled_at + delay,
                                    "scheduled_at": scheduled_at,
                                    "allowed": budget is None or delay < budget,
                                }

                            raw_schedule = await self._record_policy(
                                state,
                                schedule_identity,
                                {
                                    "retry": attempt + 1,
                                    "started_at": retry_started,
                                    "delay": retry_policy.delay,
                                    "backoff": retry_policy.backoff,
                                    "jitter_from": retry_policy.jitter_from,
                                    "jitter_to": retry_policy.jitter_to,
                                },
                                choose_schedule,
                            )
                            schedule = RetrySchedule.from_value(raw_schedule, attempt + 1)
                            delay = max(0.0, schedule.due_at - self._absolute_clock())
                            retry_ready = schedule.allowed and (
                                scheduled or remaining is None or delay < remaining
                            )
                        if retry_ready:
                            await self._token(state).sleep(delay)
                        if retry_ready and (
                            scheduled
                            or retry_policy.duration is None
                            or self._absolute_clock() - retry_started < retry_policy.duration
                        ):
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
                                    **(
                                        {"operation_id": exc._operation_id}
                                        if isinstance(exc, OwaError)
                                        and exc._operation_id is not None
                                        else {}
                                    ),
                                    "status": "retrying",
                                    "attempt": attempt + 1,
                                    "error": _error_details(exc),
                                },
                            )
                            attempt += 1
                            continue
                    await self._run_tasks(
                        _task_body(catch.get("do", [])),
                        state,
                        prefix=self._nested_prefix(state, "catch/do"),
                    )
                    self._apply_transition(catch.get("then"), state)
                    return state.data
        if "wait" in definition:
            if _RECONCILE_EXPIRED_RETRY.get():
                wait_timeout_error = WorkflowExecutionError(
                    "retry budget expired before a pending wait",
                    details={
                        "type": "https://serverlessworkflow.io/spec/1.0.0/errors/timeout",
                        "status": 408,
                        "instance": state.variables.get("_task_reference", "/do"),
                    },
                )
                wait_timeout_error.status_code = 408
                raise wait_timeout_error
            wait_value = self._expressions(state).evaluate(
                definition["wait"], state.data, variables=state.variables
            )
            seconds = max(0.0, _duration_seconds(wait_value) or 0)
            if (
                seconds > 0
                and self.services is not None
                and isinstance(state.metadata.get("_invocation_handle"), ExecutionHandle)
            ):
                return await self._durable_wait(name, state, seconds)
            await self._notify_waiting(state)
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
                    error = self._expressions(state).evaluate(
                        error, state.data, variables=state.variables
                    )
                    message = error.get("detail") or error.get("title") or error.get("type")
                    details = dict(error)
                    details["instance"] = state.variables.get("_task_reference", "/do")
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
        imported = state.metadata.get("_catalog_tasks", {}).get(
            state.variables.get("_execution_reference")
        )
        if isinstance(imported, ResolvedCatalogFunction):
            resolver = state.metadata.get("_external_catalog_resolver")
            if resolver is None and self.services is not None:
                resolver = self.services.external_catalogs
            if resolver is None:
                raise WorkflowExecutionError("external catalog resolver is unavailable")
            return await self._run_effect(
                call,
                payload,
                state,
                lambda: resolver.invoke_prepared(
                    imported,
                    call,
                    payload,
                    mode=self._expressions(state).mode,
                    registry=state.metadata.get("_schema_registry"),
                ),
            )
        if call in {"http", "mcp", "a2a", "openapi"}:
            if self.services is None:
                raise WorkflowExecutionError(f"protocol service unavailable: {call}")
            return await self._run_effect(
                call,
                payload,
                state,
                lambda: self.services.call_protocol(call, payload),
                a2a_dispatch=(
                    lambda selection: self.services.call_protocol(
                        call, payload, _a2a_selection=selection
                    )
                )
                if call == "a2a"
                else None,
            )
        if self.services is None:
            raise WorkflowExecutionError("runtime services unavailable for catalog call")
        binding = state.metadata.get("_imports", {}).get(call)
        catalog_reference = parse_catalog_function_reference(call)
        if (
            binding is None
            and catalog_reference is not None
            and catalog_reference.catalog != "default"
        ):
            registered = getattr(
                getattr(self.services, "external_catalogs", None), "registered_binding", None
            )
            if callable(registered):
                binding = registered(call)
        if isinstance(binding, ResolvedCatalogFunction):
            return await self.services.external_catalogs.invoke_frozen(
                binding,
                payload,
                variables=state.variables,
                operation_id=self._operation_id(state),
                mode=self._expressions(state).mode,
                metadata=state.metadata,
                parent_state=state,
            )
        context = CatalogContext(
            model=self.services.model,
            agent_instruction=getattr(self.services, "agent_instruction", ""),
            services=self.services,
            metadata=state.metadata,
        )
        return await self._run_effect(
            call, payload, state, lambda: self.catalog.call(call, payload, context)
        )

    async def _run_effect(
        self,
        call: str,
        payload: Any,
        state: ExecutionState,
        dispatch: Callable[[], Awaitable[Any]],
        *,
        operation: str | None = None,
        child_dependency: Callable[[], ChildInvocationDependency] | None = None,
        a2a_dispatch: Callable[[A2ACardSelection], Awaitable[Any]] | None = None,
    ) -> Any:
        journal = getattr(self.services, "effect_journal", None)
        if journal is None:
            if _RECONCILE_EXPIRED_RETRY.get():
                raise WorkflowExecutionError("retry budget expired before a new action")
            try:
                return await dispatch()
            except (_McpOutcomeUncertain, _A2AOutcomeUncertain) as uncertainty:
                raise _EffectRecoveryRequired(
                    uncertainty.message, details=uncertainty.details
                ) from None
        identity = self._effect_identity(state, operation or "call:" + call)
        operation_id = (
            payload.get("operation_id")
            if call in {"http", "mcp", "a2a", "openapi"} and isinstance(payload, Mapping)
            else None
        ) or self._operation_id(state)
        if call == "a2a" and isinstance(payload, Mapping) and "agentCard" in payload:
            from .a2a_admission import A2AAgentCardAdmission

            if a2a_dispatch is None:
                raise _EffectRecoveryRequired("A2A Agent Card admission context is unavailable")
            if _RECONCILE_EXPIRED_RETRY.get() and journal.get(identity) is None:
                raise WorkflowExecutionError("retry budget expired before a new action")
            admission_store = getattr(self.services, "a2a_agent_card_admission", None)
            if not isinstance(admission_store, A2AAgentCardAdmission):
                raise _EffectRecoveryRequired("A2A Agent Card admission service is unavailable")
            deadlines = [
                scope.absolute_due
                for scope in _ACTIVE_TIMEOUTS.get()
                if scope.absolute_due is not None
            ]
            admission = await admission_store.prepare(
                identity,
                payload,
                cast(ExecutionHandle, state.metadata["_invocation_handle"]),
                execution_lease=self._execution_lease(state),
                deadline_at=min(deadlines) if deadlines else None,
                now=self._absolute_clock,
                allow_fetch=not _RECONCILE_EXPIRED_RETRY.get(),
            )
            if admission is not None:
                prepared_at = time.monotonic()
                await self._notify_receipt(state, admission.identity, admission.receipt)
                selected_dispatch = a2a_dispatch

                async def dispatch_admitted_a2a() -> Any:
                    selection = admission.selection
                    remaining = admission.receipt.output["deadline_at"] - self._absolute_clock()
                    if selection.remaining_timeout is not None:
                        remaining = min(
                            remaining,
                            selection.remaining_timeout - (time.monotonic() - prepared_at),
                        )
                    return await selected_dispatch(replace(selection, remaining_timeout=remaining))

                dispatch = dispatch_admitted_a2a
        if _RECONCILE_EXPIRED_RETRY.get():
            known = journal.get(identity)
            if known is None:
                raise WorkflowExecutionError("retry budget expired before a new action")
            if known.status == "pending":
                raise _EffectRecoveryRequired(
                    "workflow action has an unresolved execution intent",
                    details={"instance": state.variables.get("_task_reference")},
                )
            if known.request_digest != effect_request_digest(call, payload):
                raise _EffectRecoveryRequired(
                    "workflow action request differs during expired retry recovery"
                )
            await self._notify_receipt(state, identity, known)
            return self._effect_result(known)
        # Each dispatch attempt gets a fresh private owner. Recovery never adopts
        # an earlier pending intent: its external outcome may be ambiguous.
        owner = str(uuid4())
        recovery = cast(
            "HttpExternalActionRecovery | None",
            getattr(self.services, "external_action_recovery", None),
        )
        if (
            call == "http"
            and recovery is not None
            and recovery.http
            is not getattr(getattr(self.services, "protocols", None), "http", None)
        ):
            raise _EffectRecoveryRequired("external action recovery transport binding changed")
        try:
            admitted = (
                recovery.admit(
                    identity,
                    payload,
                    owner=owner,
                    operation_id=operation_id,
                    execution_lease=self._execution_lease(state),
                )
                if call == "http" and recovery is not None
                else None
            )
            if admitted is not None:
                receipt, trust = admitted
                await self._notify_receipt(
                    state,
                    replace(identity, operation="policy:external-action-admission:v1"),
                    trust,
                )
            else:
                receipt = journal.begin(
                    identity,
                    effect_request_digest(call, payload),
                    owner,
                    operation_id=operation_id,
                    execution_lease=self._execution_lease(state),
                )
        except WorkflowExecutionError as exc:
            raise _EffectRecoveryRequired(exc.message, details=exc.details) from exc
        if receipt.status != "pending":
            await self._notify_receipt(state, identity, receipt)
            return self._effect_result(receipt)
        if receipt.owner != owner:
            if call == "http" and recovery is not None and admitted is not None:
                try:
                    recovered = await recovery.recover(
                        identity, payload, execution_lease=self._execution_lease(state)
                    )
                except InvocationStateConflict:
                    raise
                except WorkflowExecutionError as exc:
                    # Failed outcome lookup does not establish that retrying
                    # the external business request would be safe.
                    raise _EffectRecoveryRequired(exc.message, details=exc.details) from exc
                except Exception:
                    raise _EffectRecoveryRequired(
                        "external action recovery outcome could not be reconciled"
                    ) from None
                if recovered is not None:
                    resolved, proof = recovered
                    await self._notify_receipt(
                        state,
                        replace(identity, operation="policy:external-action-recovery:v1"),
                        proof,
                    )
                    await self._notify_receipt(state, identity, resolved)
                    return self._effect_result(resolved)
            raise _EffectRecoveryRequired(
                "workflow action has an unresolved execution intent",
                details={"instance": state.variables.get("_task_reference")},
            )
        try:
            result = await dispatch()
            validate_expression_data(result)
        except asyncio.CancelledError:
            expired = [scope for scope in _ACTIVE_TIMEOUTS.get() if scope.deadline.expired()]
            if expired:
                reference = str(state.variables.get("_task_reference", "/do"))
                for scope in expired:
                    # Cancellation wrappers may drain and discard a child error.
                    # Share the uncertainty with the timer owner before unwinding.
                    scope.unresolved_reference = reference
                raise _EffectRecoveryRequired(
                    "workflow action exceeded a deadline with an unresolved execution intent",
                    details={"instance": reference},
                ) from None
            raise
        except (_McpOutcomeUncertain, _A2AOutcomeUncertain) as uncertainty:
            # No correlated known result means the business action may have
            # committed. Keep its pending intent and bypass catch/retry routing.
            raise _EffectRecoveryRequired(
                uncertainty.message, details=uncertainty.details
            ) from None
        except (InvocationCancelled, InvocationStateConflict):
            # Cancellation or loss of common state authority does not prove
            # an external outcome, even when the invocation itself has an
            # authorized business cancellation. Retain its pending intent.
            raise
        except Exception as exc:
            # Match the common task boundary and avoid saving unexpected native
            # exception messages in the durable journal.
            error = (
                exc
                if isinstance(exc, OwaError)
                else WorkflowExecutionError(
                    f"task {state.variables.get('_task_name', 'unknown')} failed",
                    details={"task": state.variables.get("_task_name", "unknown")},
                )
            )
            error._operation_id = error._operation_id or operation_id
            receipt = self._store_effect_outcome(
                journal,
                identity,
                owner,
                state,
                error=error,
                child_dependency=child_dependency,
                operation_id=operation_id,
            )
            await self._notify_receipt(state, identity, receipt)
            if error is exc:
                raise
            raise error from exc
        receipt = self._store_effect_outcome(
            journal,
            identity,
            owner,
            state,
            output=result,
            child_dependency=child_dependency,
            operation_id=operation_id,
        )
        await self._notify_receipt(state, identity, receipt)
        return self._effect_result(receipt)

    @staticmethod
    def _effect_identity(state: ExecutionState, operation: str) -> EffectIdentity:
        return EffectIdentity(
            str(state.metadata["invocation_id"]),
            str(state.metadata["_workflow_fingerprint"]),
            str(state.variables.get("_execution_reference", state.variables["_task_reference"])),
            state.execution_path,
            operation,
        )

    async def _durable_wait(self, name: str, state: ExecutionState, seconds: float) -> Any:
        assert self.services is not None
        handle = cast(ExecutionHandle, state.metadata["_invocation_handle"])
        identity = self._effect_identity(state, "wait:arm:v1")
        deadlines = [
            scope.absolute_due for scope in _ACTIVE_TIMEOUTS.get() if scope.absolute_due is not None
        ]
        registration = self.services.timers.arm(
            identity,
            seconds,
            now=self._absolute_clock(),
            workflow_source=state.metadata["_workflow_source"],
            execution_lease=handle._execution_lease,
            deadline_at=min(deadlines) if deadlines else None,
        )
        await self._notify_receipt(state, identity, registration.receipt)
        wake = self.services.timers.wake_for(registration)
        if wake is None and self._absolute_clock() >= registration.wake_at:
            wake = self.services.timers.wake(
                registration,
                "timer_due",
                now=self._absolute_clock(),
                execution_lease=handle._execution_lease,
            )
        reference = str(state.variables.get("_task_reference", "unknown-task"))
        if wake is not None:
            await self._notify_receipt(
                state, replace(identity, operation="wait:wake:v1"), wake.receipt
            )
            self.services.timers.consume(registration, execution_lease=handle._execution_lease)
            if wake.reason == "resume_requested":
                state.variables["resume_input"] = copy.deepcopy(wake.resume_input)
            self._set_invocation_status(state, "running")
            event = {
                **self._task_event(reference, name, state),
                "status": "running",
                "reason": "resume_requested"
                if wake.reason == "resume_requested"
                else "wait_elapsed",
                "progress": {"phase": "resumed"},
            }
            self._emit("TaskProgress", event)
            self._emit("WorkflowResumed", event)
            return state.data
        await self._notify_waiting(state)
        self._set_invocation_status(state, "waiting")
        event = {
            **self._task_event(reference, name, state),
            "status": "waiting",
            "progress": {"phase": "waiting", "seconds": seconds, "due_at": registration.due_at},
        }
        self._emit("TaskProgress", event)
        self._emit("TaskWaiting", event)
        self._emit("WorkflowWaiting", event)
        raise TimerSuspension(identity, registration.due_at)

    @staticmethod
    async def _notify_receipt(
        state: ExecutionState, identity: EffectIdentity, receipt: EffectReceipt
    ) -> None:
        # Native adapters own the persistence of these opaque receipt links.
        # Core keeps no native checkpoint or copy of native execution state.
        observer = state.metadata.get("_effect_receipt_observer")
        if observer is None:
            return
        try:
            await observer(identity, receipt)
        except InvocationStateConflict:
            raise
        except Exception:
            raise _EffectRecoveryRequired(
                "workflow action recovery evidence could not be retained",
                details={"instance": state.variables.get("_task_reference")},
            ) from None

    @staticmethod
    async def _notify_waiting(state: ExecutionState) -> None:
        observer = state.metadata.get("_waiting_checkpoint_observer")
        if observer is None:
            return
        try:
            await observer()
        except InvocationStateConflict:
            raise
        except Exception:
            raise _EffectRecoveryRequired(
                "workflow waiting recovery evidence could not be retained",
                details={"instance": state.variables.get("_task_reference")},
            ) from None

    def _store_effect_outcome(
        self,
        journal: EffectJournal,
        identity: EffectIdentity,
        owner: str,
        state: ExecutionState,
        *,
        output: Any = None,
        error: OwaError | None = None,
        child_dependency: Callable[[], ChildInvocationDependency] | None = None,
        operation_id: str | None = None,
    ) -> EffectReceipt:
        try:
            return journal.finish(
                identity,
                owner,
                output=output,
                error=error.as_dict() if error else None,
                error_status_code=error.status_code if error else None,
                operation_id=operation_id
                or (error._operation_id if error else self._operation_id(state)),
                child_invocation=child_dependency() if child_dependency else None,
            )
        except WorkflowExecutionError as exc:
            raise _EffectRecoveryRequired(
                "workflow action outcome could not be reconciled",
                details={"instance": state.variables.get("_task_reference")},
            ) from exc

    @staticmethod
    def _execution_lease(state: ExecutionState) -> ExecutionLease | None:
        handle = state.metadata.get("_invocation_handle")
        return handle._execution_lease if handle is not None else None

    @staticmethod
    def _effect_result(receipt: EffectReceipt) -> Any:
        if receipt.error is not None:
            error_types = (
                InvocationCancelled,
                WorkflowSemanticError,
                UnsupportedWorkflowFeature,
                ExpressionError,
                ToolError,
                ModelError,
                AgentError,
                KnowledgeError,
                MemoryError,
            )
            error_type = next(
                (kind for kind in error_types if kind.code == receipt.error["code"]),
                WorkflowExecutionError,
            )
            error = error_type(
                receipt.error["message"], details=copy.deepcopy(receipt.error["details"])
            )
            error.code = receipt.error["code"]
            if receipt.error_status_code is not None:
                error.status_code = receipt.error_status_code
            error._operation_id = receipt.operation_id
            raise error
        return copy.deepcopy(receipt.output)

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
            state.task_visits = child.task_visits
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
        if compete:
            return await self._run_competing_fork(branches, state)

        async def run_branch(index: int, branch: Any) -> Any:
            child = self._copy_state(state)
            owner = asyncio.current_task()
            assert owner is not None
            child.branch_owners += (owner,)
            try:
                await self._run_tasks(
                    _task_body(branch),
                    child,
                    prefix=self._nested_prefix(child, f"fork/branches/{index}"),
                    indexed=False,
                )
            except (TimerSuspension, ChildSuspension) as suspension:
                # A paused branch has released its execution. Drain runnable
                # siblings before handing suspension back to the native scope.
                return suspension
            if state.flow.ended.is_set() and owner in state.flow.owners:
                state.flow.output = copy.deepcopy(child.data)
                state.flow.context = copy.deepcopy(child.context)
            return child.data

        branch_tasks = [
            asyncio.create_task(run_branch(index, branch)) for index, branch in enumerate(branches)
        ]
        ending = asyncio.create_task(state.flow.ended.wait())
        joined = asyncio.gather(*branch_tasks)
        try:
            watched: set[asyncio.Future[Any]] = {joined, ending}
            await asyncio.wait(watched, return_when=asyncio.FIRST_COMPLETED)
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
                cancel_children = state.metadata.get("_cancel_child_scopes")
                handle = state.metadata.get("_invocation_handle")
                if cancel_children is not None and isinstance(handle, ExecutionHandle):
                    await cancel_children(
                        handle,
                        tuple(
                            self._nested_prefix(state, f"fork/branches/{index}") + "/"
                            for index, branch in enumerate(branch_tasks)
                            if branch not in state.flow.owners
                        ),
                        state.execution_path,
                        reason="workflow_end",
                    )
                state.stop = True
                state.context = copy.deepcopy(state.flow.context)
                state.variables["context"] = state.context
                return copy.deepcopy(state.flow.output)
            results = joined.result()
            suspensions = [
                result
                for result in results
                if isinstance(result, (TimerSuspension, ChildSuspension))
            ]
            if suspensions:
                self._set_invocation_status(state, "waiting")
                raise min(suspensions, key=self._suspension_order)
        finally:
            for pending in (*branch_tasks, ending):
                if not pending.done():
                    pending.cancel()
            await asyncio.gather(*branch_tasks, ending, return_exceptions=True)
            await asyncio.gather(joined, return_exceptions=True)
        return results

    async def _run_competing_fork(self, branches: list[Any], state: ExecutionState) -> Any:
        if not branches:
            return state.data
        store = getattr(self.services, "fork_decisions", None)
        if store is None:
            raise WorkflowExecutionError("competitive fork decision service is unavailable")
        identity = ForkIdentity(
            str(state.metadata["invocation_id"]),
            str(state.metadata["_workflow_fingerprint"]),
            str(state.variables["_execution_reference"]),
            state.execution_path,
        )
        recorded = store.get(identity)
        if recorded is not None:
            self._token(state).checkpoint()
            if not recorded.drained:
                store.mark_drained(identity, execution_lease=self._execution_lease(state))
            return self._fork_decision_result(recorded, state)

        completion: asyncio.Future[ForkDecision] = asyncio.get_running_loop().create_future()
        branch_tasks: list[asyncio.Task[Any]] = []
        suspensions: dict[int, TimerSuspension | ChildSuspension] = {}

        def cancel_siblings(owner: asyncio.Task[Any]) -> None:
            for pending in branch_tasks:
                if pending is not owner and not pending.done() and not pending.cancelling():
                    pending.cancel()

        async def run_branch(index: int, branch: Any) -> None:
            nonlocal recorded
            child = self._copy_state(state)
            owner = asyncio.current_task()
            assert owner is not None
            child.branch_owners += (owner,)
            error: OwaError | None = None
            try:
                await self._run_tasks(
                    _task_body(branch),
                    child,
                    prefix=self._nested_prefix(child, f"fork/branches/{index}"),
                    indexed=False,
                )
            except (TimerSuspension, ChildSuspension) as suspension:
                suspensions[index] = suspension
                return
            except (_EffectRecoveryRequired, InvocationStateConflict) as exc:
                # Uncertain actions have no branch outcome. Preserve recovery
                # routing and drain siblings without fabricating a winner.
                if not completion.done():
                    completion.set_exception(exc)
                    cancel_siblings(owner)
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error = (
                    exc
                    if isinstance(exc, OwaError)
                    else WorkflowExecutionError("competitive fork branch failed")
                )
            if completion.done():
                return
            if state.flow.ended.is_set() and owner not in state.flow.owners:
                return
            try:
                self._token(state).checkpoint()
                ended = state.flow.ended.is_set() and owner in state.flow.owners
                decision = ForkDecision(
                    winner=index,
                    status="faulted" if error else "ended" if ended else "completed",
                    output=copy.deepcopy(child.data) if error is None else None,
                    context=copy.deepcopy(child.context),
                    error=copy.deepcopy(error.as_dict()) if error else None,
                    operation_id=error._operation_id if error else self._operation_id(child),
                    cancellation_intents=tuple(i for i in range(len(branches)) if i != index),
                    error_status_code=error.status_code if error else None,
                )
                recorded = store.claim(
                    identity, decision, execution_lease=self._execution_lease(state)
                )
                completion.set_result(recorded)
            except Exception as exc:
                completion.set_exception(exc)
            finally:
                cancel_siblings(owner)

        branch_tasks = [
            asyncio.create_task(run_branch(index, branch)) for index, branch in enumerate(branches)
        ]
        ending = asyncio.create_task(state.flow.ended.wait())
        joined = asyncio.gather(*branch_tasks, return_exceptions=True)
        try:
            watched: set[asyncio.Future[Any]] = {completion, ending, joined}
            await asyncio.wait(watched, return_when=asyncio.FIRST_COMPLETED)
            if state.flow.ended.is_set() and not completion.done():
                if not any(branch_task in state.flow.owners for branch_task in branch_tasks):
                    # End belongs to an enclosing/sibling scope. This race has
                    # no winner and is drained by the finally block below.
                    return state.data
                # The ending branch may still be saving its native checkpoint.
                # Commit its decision before cancelling any losing branch.
                decision = await completion
            elif completion.done():
                decision = completion.result()
            else:
                for branch_result in joined.result():
                    if isinstance(branch_result, BaseException):
                        raise branch_result
                if suspensions:
                    self._set_invocation_status(state, "waiting")
                    raise min(suspensions.values(), key=self._suspension_order)
                raise WorkflowExecutionError("competitive fork has no completed branch outcome")
        finally:
            for pending in (*branch_tasks, ending):
                if not pending.done() and not pending.cancelling():
                    pending.cancel()
            await asyncio.gather(*branch_tasks, ending, return_exceptions=True)
            await asyncio.gather(joined, return_exceptions=True)
            # Retrieve any storage failure even when cancellation interrupted the
            # parent, avoiding an abandoned exception on its completion future.
            if completion.done() and not completion.cancelled():
                completion.exception()
            if recorded is not None:
                cancel_children = state.metadata.get("_cancel_child_scopes")
                handle = state.metadata.get("_invocation_handle")
                if cancel_children is not None and isinstance(handle, ExecutionHandle):
                    await cancel_children(
                        handle,
                        tuple(
                            self._nested_prefix(state, f"fork/branches/{index}") + "/"
                            for index in recorded.cancellation_intents
                        ),
                        state.execution_path,
                    )
                store.mark_drained(identity, execution_lease=self._execution_lease(state))
        self._token(state).checkpoint()
        handle = state.metadata.get("_invocation_handle")
        if handle is not None and handle.status == "waiting":
            self._set_invocation_status(state, "running")
        return self._fork_decision_result(decision, state)

    @staticmethod
    def _fork_decision_result(decision: ForkDecision, state: ExecutionState) -> Any:
        if decision.error is not None:
            error_type = (
                InvocationCancelled
                if decision.error["code"] == InvocationCancelled.code
                else WorkflowExecutionError
            )
            error = error_type(
                decision.error["message"], details=copy.deepcopy(decision.error["details"])
            )
            error.code = decision.error["code"]
            if decision.error_status_code is not None:
                error.status_code = decision.error_status_code
            error._operation_id = decision.operation_id
            raise error
        if decision.status == "ended":
            state.flow.output = copy.deepcopy(decision.output)
            state.flow.context = copy.deepcopy(decision.context)
            state.flow.owners = state.branch_owners
            state.flow.ended.set()
            state.stop = True
            state.context = copy.deepcopy(decision.context)
            state.variables["context"] = state.context
        return copy.deepcopy(decision.output)

    @staticmethod
    def _suspension_order(value: TimerSuspension | ChildSuspension) -> tuple[float, str]:
        if isinstance(value, TimerSuspension):
            return value.due_at, value.timer_key
        return value.deadline_at or math.inf, value.identity.key

    async def _suspend_child(
        self,
        state: ExecutionState,
        child: ExecutionHandle,
        identity: EffectIdentity,
        deadline_at: float | None,
    ) -> NoReturn:
        parent = cast(ExecutionHandle, state.metadata["_invocation_handle"])
        registration = self.services.child_waits.arm(
            identity,
            parent,
            child,
            workflow_source=state.metadata["_workflow_source"],
            deadline_at=deadline_at,
        )
        await self._notify_receipt(
            state, replace(identity, operation="policy:child-await:v1"), registration.receipt
        )
        await self._notify_waiting(state)
        self._set_invocation_status(state, "waiting")
        event = {
            **self._task_event(
                str(state.variables["_task_reference"]),
                str(state.variables.get("_task_name", "child")),
                state,
            ),
            "status": "waiting",
            "progress": {"phase": "waiting", "child_invocation_id": child.invocation_id},
        }
        self._emit("TaskWaiting", event)
        self._emit("WorkflowWaiting", event)
        raise registration.suspension

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
        identity = self._effect_identity(state, "run:workflow")
        binding = self.services.children.get(identity)
        child_input = (
            self._expressions(state).evaluate(
                reference["input"], state.data, variables=state.variables
            )
            if "input" in reference
            else copy.deepcopy(state.data)
        )
        if binding is None:
            plan = self.services.workflow_catalog.resolve(reference)
        else:
            pinned = json.loads(binding.workflow_source_json)["document"]
            if (
                reference.get("namespace") != pinned["namespace"]
                or reference.get("name") != pinned["name"]
                or reference.get("version") not in {"latest", pinned["version"]}
            ):
                raise _EffectRecoveryRequired("recorded child workflow reference changed")
            parent = state.metadata.get("_invocation_handle")
            if not isinstance(parent, ExecutionHandle):
                raise _EffectRecoveryRequired("recorded child parent authority is unavailable")
            admitted_child = self.services.children.verify_control(binding, parent)
            recorded_digest = effect_request_digest(
                "run:workflow",
                {"workflow_fingerprint": admitted_child.workflow_fingerprint, "input": child_input},
            )
            if binding.request_digest != recorded_digest:
                raise _EffectRecoveryRequired("recorded child workflow request differs")
            outcome = self.services.effect_journal.get(identity)
            if outcome is not None and outcome.status != "pending":
                if outcome.request_digest != recorded_digest:
                    raise _EffectRecoveryRequired("recorded child workflow request differs")
                # A retained terminal receipt is a common outcome read. It does
                # not need current catalog content or another child execution.
                await self._notify_receipt(state, identity, outcome)
                return self._effect_result(outcome)
            if self.services.recovery_definitions.has_binding(admitted_child):
                plan = self.services.resolve_original_definition(admitted_child)
            else:
                if admitted_child.status not in {"completed", "faulted", "cancelled"}:
                    preflight = self.services._workflow_initial_child_preflight
                    if preflight is None:
                        raise _EffectRecoveryRequired(
                            "child initial definition verification unavailable"
                        )
                    # Verify unstarted origin/progress before current catalog
                    # lookup can select or compile any executable definition.
                    await preflight(admitted_child)
                plan = self.services.workflow_catalog.resolve(pinned)
        task_reference = str(state.variables.get("_task_reference", "unknown-task"))
        request_digest = effect_request_digest(
            "run:workflow", {"workflow_fingerprint": plan.fingerprint, "input": child_input}
        )
        if binding is not None and binding.request_digest != request_digest:
            raise _EffectRecoveryRequired("recorded child workflow request differs")
        if definition.get("await", True) is False and _RECONCILE_EXPIRED_RETRY.get():
            decision = replace(identity, operation="policy:detached-child:v1")
            recorded = self.services.effect_journal.get(decision)
            if recorded is None or recorded.status != "completed":
                raise WorkflowExecutionError("retry budget expired before detached child admission")
            if recorded.output["request_digest"] != request_digest:
                raise _EffectRecoveryRequired("recorded child workflow request differs")
            await self._notify_receipt(state, decision, recorded)
            return copy.deepcopy(recorded.output["returned_input"])
        child_id: str | None = None

        def observe_child(child: ExecutionHandle) -> None:
            nonlocal child_id
            if (
                child_id is not None
                or child.parent_invocation_id != state.metadata["invocation_id"]
                or child.parent_task_reference != task_reference
                or child.workflow_fingerprint != plan.fingerprint
            ):
                raise WorkflowExecutionError("child invocation identity is inconsistent")
            child_id = child.invocation_id

        def capture_child() -> ChildInvocationDependency:
            if child_id is None:
                raise WorkflowExecutionError("child invocation recovery binding is unavailable")
            return ChildInvocationDependency.capture(self.services.invocations, child_id)

        known = self.services.effect_journal.get(identity)
        if known is not None and known.status != "pending":
            parent = state.metadata.get("_invocation_handle")
            if binding is not None and isinstance(parent, ExecutionHandle):
                self.services.children.verify_control(binding, parent)
            if known.request_digest != effect_request_digest(
                "run:workflow", {"workflow_fingerprint": plan.fingerprint, "input": child_input}
            ):
                raise _EffectRecoveryRequired("recorded child workflow request differs")
            # Existing terminal receipts own their recorded child dependency.
            # Recovery must not reserve an extra orphan to replay that result.
            await self._notify_receipt(state, identity, known)
            return self._effect_result(known)
        parent_handle = state.metadata.get("_invocation_handle")
        if isinstance(parent_handle, ExecutionHandle):
            # Admit quotas before opening an external-action intent. A quota
            # rejection has created no child and is an ordinary policy fault.
            parent_child = (
                self.services.children.verify(binding, parent_handle, plan, child_input)
                if binding is not None
                else self.services.children.reserve(
                    identity, parent_handle, plan, child_input, task_reference=task_reference
                )
            )
            if parent_child.parent_task_reference != task_reference:
                raise _EffectRecoveryRequired("recorded child task reference changed")
            if definition.get("await", True) is False:
                receipt = self.services.children.enqueue(
                    identity, parent_handle, plan, child_input, copy.deepcopy(state.data)
                )
                decision = replace(identity, operation="policy:detached-child:v1")
                await self._notify_receipt(state, decision, receipt)
                return copy.deepcopy(receipt.output["returned_input"])
            child_context = {
                **state.metadata,
                "_child_invocation_handle": parent_child,
                "_child_invocation_observer": observe_child,
                "_child_effect_identity": identity,
                "_child_request_digest": request_digest,
            }
            deadlines = [
                scope.absolute_due
                for scope in _ACTIVE_TIMEOUTS.get()
                if scope.absolute_due is not None
            ]
            deadline_at = min(deadlines) if deadlines else None
            child_context["_child_deadline_at"] = deadline_at
            registration = self.services.child_waits.registration(identity)
            if known is not None and known.status == "pending":
                self.services.invocations.refresh(parent_child)
                if parent_child.status in {"completed", "faulted", "cancelled"}:
                    # A terminal common child can acknowledge cancellation while
                    # its dispatched external action remains uncertain. Recover
                    # internal child delivery only after proving that no such
                    # unresolved leaf intent survives in the recorded child tree.
                    unresolved_children = [parent_child]
                    visited_children: set[str] = set()
                    while unresolved_children:
                        recorded_child = unresolved_children.pop()
                        if recorded_child.invocation_id in visited_children:
                            continue
                        visited_children.add(recorded_child.invocation_id)
                        child_indexes = self.services.effect_journal.connection.execute(
                            "SELECT payload FROM workflow_effect_invocations WHERE invocation_id=?",
                            (recorded_child.invocation_id,),
                        ).fetchall()
                        for (encoded,) in child_indexes:
                            child_effect = EffectIdentity.from_value(json.loads(encoded))
                            if (
                                child_effect.invocation_id != recorded_child.invocation_id
                                or child_effect.workflow_fingerprint
                                != recorded_child.workflow_fingerprint
                            ):
                                raise _EffectRecoveryRequired(
                                    "child workflow action has an unresolved execution intent"
                                )
                            child_receipt = self.services.effect_journal.get(child_effect)
                            if child_receipt is None:
                                raise _EffectRecoveryRequired(
                                    "child workflow action has an unresolved execution intent"
                                )
                            if child_effect.operation != "run:workflow":
                                if child_receipt.status == "pending":
                                    raise _EffectRecoveryRequired(
                                        "child workflow action has an unresolved execution intent"
                                    )
                                continue
                            nested_binding = self.services.children.get(child_effect)
                            if nested_binding is None:
                                raise _EffectRecoveryRequired(
                                    "child workflow action has an unresolved execution intent"
                                )
                            unresolved_children.append(
                                self.services.children.verify_control(
                                    nested_binding, recorded_child
                                )
                            )
                if parent_child.status not in {"completed", "faulted", "cancelled"}:
                    if registration is None:
                        raise _EffectRecoveryRequired(
                            "workflow action has an unresolved execution intent"
                        )
                    wake = self.services.child_waits.wake(
                        registration, parent_handle, now=self._absolute_clock()
                    )
                    if wake is None:
                        await self._suspend_child(
                            state, parent_child, identity, registration.suspension.deadline_at
                        )
                    if wake["reason"] == "deadline":
                        error = WorkflowExecutionError(
                            "nested workflow exceeded its original deadline",
                            details={
                                "status": 408,
                                "instance": task_reference,
                                "type": "https://serverlessworkflow.io/spec/1.0.0/errors/timeout",
                            },
                        )
                        error.status_code = 408
                        raise error
                error_value: OwaError | None = None
                if registration is not None:
                    wake_identity = replace(identity, operation="policy:child-wake:v1")
                    wake_receipt = self.services.effect_journal.get(wake_identity)
                    if wake_receipt is None:
                        raise _EffectRecoveryRequired("child continuation has no recorded wake")
                    await self._notify_receipt(state, wake_identity, wake_receipt)
                result = None
                try:
                    result = await runner(plan, child_input, child_context, task_reference)
                except OwaError as error:
                    error_value = error
                receipt = self.services.effect_journal.finish_child(
                    identity,
                    request_digest,
                    parent_child.invocation_id,
                    execution_lease=parent_handle._execution_lease,
                    output=result,
                    error=error_value.as_dict() if error_value else None,
                    error_status_code=error_value.status_code if error_value else None,
                )
                if registration is not None:
                    self.services.child_waits.consume(registration, parent_handle)
                await self._notify_receipt(state, identity, receipt)
                return self._effect_result(receipt)
        else:
            child_context = {**state.metadata, "_child_invocation_observer": observe_child}
        # The resolved definition, not just its catalog label, determines the
        # child action request. Native progress may lag a known child outcome.
        operation = "run:workflow"
        try:
            return await self._run_effect(
                operation,
                {"workflow_fingerprint": plan.fingerprint, "input": child_input},
                state,
                lambda: runner(plan, child_input, child_context, task_reference),
                operation=operation,
                child_dependency=capture_child,
            )
        except ChildSuspension as suspension:
            await self._suspend_child(state, parent_child, identity, suspension.deadline_at)

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
        default_source = self._default_event_source(state)

        async def dispatch() -> dict[str, Any]:
            try:
                envelope = await self.services.event_bus.publish(
                    evaluated, default_source=default_source
                )
            except ValueError as exc:
                raise WorkflowSemanticError(str(exc)) from exc
            return cast(dict[str, Any], envelope.as_dict())

        handle = state.metadata.get("_invocation_handle")
        bus = self.services.event_bus
        if (
            isinstance(handle, ExecutionHandle)
            and getattr(bus, "durable", False)
            and hasattr(bus, "publish_workflow_event")
        ):
            identity = self._effect_identity(state, "emit")
            scope = handle.owner_principal or "anonymous"
            request_digest = effect_request_digest(
                "emit",
                {"properties": evaluated, "default_source": default_source, "scope": scope},
            )
            if _RECONCILE_EXPIRED_RETRY.get():
                receipt = self.services.effect_journal.get(identity)
                if receipt is None or receipt.status != "completed":
                    raise WorkflowExecutionError("retry budget expired before event publication")
                if receipt.request_digest != request_digest:
                    raise _EffectRecoveryRequired("recorded event publication request changed")
            else:
                receipt = await bus.publish_workflow_event(
                    identity,
                    request_digest,
                    evaluated,
                    default_source=default_source,
                    scope=scope,
                    execution_lease=handle._execution_lease,
                    operation_id=self._operation_id(state),
                )
            await self._notify_receipt(state, identity, receipt)
            envelope = self._effect_result(receipt)
        else:
            # Injected process-local transports retain their known publication
            # acknowledgement without claiming durable listener delivery.
            envelope = await self._run_effect(
                "emit",
                {"properties": evaluated, "default_source": default_source},
                state,
                dispatch,
                operation="emit",
            )
        task_reference = str(state.variables.get("_task_reference", "unknown-task"))
        task_name = state.variables.get("_task_name", "emit")
        self._emit(
            "EventEmitted",
            {
                **self._task_event(task_reference, task_name, state),
                "status": "completed",
                "event_id": envelope["id"],
                "event_name": envelope["type"],
            },
        )
        return state.data

    async def _run_listen(
        self, definition: Any, state: ExecutionState, iterator: Any = None
    ) -> Any:
        if self.services is None or not hasattr(self.services, "event_bus"):
            raise WorkflowExecutionError("event service unavailable for listen task")
        if not isinstance(definition, Mapping):
            raise WorkflowSemanticError("listen configuration must be an object")
        if not isinstance(definition.get("to"), Mapping):
            raise WorkflowSemanticError("listen.to must be an object")
        strategy = self._prepare_listener_strategy(definition["to"], state)
        read = definition.get("read", "data")
        if read not in {"data", "envelope", "raw"}:
            raise WorkflowSemanticError(f"unsupported listen read mode: {read}")
        bus = self.services.event_bus
        handle = state.metadata.get("_invocation_handle")
        if (
            isinstance(handle, ExecutionHandle)
            and getattr(bus, "durable", False)
            and hasattr(bus, "register_workflow_listener")
        ):
            return await self._run_durable_listen(strategy, read, iterator, state, handle)
        if set(strategy) != {"one"} or strategy["one"].get("correlate"):
            raise UnsupportedWorkflowFeature(
                "advanced event consumption requires the embedded durable event backend"
            )
        # Synchronous buses register before any waiting observation. Custom
        # process-local transports retain their public receive contract.
        pending = bus.receive(strategy)
        await self._listener_waiting(state)
        envelope = await self._await_with_cancellation(pending, state)
        self._token(state).checkpoint()
        self._listener_received(envelope, state)
        return [
            await self._listener_item(envelope, read, iterator, 1, state, durable_selection=False)
        ]

    def _prepare_listener_strategy(
        self, strategy: Mapping[str, Any], state: ExecutionState
    ) -> dict[str, Any]:
        prepared = copy.deepcopy(dict(strategy))
        for mode in ("one", "all", "any"):
            if mode not in prepared:
                continue
            filters = [prepared[mode]] if mode == "one" else prepared[mode]
            for event_filter in filters:
                for correlation in event_filter.get("correlate", {}).values():
                    if "expect" in correlation:
                        correlation["expect"] = self._expressions(state).evaluate(
                            correlation["expect"], state.data, variables=state.variables
                        )
        until = prepared.get("until")
        if isinstance(until, Mapping):
            prepared["until"] = self._prepare_listener_strategy(until, state)
        validate_expression_data(prepared)
        return prepared

    async def _run_durable_listen(
        self,
        strategy: Mapping[str, Any],
        read: str,
        iterator: Any,
        state: ExecutionState,
        handle: ExecutionHandle,
    ) -> list[Any]:
        bus = self.services.event_bus
        journal: EffectJournal = self.services.effect_journal
        identity = self._effect_identity(state, "listen")
        if _RECONCILE_EXPIRED_RETRY.get():
            known = journal.get(identity)
            if known is None or known.status != "completed":
                raise WorkflowExecutionError("retry budget expired before event consumption")
        registration: EventRegistration = bus.register_workflow_listener(
            identity,
            strategy,
            scope=handle.owner_principal or "anonymous",
            execution_lease=handle._execution_lease,
        )
        registered = journal.get(registration.receipt_identity)
        if registered is None or registered.status != "completed":
            raise _EffectRecoveryRequired("durable event registration evidence is missing")
        await self._notify_receipt(state, registration.receipt_identity, registered)
        progress: EventProgress = bus.listener_state(registration)
        if self._listener_complete_without_events(strategy) and not progress.completed:
            progress = bus.complete_workflow_listener(
                registration, execution_lease=handle._execution_lease
            )
        matcher = candidate_matcher(
            strategy,
            expressions=self._expressions(state),
            variables=state.variables,
            read=read,
            filter_guard=getattr(bus, "event_filter_allowed", None),
        )
        results: list[Any] = []
        processed = 0
        decisions = 0
        while True:
            self._token(state).checkpoint()
            # Include immutable routing decisions before native waiting/child
            # progress so partial collection cannot change during recovery.
            decision_count = progress.decision_ordinal
            while decisions < decision_count:
                decisions += 1
                decision_identity = registration.decision_identity(decisions)
                decision = journal.get(decision_identity)
                if decision is None or decision.status != "completed":
                    raise _EffectRecoveryRequired("durable event decision evidence is missing")
                await self._notify_receipt(state, decision_identity, decision)
            while processed < len(progress.events):
                ordinal = processed + 1
                event = progress.events[processed]
                event_identity = registration.event_identity(ordinal)
                selected = journal.get(event_identity)
                if selected is None or selected.status != "completed":
                    raise _EffectRecoveryRequired("durable event selection evidence is missing")
                await self._notify_receipt(state, event_identity, selected)
                self._listener_received(event, state)
                results.append(await self._listener_item(event, read, iterator, ordinal, state))
                processed += 1
                if state.stop or state.flow.ended.is_set():
                    return results
            if progress.completed:
                if progress.receipt is None:
                    raise _EffectRecoveryRequired("durable event completion evidence is missing")
                await self._notify_receipt(state, identity, progress.receipt)
                # A terminating event is excluded from the output and therefore
                # does not pass through _listener_received. Completion still
                # resumes the invocation before its terminal transition.
                self._set_invocation_status(state, "running")
                return results
            if progress.closed:
                raise InvocationCancelled("event listener belongs to a terminated invocation")
            await self._listener_waiting(state)
            progress = await self._await_with_cancellation(
                bus.receive_next(
                    registration,
                    execution_lease=handle._execution_lease,
                    evaluate_candidate=matcher,
                ),
                state,
            )

    @staticmethod
    def _listener_complete_without_events(strategy: Mapping[str, Any]) -> bool:
        if "all" in strategy:
            return not strategy["all"]
        until = strategy.get("until")
        return (
            "any" in strategy
            and isinstance(until, Mapping)
            and WorkflowExecutor._listener_complete_without_events(until)
        )

    async def _listener_item(
        self,
        event: EventEnvelope,
        read: str,
        iterator: Any,
        ordinal: int,
        state: ExecutionState,
        *,
        durable_selection: bool = True,
    ) -> Any:
        value = event_projection(event, read)
        if iterator is None:
            return value
        if not isinstance(iterator, Mapping):
            raise WorkflowSemanticError("listen foreach must be an object")
        child = self._copy_state(state)
        child.data = copy.deepcopy(value)
        child.variables["input"] = copy.deepcopy(value)
        child.variables[str(iterator.get("item", "item"))] = copy.deepcopy(value)
        child.variables[str(iterator.get("at", "index"))] = ordinal - 1
        reference = str(child.variables.get("_execution_reference", "/do"))
        if durable_selection:
            child.execution_path = (*child.execution_path, (f"{reference}/@event", ordinal))
        # A nondurable bus admits only one event. Its existing listener task
        # occurrence identifies the sole child; it has no durable selection
        # receipt to justify an additional event ordinal in native state.
        body = iterator.get("do", [])
        if body:
            await self._run_tasks(body, child, prefix=self._nested_prefix(child, "foreach/do"))
        output = self._apply_task_data(iterator, child.data, child)
        state.context = copy.deepcopy(child.context)
        state.variables["context"] = state.context
        state.task_visits = child.task_visits
        state.stop = child.stop
        return output

    async def _listener_waiting(self, state: ExecutionState) -> None:
        task_reference = str(state.variables.get("_task_reference", "unknown-task"))
        task_name = state.variables.get("_task_name", "listen")
        await self._notify_waiting(state)
        self._set_invocation_status(state, "waiting")
        waiting_event = {
            **self._task_event(task_reference, task_name, state),
            "status": "waiting",
            "progress": {"phase": "listening"},
        }
        self._emit("TaskProgress", waiting_event)
        self._emit("TaskWaiting", waiting_event)
        self._emit("WorkflowWaiting", waiting_event)

    def _listener_received(self, envelope: EventEnvelope, state: ExecutionState) -> None:
        task_reference = str(state.variables.get("_task_reference", "unknown-task"))
        task_name = state.variables.get("_task_name", "listen")
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
        self._validate_data_schema(output_spec, output, "task output", state)
        state.variables["output"] = copy.deepcopy(output)
        export_spec = definition.get("export")
        exported = (
            self._expressions(state).evaluate(export_spec["as"], output, variables=state.variables)
            if isinstance(export_spec, Mapping) and "as" in export_spec
            else copy.deepcopy(state.context)
        )
        self._validate_data_schema(export_spec, exported, "task export", state)
        state.context = copy.deepcopy(exported)
        state.variables["context"] = state.context
        return copy.deepcopy(output)

    def _validate_data_schema(
        self, specification: Any, value: Any, label: str, state: ExecutionState | None = None
    ) -> None:
        if not isinstance(specification, Mapping):
            return
        schema = specification.get("schema")
        if not isinstance(schema, Mapping):
            return
        document = schema.get("document")
        if not isinstance(document, (Mapping, bool)):
            return
        errors = sorted(
            Draft202012Validator(
                document,
                registry=state.metadata.get("_schema_registry", Registry())
                if state
                else Registry(),
            ).iter_errors(value),
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
                    "type": "https://serverlessworkflow.io/spec/1.0.0/errors/validation",
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
        parent = str(
            state.variables.get(
                "_execution_reference", state.variables.get("_task_reference", "/do")
            )
        )
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

    def _set_invocation_status(self, state: ExecutionState, status: str, **changes: Any) -> None:
        handle = state.metadata.get("_invocation_handle")
        if handle is not None and self.services is not None:
            self.services.invocations.update(
                handle,
                status=status,
                completed_at=self._absolute_clock()
                if status in {"completed", "faulted", "cancelled"}
                else None,
                **changes,
            )

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
            "operation_id": self._operation_id(state)
            if state.variables.get("_task_reference") == reference
            else f"{context.get('invocation_id', 'unknown-invocation')}:{reference}",
            "parent_invocation_id": context.get("parent_invocation_id"),
            "parent_task_reference": context.get("parent_task_reference"),
        }

    @staticmethod
    def _legacy_operation_id(state: ExecutionState) -> str:
        invocation = state.metadata.get("invocation_id", "unknown-invocation")
        reference = state.variables.get(
            "_execution_reference", state.variables.get("_task_reference", "unknown-task")
        )
        return f"{invocation}:{reference}"

    @staticmethod
    @contextmanager
    def _operation_visit_scope(state: ExecutionState) -> Iterator[None]:
        """Keep child visit ordinals relative to this attempt's entry boundary.

        Physical task counters remain native/common replay evidence. Generated
        receiver keys identify a logical action instead: a try retry restarts
        its child ordinals, while a loop within that attempt keeps each visit.
        These private live baselines are rebuilt during normal execution; they
        neither replace checkpoints nor change admitted effect identities.
        """
        previous = state.metadata.get("_operation_visit_scopes", ())
        reference = str(
            state.variables.get("_execution_reference", state.variables.get("_task_reference", ""))
        )
        state.metadata["_operation_visit_scopes"] = (
            *previous,
            (reference, dict(state.task_visits)),
        )
        try:
            yield
        finally:
            if previous:
                state.metadata["_operation_visit_scopes"] = previous
            else:
                state.metadata.pop("_operation_visit_scopes", None)

    @staticmethod
    def _logical_operation_path(state: ExecutionState) -> tuple[tuple[str, int], ...]:
        scopes = cast(
            tuple[tuple[str, Mapping[str, int]], ...],
            state.metadata.get("_operation_visit_scopes", ()),
        )
        logical: list[tuple[str, int]] = []
        for reference, visit in state.execution_path:
            if reference.endswith(("/@attempt", "/@catch-attempt")):
                continue
            for parent, baseline in reversed(scopes):
                if reference.startswith(parent + "/"):
                    visit -= baseline.get(reference, 0)
                    break
            if visit < 1:
                raise _EffectRecoveryRequired("logical action occurrence is inconsistent")
            logical.append((reference, visit))
        return tuple(logical)

    @staticmethod
    def _operation_id(state: ExecutionState) -> str:
        legacy = WorkflowExecutor._legacy_operation_id(state)
        logical = WorkflowExecutor._logical_operation_path(state)
        if not any(visit > 1 for _, visit in logical):
            # Retain the original first-visit key and diagnostic suffix. Existing
            # admitted receipts separately retain their original keys on replay.
            return legacy
        invocation = str(state.metadata.get("invocation_id", "unknown-invocation"))
        reference = str(
            state.variables.get(
                "_execution_reference", state.variables.get("_task_reference", "unknown-task")
            )
        )
        digest = effect_request_digest(
            "owa/logical-operation/v1",
            {
                "invocation_id": invocation,
                "fingerprint": state.metadata.get("_workflow_fingerprint"),
                "occurrence": [[reference, visit] for reference, visit in logical],
            },
        )
        return f"{invocation}:visit-v1:{digest}:{reference}"

    def _protocol_operation_id(
        self, call: str, payload: Mapping[str, Any], state: ExecutionState
    ) -> Any:
        explicit = payload.get("operation_id") or payload.get("operationId")
        if explicit:
            return explicit
        journal = getattr(self.services, "effect_journal", None)
        if journal is not None and "_workflow_fingerprint" in state.metadata:
            known = journal.get(self._effect_identity(state, "call:" + call))
            if known is not None:
                # A persisted intent predates current key generation. Retain
                # its complete prepared request, including the original key.
                return known.operation_id or self._legacy_operation_id(state)
        catalog_key = state.metadata.get("_catalog_operation_id")
        if catalog_key:
            reference = state.variables.get(
                "_execution_reference", state.variables.get("_task_reference")
            )
            if reference in state.metadata.get("_catalog_tasks", {}):
                return catalog_key
            digest = effect_request_digest(
                "owa/catalog-logical-operation/v1",
                {
                    "parent_key": catalog_key,
                    "reference": reference,
                    "occurrence": [
                        [reference, visit]
                        for reference, visit in self._logical_operation_path(state)
                    ],
                },
            )
            return f"{catalog_key}:visit-v1:{digest}:{reference}"
        return self._operation_id(state)


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

    def validate_strategy(value: Any, location: str) -> None:
        if not isinstance(value, Mapping):
            raise WorkflowSemanticError(f"listen strategy must be an object at {location}")
        modes = [mode for mode in ("one", "all", "any") if mode in value]
        if len(modes) != 1:
            raise WorkflowSemanticError(f"listen requires one consumption strategy at {location}")
        mode = modes[0]
        filters = [value[mode]] if mode == "one" else value[mode]
        if not isinstance(filters, list):
            raise WorkflowSemanticError(f"listen.{mode} must be an array at {location}")
        for index, selected in enumerate(filters):
            if (
                not isinstance(selected, Mapping)
                or not isinstance(selected.get("with"), Mapping)
                or not selected["with"]
            ):
                raise WorkflowSemanticError(f"event filter.with is required at {location}/{index}")
            correlations = selected.get("correlate", {})
            if not isinstance(correlations, Mapping):
                raise WorkflowSemanticError(f"event correlations must be an object at {location}")
            for rule in correlations.values():
                if not isinstance(rule, Mapping) or not isinstance(rule.get("from"), str):
                    raise WorkflowSemanticError(f"correlation.from is required at {location}")
        until = value.get("until")
        if mode == "any" and isinstance(until, Mapping):
            validate_strategy(until, f"{location}/until")
        elif mode == "any" and until is not None and not isinstance(until, str):
            raise WorkflowSemanticError(
                f"listen.until must be an expression or strategy at {location}"
            )

    validate_strategy(strategy, f"{reference}/listen/to")


def _validate_run_capability(definition: Any, reference: str) -> None:
    if not isinstance(definition, Mapping):
        raise WorkflowSemanticError(f"run configuration must be an object at {reference}")
    detached_workflow = definition.get("await", True) is False and "workflow" in definition
    if definition.get("await", True) is not True and not detached_workflow:
        _unsupported("background run", f"{reference}/run/await")
    if "return" in definition and not detached_workflow:
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
            return {
                name: copy.deepcopy(item)
                if name in PROTOCOL_DATA_FIELDS or name == "headers"
                else resolve(item, str(name))
                for name, item in value.items()
            }
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
            "type": error_details.get("type", error.get("type", error.get("code"))),
            "status": error_details.get("status", error.get("status", error.get("status_code"))),
            "instance": error_details.get("instance", error.get("instance")),
            "title": error_details.get("title", error.get("title", error.get("code"))),
            "detail": error_details.get("detail", error.get("detail", error.get("message"))),
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
    if "delay" not in retry and "jitter" not in retry:
        return None
    return RetryPolicy.from_value(retry, duration=_duration_seconds).delay_for(
        attempt, random.uniform
    )


def _error_details(error: Exception) -> dict[str, Any]:
    if hasattr(error, "as_dict"):
        return cast(dict[str, Any], error.as_dict())
    return {"code": "workflow_execution_error", "message": str(error), "details": {}}


def _catch_error_value(
    error: Mapping[str, Any], *, status_code: int | None = None
) -> dict[str, Any]:
    """Expose OWS error fields to catch handlers without changing API errors."""

    value = dict(error)
    details = value.pop("details", None)
    if isinstance(details, Mapping):
        value.update(details)
    if status_code is not None:
        value.setdefault("status", status_code)
        category = {
            "configuration_error": "configuration",
            "unsupported_workflow_feature": "configuration",
            "workflow_schema_error": "validation",
            "workflow_semantic_error": "validation",
            "expression_error": "expression",
            "model_error": "communication",
            "tool_error": "communication",
            "approval_authorization_error": "authorization",
        }.get(str(error.get("code")), "runtime")
        if value["status"] == 401:
            category = "authentication"
        elif value["status"] == 403:
            category = "authorization"
        value.setdefault("type", "https://serverlessworkflow.io/spec/1.0.0/errors/" + category)
        value.setdefault("title", error.get("code", "workflow_execution_error"))
        value.setdefault("detail", error.get("message", "workflow execution failed"))
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
    iterator = definition.get("foreach") if "listen" in definition else None
    if isinstance(iterator, Mapping) and isinstance(iterator.get("do"), list):
        yield "foreach/do", iterator["do"]
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
