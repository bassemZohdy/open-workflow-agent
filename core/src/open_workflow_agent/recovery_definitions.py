"""Immutable public definition inputs, exact artifact continuation, no checkpoints."""

from __future__ import annotations

import asyncio
import copy
import csv
import hashlib
import importlib.metadata
import importlib.util
import io
import json
import os
import platform
import re
import stat
import sys
import threading
import tomllib
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit
from urllib.request import url2pathname

from ._effect_storage import insert_completed_receipt
from .config import RuntimeConfig
from .effects import EffectIdentity, EffectJournal, EffectReceipt, effect_request_digest
from .errors import (
    InvocationStateConflict,
    InvocationStateError,
    OwaError,
    WorkflowDefinitionChanged,
)
from .external_catalog import ResolvedCatalogFunction
from .ownership import ExecutionLease, owned_write_transaction
from .persistence import ExecutionHandle, InvocationStore
from .resources import SchemaResource
from .storage import StorageConnection
from .workflow import WorkflowPlan, normalize_workflow, validate_schema

PROFILE = "owa/definition-continuation/v1"
ADMISSION_OPERATION = "policy:definition-admission:v1"
MAX_ARCHIVE_BYTES = 64_000_000
MAX_DEPENDENCIES = 128
MAX_PROFILE_BYTES = 1_000_000
_SHA256 = re.compile(r"[a-f0-9]{64}")
_ADAPTERS = {
    "adk": ("open-workflow-agent-adk", "open_workflow_agent_adk"),
    "langgraph": ("open-workflow-agent-langgraph", "open_workflow_agent_langgraph"),
    "agent-framework": (
        "open-workflow-agent-agent-framework",
        "open_workflow_agent_agent_framework",
    ),
}
_NATIVE_SDKS = {
    "adk": "google-adk",
    "langgraph": "langgraph",
    "agent-framework": "agent-framework-core",
}


def _unavailable(reason: str) -> InvocationStateError:
    return InvocationStateError(
        "original workflow recovery evidence is unavailable or inconsistent",
        details={"reason": reason},
    )


def _encoded(value: Any, *, limit: int = MAX_ARCHIVE_BYTES) -> str:
    effect_request_digest(PROFILE, value)
    encoded = json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )
    if len(encoded.encode("utf-8")) > limit:
        raise _unavailable("archive_limit_exceeded")
    return encoded


def _parsed(encoded: Any, *, limit: int = MAX_ARCHIVE_BYTES) -> Any:
    if not isinstance(encoded, str) or len(encoded.encode("utf-8")) > limit:
        raise _unavailable("archive_limit_exceeded")

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate field")
            value[key] = item
        return value

    value = json.loads(encoded, object_pairs_hook=unique)
    _encoded(value, limit=limit)
    return value


def _digest(value: Any) -> str:
    return hashlib.sha256(_encoded(value).encode("utf-8")).hexdigest()


def _file_bytes(path: Path, limit: int) -> bytes:
    with path.open("rb") as source:
        before = os.fstat(source.fileno())
        if before.st_size < 0 or before.st_size > limit:
            raise _unavailable("runtime_profile_unavailable")
        # A package can contain many tiny files. Allocate for the current file,
        # not the full remaining package budget, and reject concurrent changes.
        value = source.read(before.st_size + 1)
        after = os.fstat(source.fileno())
        if len(value) != before.st_size or (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise _unavailable("runtime_profile_unavailable")
    return value


def _code_digest(package: str) -> str:
    # find_spec locates the adapter package without importing it or its SDK.
    spec = importlib.util.find_spec(package)
    if spec is None or spec.origin is None:
        raise _unavailable("runtime_profile_unavailable")
    root = Path(spec.origin).parent.resolve()
    paths = sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix in {".py", ".json", ".yaml", ".yml", ".toml"}
        and "__pycache__" not in path.parts
    )
    if not paths or len(paths) > 4096:
        raise _unavailable("runtime_profile_unavailable")
    digest, size = hashlib.sha256(), 0
    for path in paths:
        relative = path.resolve().relative_to(root).as_posix()
        content = _file_bytes(path, 128_000_000 - size)
        size += len(content)
        if size > 128_000_000:
            raise _unavailable("runtime_profile_unavailable")
        digest.update(_encoded([relative, hashlib.sha256(content).hexdigest()]).encode())
    return digest.hexdigest()


def _editable_project(installed: importlib.metadata.Distribution) -> Path:
    value = _parsed(installed.read_text("direct_url.json"), limit=MAX_PROFILE_BYTES)
    if not isinstance(value, dict) or value.get("dir_info", {}).get("editable") is not True:
        raise _unavailable("runtime_profile_unavailable")
    parsed = urlsplit(value.get("url", ""))
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        raise _unavailable("runtime_profile_unavailable")
    return Path(url2pathname(parsed.path)).resolve()


def _project_digest(project: Path, name: str) -> str:
    data = _file_bytes(project / name, 16_000_000).replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def _root_editable_adapter(engine: str, core: str) -> dict[str, Any]:
    """Root development fallback only; never infer provenance from adjacent files."""
    if core != "open-workflow-agent":
        raise _unavailable("runtime_profile_unavailable")
    root_distribution = importlib.metadata.distribution(core)
    project = _editable_project(root_distribution)
    distribution, package = _ADAPTERS[engine]
    engine_project = project / "engines" / engine
    expected = engine_project / "src" / package / "__init__.py"
    spec = importlib.util.find_spec(package)
    if (
        spec is None
        or spec.origin is None
        or not expected.resolve().is_relative_to(project)
        or Path(spec.origin).resolve() != expected.resolve()
        or any(
            not (engine_project / name).resolve().is_relative_to(engine_project.resolve())
            for name in ("pyproject.toml", "uv.lock")
        )
    ):
        raise _unavailable("runtime_profile_unavailable")
    metadata = tomllib.loads(_file_bytes(engine_project / "pyproject.toml", 16_000_000).decode())
    declaration = metadata.get("project", {})
    if declaration.get("name") != distribution or not isinstance(declaration.get("version"), str):
        raise _unavailable("runtime_profile_unavailable")
    return {
        "distribution": distribution,
        "version": declaration["version"],
        "provenance": "owa/root-editable-adapter-source/v1",
        "root_distribution": core,
        "root_version": root_distribution.version,
        "lock_sha256": _project_digest(engine_project, "uv.lock"),
        "metadata_sha256": _project_digest(engine_project, "pyproject.toml"),
        "code_sha256": _code_digest(package),
    }


def _artifact(distribution: str, package: str) -> dict[str, Any]:
    installed = importlib.metadata.distribution(distribution)
    marker = Path(str(installed.locate_file(package + "/_recovery_build.json")))
    if marker.is_file():
        value = _parsed(
            _file_bytes(marker, MAX_PROFILE_BYTES).decode("utf-8"), limit=MAX_PROFILE_BYTES
        )
        if (
            not isinstance(value, dict)
            or value.get("profile") != "owa/recovery-build/v1"
            or value.get("project") != distribution
            or value.get("version") != installed.version
            or not all(
                isinstance(value.get(key), str) and _SHA256.fullmatch(value[key])
                for key in ("lock_sha256", "metadata_sha256")
            )
        ):
            raise _unavailable("runtime_profile_unavailable")
    else:
        # Editable provenance is explicit installed metadata. A production wheel
        # without its build marker cannot fall back to a convenient checkout.
        project = _editable_project(installed)
        value = {
            "lock_sha256": _project_digest(project, "uv.lock"),
            "metadata_sha256": _project_digest(project, "pyproject.toml"),
        }
    return {
        "distribution": distribution,
        "version": installed.version,
        "lock_sha256": value["lock_sha256"],
        "metadata_sha256": value["metadata_sha256"],
        "code_sha256": _code_digest(package),
    }


@lru_cache(maxsize=64)
def _sdk_file_digest(entries: tuple[tuple[str, str, int, int], ...]) -> str:
    digest = hashlib.sha256()
    for relative, path, _modified, size in entries:
        content = _file_bytes(Path(path), size)
        if len(content) != size:
            raise _unavailable("runtime_profile_unavailable")
        digest.update(_encoded([relative, hashlib.sha256(content).hexdigest()]).encode())
    return digest.hexdigest()


@lru_cache(maxsize=32)
def _record_paths(digest: str, metadata_root: str, content: bytes) -> tuple[str, ...]:
    """Cache only a bounded installed inventory, never physical file evidence."""
    if hashlib.sha256(content).hexdigest() != digest or not metadata_root:
        raise _unavailable("runtime_profile_unavailable")
    rows = csv.reader(io.StringIO(content.decode("utf-8", errors="strict"), newline=""))
    paths: list[str] = []
    for row in rows:
        if len(row) != 3 or not row[0] or "\x00" in row[0] or len(row[0]) > 4096:
            raise _unavailable("runtime_profile_unavailable")
        if len(paths) >= 65_536:
            raise _unavailable("runtime_profile_unavailable")
        paths.append(row[0])
    if not paths or len(paths) != len(set(paths)):
        raise _unavailable("runtime_profile_unavailable")
    return tuple(sorted(paths))


def _sdk_paths(installed: importlib.metadata.Distribution) -> tuple[str, ...]:
    if isinstance(installed, importlib.metadata.PathDistribution):
        # PathDistribution is the actual wheel metadata provider. Its metadata
        # directory identifies this selected installation without scanning an
        # arbitrary checkout or skipping missing installed files via .files.
        try:
            metadata_root = Path(str(cast(Any, installed)._path)).resolve()
            content = _file_bytes(metadata_root / "RECORD", 4_000_000)
            return _record_paths(hashlib.sha256(content).hexdigest(), str(metadata_root), content)
        except (OSError, UnicodeError, csv.Error):
            raise _unavailable("runtime_profile_unavailable") from None
    # Preserve the existing custom distribution seam without caching it.
    files = installed.files
    if files is None:
        raise _unavailable("runtime_profile_unavailable")
    return tuple(sorted(str(item) for item in files))


def _sdk_digest(name: str) -> str:
    """Bounded checkpoint/protocol code, including declared gRPC codec binaries."""
    installed = importlib.metadata.distribution(name)
    entries: list[tuple[str, str, int, int]] = []
    size = 0
    for item in _sdk_paths(installed):
        relative = str(item).replace("\\", "/")
        suffixes = (
            (".py", ".pyi", ".so", ".pyd", ".dll", ".dylib")
            if name in {"grpcio", "protobuf"}
            else (".py", ".pyi")
        )
        if not relative.endswith(suffixes) or "__pycache__" in relative:
            continue
        path = Path(str(installed.locate_file(item)))
        try:
            info = path.stat()
        except OSError:
            raise _unavailable("runtime_profile_unavailable") from None
        if not stat.S_ISREG(info.st_mode):
            raise _unavailable("runtime_profile_unavailable")
        size += info.st_size
        if len(entries) >= 4096 or size > (
            128_000_000 if name in {"grpcio", "protobuf"} else 32_000_000
        ):
            raise _unavailable("runtime_profile_unavailable")
        entries.append((relative, str(path), info.st_mtime_ns, info.st_size))
    if not entries:
        raise _unavailable("runtime_profile_unavailable")
    # Re-stat on every profile read. Content caches are invalidated when the
    # installed file inventory, modification time or size changes.
    return _sdk_file_digest(tuple(entries))


def recovery_profile(
    engine: str, *, backend: str, config: RuntimeConfig | None = None
) -> dict[str, Any]:
    """Read qualified installed artifacts; no Git, SDK import, secret resolution or I/O client."""
    try:
        if backend not in {"sqlite", "postgresql"}:
            raise _unavailable("runtime_profile_unavailable")
        core = "open-workflow-agent"
        try:
            importlib.metadata.distribution(core)
        except importlib.metadata.PackageNotFoundError:
            core = "open-workflow-agent-core"
        artifacts = [_artifact(core, "open_workflow_agent")]
        adapter_mode = "common"
        if engine in _ADAPTERS:
            try:
                importlib.metadata.distribution(_NATIVE_SDKS[engine])
                adapter_mode = "native"
            except importlib.metadata.PackageNotFoundError:
                adapter_mode = "common_fallback"
            try:
                artifacts.append(_artifact(*_ADAPTERS[engine]))
            except importlib.metadata.PackageNotFoundError:
                if adapter_mode != "common_fallback":
                    raise _unavailable("runtime_profile_unavailable") from None
                artifacts.append(_root_editable_adapter(engine, core))
        elif engine not in {"base", "reference", "portable"}:
            raise _unavailable("runtime_profile_unavailable")
        relevant = {
            "jq",
            "jsonschema",
            "referencing",
            "pydantic",
            "pydantic-core",
            "httpx",
            "PyYAML",
            "croniter",
            "tzdata",
            "mcp",
            "mcp-types",
            "a2a-sdk",
            "grpcio",
            "protobuf",
            "googleapis-common-protos",
            "json-rpc",
            "anyio",
        }
        if engine == "adk":
            relevant.update({"google-adk", "google-genai", "sqlalchemy", "aiosqlite", "asyncpg"})
        elif engine == "langgraph":
            relevant.update(
                {
                    "langgraph",
                    "langgraph-checkpoint",
                    "langchain-core",
                    "ormsgpack",
                    "langgraph-checkpoint-postgres"
                    if backend == "postgresql"
                    else "langgraph-checkpoint-sqlite",
                }
            )
        elif engine == "agent-framework":
            relevant.update({"agent-framework", "agent-framework-core"})
        if backend == "postgresql":
            relevant.update({"psycopg", "psycopg-binary", "psycopg-pool"})
        versions: dict[str, str | None] = {}
        for name in sorted(relevant):
            try:
                versions[name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                versions[name] = None
        checkpoint_packages = {
            "adk": {"google-adk", "google-genai", "sqlalchemy", "aiosqlite", "asyncpg"},
            "langgraph": {
                "langgraph",
                "langgraph-checkpoint",
                "langchain-core",
                "ormsgpack",
                "langgraph-checkpoint-postgres"
                if backend == "postgresql"
                else "langgraph-checkpoint-sqlite",
            },
            "agent-framework": {"agent-framework-core"},
        }.get(engine, set()) | {
            "mcp",
            "mcp-types",
            "a2a-sdk",
            "grpcio",
            "protobuf",
            "googleapis-common-protos",
        }
        sdk_code = {
            name: _sdk_digest(name)
            for name in sorted(checkpoint_packages)
            if versions.get(name) is not None
        }
        policy: dict[str, Any] = {}
        continuation_supported = True
        if config is not None:
            continuation_supported = not config.model.options and not any(
                tool.options
                and not (
                    tool.type == "openapi"
                    and tool.openapi_binding is not None
                    and not (set(tool.options) - {"operationId", "output", "redirect"})
                )
                for tool in config.tools
            )
            protocol_policy = config.protocols.model_dump(mode="json", exclude={"grpc_bindings"})
            protocol_policy["grpc_bindings"] = [
                binding.recovery_snapshot(name)
                for name, binding in sorted(config.protocols.grpc_bindings.items())
            ]
            agent_policy = config.agent.model_dump(mode="json")
            if not config.agent.roles:
                agent_policy.pop("roles")
            policy = {
                "agent": agent_policy,
                "security": config.security.model_dump(mode="json"),
                "execution_authorization": config.execution_authorization.model_dump(mode="json"),
                "protocols": protocol_policy,
                "catalogs": {
                    key: value.model_dump(mode="json")
                    for key, value in config.workflow.external_catalogs.items()
                },
                "schemas": {
                    key: value.model_dump(mode="json")
                    for key, value in config.workflow.external_resources.items()
                },
                "sandbox": {
                    **config.sandbox.model_dump(mode="json", exclude={"script_sources"}),
                    "script_sources": [
                        item.model_dump(mode="json", exclude={"code", "profile"})
                        for item in config.sandbox.script_sources
                    ],
                },
                "timing": config.execution_timing.model_dump(mode="json"),
                "children": config.child_workflows.model_dump(mode="json"),
                "events": config.events.model_dump(mode="json"),
                "memory_enabled": config.memory.enabled,
                "model": {
                    "provider": config.model.provider,
                    "name": config.model.name,
                    "temperature": config.model.temperature,
                },
                "tools": [
                    {
                        "type": tool.type,
                        "name": tool.name,
                        "endpoint": tool.endpoint,
                        "security_profile": tool.security_profile,
                        "openapi_binding": {
                            "id": tool.openapi_binding.id,
                            "version": tool.openapi_binding.version,
                            "document_uri": tool.openapi_binding.document_uri,
                            "digest": tool.openapi_binding.digest,
                            "operationId": tool.options.get("operationId"),
                            "output": tool.options.get("output", "content"),
                            "redirect": tool.options.get("redirect", False),
                        }
                        if tool.openapi_binding is not None
                        else None,
                    }
                    for tool in config.tools
                ],
            }
        value = {
            "profile": PROFILE,
            "engine": engine,
            "backend": backend,
            "continuation_supported": continuation_supported,
            "common_schema": "owa-runtime-v1",
            "adapter_mode": adapter_mode,
            "native_authority_namespace": "owa/principal-native/v1" if engine == "adk" else None,
            "native_binding": (
                "common-fallback-v1"
                if adapter_mode == "common_fallback"
                else {
                    "adk": "adk-receipts-v1",
                    "langgraph": "langgraph-receipts-v2",
                }.get(engine, "common-reference-v1")
            ),
            "python": {
                "version": platform.python_version(),
                "abi": sys.implementation.cache_tag,
                "implementation": platform.python_implementation(),
                "os": platform.system(),
                "architecture": platform.machine(),
            },
            "artifacts": artifacts,
            "dependencies": versions,
            "sdk_code": sdk_code,
            "policy": policy,
        }
        _encoded(value, limit=MAX_PROFILE_BYTES)
        return value
    except InvocationStateError:
        raise _unavailable("runtime_profile_unavailable") from None
    except Exception:
        raise _unavailable("runtime_profile_unavailable") from None


def _identity(handle: ExecutionHandle) -> dict[str, Any]:
    return {
        key: getattr(handle, key)
        for key in (
            "invocation_id",
            "engine",
            "engine_execution_reference",
            "user_id",
            "session_id",
            "workflow_name",
            "workflow_version",
            "workflow_fingerprint",
            "owner_principal",
            "parent_invocation_id",
            "parent_task_reference",
        )
    }


def _definition(plan: WorkflowPlan) -> dict[str, Any]:
    if (
        len(plan.imports) > MAX_DEPENDENCIES
        or len(plan.resources) > MAX_DEPENDENCIES
        or len(plan.script_resources) > MAX_DEPENDENCIES
    ):
        raise _unavailable("archive_limit_exceeded")
    size = len(plan.source_json.encode("utf-8"))
    for item in plan.imports:
        size += len(item.definition_json.encode("utf-8"))
        size += len((item.execution_definition_json or item.definition_json).encode("utf-8"))
    size += sum(len(item.document_json.encode("utf-8")) for item in plan.resources)
    size += sum(len(item.source.verified_bytes()) for item in plan.script_resources)
    if size > MAX_ARCHIVE_BYTES:
        raise _unavailable("archive_limit_exceeded")
    return {
        "profile": "owa/definition-archive/v1",
        "fingerprint": plan.fingerprint,
        "namespace": plan.namespace,
        "name": plan.name,
        "version": plan.version,
        "source": _parsed(plan.source_json),
        "imports": [
            {
                "reference": item.reference,
                "resource_uri": item.resource_uri,
                "digest": item.digest,
                "definition": _parsed(item.definition_json),
                "execution_definition": _parsed(
                    item.execution_definition_json or item.definition_json
                ),
            }
            for item in plan.imports
        ],
        "resources": [
            {
                "uri": item.uri,
                "digest": item.digest,
                "document": _parsed(item.document_json),
                "size_bytes": item.size_bytes,
            }
            for item in plan.resources
        ],
        **(
            {"script_resources": [item.as_value() for item in plan.script_resources]}
            if plan.script_resources
            else {}
        ),
    }


def _plan(value: Any, *, enabled_function_calls: frozenset[str] = frozenset()) -> WorkflowPlan:
    if (
        not isinstance(value, dict)
        or set(value) - {"script_resources"}
        != {
            "profile",
            "fingerprint",
            "namespace",
            "name",
            "version",
            "source",
            "imports",
            "resources",
        }
        or value["profile"] != "owa/definition-archive/v1"
    ):
        raise _unavailable("archive_invalid")
    if not all(
        isinstance(value[key], list) and len(value[key]) <= MAX_DEPENDENCIES
        for key in ("imports", "resources")
    ):
        raise _unavailable("archive_invalid")
    imports: list[ResolvedCatalogFunction] = []
    resources: list[SchemaResource] = []
    for item in value["imports"]:
        if (
            not isinstance(item, dict)
            or set(item)
            != {"reference", "resource_uri", "digest", "definition", "execution_definition"}
            or not isinstance(item["digest"], str)
            or not _SHA256.fullmatch(item["digest"])
        ):
            raise _unavailable("archive_invalid")
        imports.append(
            ResolvedCatalogFunction(
                item["reference"],
                item["resource_uri"],
                item["digest"],
                _encoded(item["definition"]),
                _encoded(item["execution_definition"]),
            )
        )
    for item in value["resources"]:
        if (
            not isinstance(item, dict)
            or set(item) != {"uri", "digest", "document", "size_bytes"}
            or not isinstance(item["digest"], str)
            or not _SHA256.fullmatch(item["digest"])
            or type(item["size_bytes"]) is not int
            or not 0 <= item["size_bytes"] <= MAX_ARCHIVE_BYTES
        ):
            raise _unavailable("archive_invalid")
        resources.append(
            SchemaResource(
                item["uri"], item["digest"], _encoded(item["document"]), item["size_bytes"]
            )
        )
    if len({item.reference for item in imports}) != len(imports) or len(
        {item.uri for item in resources}
    ) != len(resources):
        raise _unavailable("archive_invalid")
    validate_schema(value["source"])

    def role_calls(item: Any) -> None:
        if isinstance(item, dict):
            call = item.get("call")
            if (
                isinstance(call, str)
                and call.startswith(("agent-", "llm-"))
                and call.endswith("@default")
                and call not in enabled_function_calls
            ):
                raise _unavailable("archived_role_binding_missing")
            for child in item.values():
                role_calls(child)
        elif isinstance(item, list):
            for child in item:
                role_calls(child)

    role_calls(value["source"])
    from .executable_sources import ExecutableScriptBinding

    captured = value.get("script_resources", [])
    if not isinstance(captured, list) or len(captured) > MAX_DEPENDENCIES:
        raise _unavailable("archive_invalid")
    try:
        script_resources = tuple(ExecutableScriptBinding.from_value(item) for item in captured)
        plan = normalize_workflow(
            value["source"],
            imports=tuple(imports),
            resources=tuple(resources),
            script_resources=script_resources,
        )
    except Exception:
        raise _unavailable("archive_invalid") from None
    if (plan.fingerprint, plan.namespace, plan.name, plan.version) != (
        value["fingerprint"],
        value["namespace"],
        value["name"],
        value["version"],
    ):
        raise _unavailable("archive_invalid")
    return plan


def admission_identity(handle: ExecutionHandle) -> EffectIdentity:
    return EffectIdentity(
        handle.invocation_id, handle.workflow_fingerprint, "/do", (("/do", 1),), ADMISSION_OPERATION
    )


def _expected_receipt(binding: dict[str, Any]) -> EffectReceipt:
    value = {"profile": PROFILE, "binding_digest": _digest(binding)}
    return EffectReceipt(
        effect_request_digest(ADMISSION_OPERATION, value),
        "definition-archive",
        status="completed",
        output=value,
    )


def _role_function_calls(manifest: dict[str, Any]) -> frozenset[str]:
    """Derive only exact named-role references from the qualified descriptor."""
    from .agent_roles import PROFILE as ROLE_PROFILE
    from .agent_roles import RoleDescriptor
    from .config import AgentRoleConfig

    role_profile = manifest.get("policy", {}).get("agent_roles")
    enabled_function_calls: set[str] = set()
    if role_profile is not None:
        if (
            type(role_profile) is not dict
            or set(role_profile)
            != {"profile", "bindings", "current_tool_allowlist", "effective_tool_bindings"}
            or role_profile["profile"] != ROLE_PROFILE
            or type(role_profile["bindings"]) is not list
            or len(role_profile["bindings"]) > 32
            or type(role_profile["current_tool_allowlist"]) is not list
            or len(role_profile["current_tool_allowlist"]) > 256
            or any(type(item) is not str for item in role_profile["current_tool_allowlist"])
            or role_profile["current_tool_allowlist"]
            != sorted(set(role_profile["current_tool_allowlist"]))
            or type(role_profile["effective_tool_bindings"]) is not list
            or len(role_profile["effective_tool_bindings"]) > 64
        ):
            raise _unavailable("archive_invalid")
        for role in role_profile["bindings"]:
            if (
                type(role) is not dict
                or role.get("profile") != ROLE_PROFILE
                or type(role.get("name")) is not str
                or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", role["name"])
                or f"agent-{role['name']}:{role.get('version')}@default" in enabled_function_calls
            ):
                raise _unavailable("archive_invalid")
            descriptor = RoleDescriptor.capture(
                role["name"],
                AgentRoleConfig.model_validate(
                    {name: item for name, item in role.items() if name not in {"name", "profile"}}
                ),
            )
            enabled_function_calls.update(descriptor.references)
    native = manifest.get("policy", {}).get("native_memory")
    if native is not None:
        from .config import MemoryConfig
        from .native_memory_contract import references

        if type(native) is not list or not native:
            raise _unavailable("archive_invalid")
        bindings = MemoryConfig.model_validate({"native": native}).native
        enabled_function_calls.update(references(bindings))
    return frozenset(enabled_function_calls)


def _stored_evidence(
    connection: StorageConnection, invocations: InvocationStore, handle: ExecutionHandle
) -> tuple[WorkflowPlan, dict[str, Any]]:
    row = connection.execute(
        "SELECT fingerprint,payload,digest FROM recovery_invocation_definitions "
        "WHERE invocation_id=?",
        (handle.invocation_id,),
    ).fetchone()
    if row is None:
        raise _unavailable("binding_missing")
    binding = _parsed(row[1], limit=MAX_PROFILE_BYTES)
    if (
        not isinstance(binding, dict)
        or set(binding) - {"execution_authority_digest"}
        != {
            "profile",
            "identity",
            "definition_digest",
            "input_digest",
            "runtime_profile",
            "runtime_profile_digest",
        }
        or binding["profile"] != PROFILE
        or row[0] != handle.workflow_fingerprint
        or binding["identity"] != _identity(handle)
        or row[2] != _digest(binding)
        or "execution_authority_digest" in binding
        and (
            type(binding["execution_authority_digest"]) is not str
            or _SHA256.fullmatch(binding["execution_authority_digest"]) is None
        )
    ):
        raise _unavailable("binding_invalid")
    current = invocations.get(handle.invocation_id)
    if current is None or _identity(current) != binding["identity"]:
        raise _unavailable("binding_invalid")
    if binding["runtime_profile_digest"] != _digest(binding["runtime_profile"]):
        raise _unavailable("binding_invalid")
    archive = connection.execute(
        "SELECT payload,digest FROM recovery_definitions WHERE fingerprint=?",
        (handle.workflow_fingerprint,),
    ).fetchone()
    if archive is None:
        raise _unavailable("archive_missing")
    value = _parsed(archive[0])
    if archive[1] != _digest(value) or archive[1] != binding["definition_digest"]:
        raise _unavailable("archive_invalid")
    plan = _plan(value, enabled_function_calls=_role_function_calls(binding["runtime_profile"]))
    if (plan.fingerprint, plan.name, plan.version) != (
        handle.workflow_fingerprint,
        handle.workflow_name,
        handle.workflow_version,
    ):
        raise _unavailable("binding_invalid")
    row = connection.execute(
        "SELECT fingerprint,payload FROM invocation_inputs WHERE invocation_id=?",
        (handle.invocation_id,),
    ).fetchone()
    if row is None:
        raise _unavailable("input_missing")
    if row[0] != handle.workflow_fingerprint or _digest(_parsed(row[1])) != binding["input_digest"]:
        raise _unavailable("input_changed")
    return plan, binding


def _verify_admission_record(
    connection: StorageConnection,
    handle: ExecutionHandle,
    binding: dict[str, Any],
    receipt: EffectReceipt | None = None,
) -> None:
    identity = admission_identity(handle)
    row = connection.execute(
        "SELECT owner,status,payload FROM workflow_effects WHERE identity=?", (identity.key,)
    ).fetchone()
    index = connection.execute(
        "SELECT invocation_id,fingerprint,payload FROM workflow_effect_invocations "
        "WHERE identity=?",
        (identity.key,),
    ).fetchone()
    expected = _expected_receipt(binding)
    if (
        row is None
        or index is None
        or tuple(index[:2]) != (handle.invocation_id, handle.workflow_fingerprint)
        or EffectIdentity.from_value(_parsed(index[2])) != identity
        or tuple(row[:2]) != (expected.owner, expected.status)
        or EffectReceipt(**_parsed(row[2])) != expected
        or (receipt is not None and receipt != expected)
    ):
        raise _unavailable("binding_invalid")


def verify_definition_admission_receipt(
    connection: StorageConnection,
    invocations: InvocationStore | None,
    identity: EffectIdentity,
    receipt: EffectReceipt,
) -> None:
    """No initialization, SDK, network, manifest adoption or nested journal read."""
    if invocations is None:
        raise _unavailable("binding_invalid")
    handle = invocations.get(identity.invocation_id)
    if handle is None or identity != admission_identity(handle):
        raise _unavailable("binding_invalid")
    _, binding = _stored_evidence(connection, invocations, handle)
    _verify_admission_record(connection, handle, binding, receipt)


class RecoveryDefinitionArchive:
    """One original-definition binding per invocation, never an executable checkpoint."""

    def __init__(
        self,
        journal: EffectJournal,
        invocations: InvocationStore,
        *,
        profile_provider: Callable[[str], dict[str, Any]] | None = None,
        async_profile_provider: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
        trust_verifier: Callable[[WorkflowPlan], None] | None = None,
    ) -> None:
        self.journal, self.invocations = journal, invocations
        self.connection = journal.connection
        self.profile_provider = profile_provider or (
            lambda engine: recovery_profile(engine, backend=self.connection.kind)
        )
        self._async_profile_provider = async_profile_provider
        self._async_provider_binding = self.profile_provider
        self._prepared_profile: ContextVar[
            tuple[asyncio.Task[Any] | None, str, dict[str, Any]] | None
        ] = ContextVar("definition_admission_profile", default=None)
        self.trust_verifier = trust_verifier
        self.admission_participant: (
            Callable[[WorkflowPlan, ExecutionHandle, Any, ExecutionLease | None, bool], None] | None
        ) = None
        self.authority_verifier: Callable[[ExecutionHandle], Any] | None = None
        self.authority_expectation: Callable[[ExecutionHandle], str | None] | None = None
        self._lock = threading.RLock()
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS recovery_definitions "
            "(fingerprint TEXT PRIMARY KEY,payload TEXT NOT NULL,digest TEXT NOT NULL)"
        )
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS recovery_invocation_definitions "
            "(invocation_id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,"
            "payload TEXT NOT NULL,digest TEXT NOT NULL)"
        )
        self.connection.commit()

    def has_binding(self, handle: ExecutionHandle) -> bool:
        """Existence only; a present row is not proof of valid recovery authority."""
        try:
            if (
                self.connection.execute(
                    "SELECT 1 FROM recovery_invocation_definitions WHERE invocation_id=?",
                    (handle.invocation_id,),
                ).fetchone()
                is not None
            ):
                return True
            if (
                self.connection.execute(
                    "SELECT 1 FROM workflow_effect_invocations WHERE invocation_id=? LIMIT 1",
                    (handle.invocation_id,),
                ).fetchone()
                is not None
                or self.connection.execute(
                    "SELECT 1 FROM workflow_effects WHERE identity=?",
                    (admission_identity(handle).key,),
                ).fetchone()
                is not None
            ):
                raise _unavailable("binding_missing")
            return False
        except InvocationStateError:
            raise
        except Exception:
            raise _unavailable("binding_invalid") from None

    async def prepare_profile(self, engine: str) -> dict[str, Any]:
        # Only the explicitly composed provider knows which part is pure filesystem
        # work. Arbitrary supplied/replaced callbacks always remain on this loop.
        if (
            self._async_profile_provider is not None
            and self.profile_provider is self._async_provider_binding
        ):
            provider = self._async_profile_provider
            binding = self.profile_provider
            manifest = await provider(engine)
            if self._async_profile_provider is not provider or self.profile_provider is not binding:
                raise _unavailable("runtime_profile_changed")
            return manifest
        return self.profile_provider(engine)

    @asynccontextmanager
    async def owned_profile(self, engine: str) -> AsyncIterator[None]:
        """Reuse fresh physical evidence only within one synchronous DB phase."""
        manifest = await self.prepare_profile(engine)
        _encoded(manifest, limit=MAX_PROFILE_BYTES)
        token = self._prepared_profile.set(
            (asyncio.current_task(), engine, copy.deepcopy(manifest))
        )
        try:
            yield
        finally:
            self._prepared_profile.reset(token)

    def _current_profile(self, engine: str) -> dict[str, Any]:
        prepared = self._prepared_profile.get()
        if prepared is not None and prepared[1] == engine and prepared[0] is asyncio.current_task():
            return copy.deepcopy(prepared[2])
        return self.profile_provider(engine)

    async def verify_owned(self, handle: ExecutionHandle, plan: WorkflowPlan) -> None:
        async with self.owned_profile(handle.engine):
            self.verify(handle, plan)

    async def verify_admission_owned(self, handle: ExecutionHandle) -> WorkflowPlan:
        async with self.owned_profile(handle.engine):
            return self.verify_admission(handle)

    async def resolve_owned(self, handle: ExecutionHandle) -> WorkflowPlan:
        async with self.owned_profile(handle.engine):
            return self.resolve(handle)

    def _read(
        self, handle: ExecutionHandle, *, allow_unqualified: bool = False
    ) -> tuple[WorkflowPlan, dict[str, Any]]:
        row = self.connection.execute(
            "SELECT payload FROM recovery_invocation_definitions WHERE invocation_id=?",
            (handle.invocation_id,),
        ).fetchone()
        if row is None:
            raise _unavailable("binding_missing")
        binding = _parsed(row[0], limit=MAX_PROFILE_BYTES)
        manifest = self._current_profile(handle.engine)
        _encoded(manifest, limit=MAX_PROFILE_BYTES)
        if (
            binding["runtime_profile_digest"] != _digest(binding["runtime_profile"])
            or binding["runtime_profile_digest"] != _digest(manifest)
            or binding["runtime_profile"] != manifest
        ):
            raise _unavailable("runtime_profile_changed")
        if manifest.get("continuation_supported") is False and not allow_unqualified:
            raise _unavailable("deployment_profile_unqualified")
        plan, binding = _stored_evidence(self.connection, self.invocations, handle)
        _verify_admission_record(self.connection, handle, binding)
        if self.trust_verifier is not None:
            try:
                self.trust_verifier(plan)
            except OwaError:
                raise _unavailable("deployment_trust_changed") from None
        if self.authority_verifier is not None:
            self.authority_verifier(handle)
        return plan, binding

    def admit(
        self,
        plan: WorkflowPlan,
        handle: ExecutionHandle,
        input_data: Any,
        *,
        execution_lease: ExecutionLease | None,
    ) -> None:
        try:
            self._admit(plan, handle, input_data, execution_lease=execution_lease)
        except OwaError:
            raise
        except Exception:
            raise _unavailable("archive_invalid") from None

    def _admit(
        self,
        plan: WorkflowPlan,
        handle: ExecutionHandle,
        input_data: Any,
        *,
        execution_lease: ExecutionLease | None,
    ) -> None:
        if execution_lease is None:
            raise InvocationStateConflict("definition admission requires execution ownership")
        self.invocations.verify_fingerprint(handle, plan.fingerprint)
        definition = _definition(plan)
        manifest = self._current_profile(handle.engine)
        _encoded(manifest, limit=MAX_PROFILE_BYTES)
        restored = _plan(definition, enabled_function_calls=_role_function_calls(manifest))
        if (plan.name, plan.version) != (handle.workflow_name, handle.workflow_version):
            raise _unavailable("binding_invalid")
        if (
            plan.execution_source != restored.execution_source
            or plan.tasks != restored.tasks
            or plan.scopes != restored.scopes
        ):
            raise _unavailable("archive_invalid")
        if self.trust_verifier is not None:
            self.trust_verifier(restored)
        definition_digest, input_digest = _digest(definition), _digest(input_data)
        binding = {
            "profile": PROFILE,
            "identity": _identity(handle),
            "definition_digest": definition_digest,
            "input_digest": input_digest,
            "runtime_profile": manifest,
            "runtime_profile_digest": _digest(manifest),
        }
        if self.authority_expectation is not None:
            expected_authority = self.authority_expectation(handle)
            if expected_authority is not None:
                binding["execution_authority_digest"] = expected_authority
        with (
            self._lock,
            owned_write_transaction(
                self.connection,
                execution_lease,
                invocation_id=handle.invocation_id,
                workflow_fingerprint=handle.workflow_fingerprint,
            ),
        ):
            lock = " FOR UPDATE" if self.connection.is_postgresql else ""
            row = self.connection.execute(
                "SELECT payload,status FROM invocations WHERE invocation_id=?" + lock,
                (handle.invocation_id,),
            ).fetchone()
            if row is None or row[0] != handle._persisted_payload or row[1] != "running":
                raise InvocationStateConflict("definition admission invocation has changed")
            existing = self.connection.execute(
                "SELECT 1 FROM recovery_invocation_definitions WHERE invocation_id=?",
                (handle.invocation_id,),
            ).fetchone()
            if existing is not None:
                _, original = self._read(handle, allow_unqualified=True)
                if original != binding:
                    raise _unavailable("binding_invalid")
                if self.admission_participant is not None:
                    self.admission_participant(plan, handle, input_data, execution_lease, False)
                return
            if self.has_binding(handle):
                raise _unavailable("binding_invalid")
            old = self.connection.execute(
                "SELECT payload,digest FROM recovery_definitions WHERE fingerprint=?",
                (plan.fingerprint,),
            ).fetchone()
            encoded = _encoded(definition)
            if old is not None and (old[0] != encoded or old[1] != definition_digest):
                raise _unavailable("archive_invalid")
            self.connection.execute(
                "INSERT INTO recovery_definitions(fingerprint,payload,digest) VALUES (?,?,?) "
                "ON CONFLICT(fingerprint) DO NOTHING",
                (plan.fingerprint, encoded, definition_digest),
            )
            self.connection.execute(
                "INSERT INTO invocation_inputs(invocation_id,fingerprint,payload) VALUES (?,?,?) "
                "ON CONFLICT(invocation_id) DO NOTHING",
                (handle.invocation_id, plan.fingerprint, _encoded(input_data)),
            )
            self.connection.execute(
                "INSERT INTO recovery_invocation_definitions"
                "(invocation_id,fingerprint,payload,digest) "
                "VALUES (?,?,?,?)",
                (
                    handle.invocation_id,
                    plan.fingerprint,
                    _encoded(binding, limit=MAX_PROFILE_BYTES),
                    _digest(binding),
                ),
            )
            insert_completed_receipt(
                self.connection, admission_identity(handle), _expected_receipt(binding)
            )
            if self.admission_participant is not None:
                self.admission_participant(plan, handle, input_data, execution_lease, True)
            self._read(handle, allow_unqualified=True)

    def resolve(self, handle: ExecutionHandle) -> WorkflowPlan:
        try:
            return self._read(handle)[0]
        except InvocationStateError:
            raise
        except Exception:
            raise _unavailable("archive_invalid") from None

    def verify_admission(self, handle: ExecutionHandle) -> WorkflowPlan:
        """Read initial admission inside an already verified live-owner execution.

        This is not a resume qualification check: public/queued continuation
        must use resolve/verify and reject an unqualified deployment profile.
        """
        try:
            return self._read(handle, allow_unqualified=True)[0]
        except InvocationStateError:
            raise
        except Exception:
            raise _unavailable("archive_invalid") from None

    def verify(self, handle: ExecutionHandle, plan: WorkflowPlan) -> None:
        original = self.resolve(handle)
        if plan.fingerprint != original.fingerprint or _definition(plan) != _definition(original):
            raise WorkflowDefinitionChanged(
                "workflow definition changed after original admission",
                details={
                    "reason": "definition_changed",
                    "stored": original.fingerprint,
                    "current": plan.fingerprint,
                },
            )

    def diagnostic_safe_snapshot(self, handle: ExecutionHandle) -> dict[str, Any]:
        result: dict[str, Any] = {
            "profile": PROFILE,
            "engine": handle.engine,
            "workflow_name": handle.workflow_name,
            "workflow_version": handle.workflow_version,
            "workflow_fingerprint": handle.workflow_fingerprint,
        }
        try:
            _, binding = self._read(handle)
            result["provenance"] = {
                "definition_digest": binding["definition_digest"],
                "runtime_profile_digest": binding["runtime_profile_digest"],
            }
            result.update(status="compatible", reason=None)
        except InvocationStateError as error:
            result.update(
                status="unavailable",
                reason=cast(str, error.details.get("reason", "archive_invalid")),
            )
        except Exception:
            result.update(status="unavailable", reason="archive_invalid")
        return result
