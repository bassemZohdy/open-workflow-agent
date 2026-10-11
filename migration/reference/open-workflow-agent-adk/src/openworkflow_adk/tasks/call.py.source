"""Builders for named functions and gRPC calls."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import inspect
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from openworkflow_adk.adk_compat import FunctionNode
from openworkflow_adk.expressions import bind
from openworkflow_adk.models import Task
from openworkflow_adk.security.security import guarded_async_client, validate_egress

from .simple import _dynamic

if TYPE_CHECKING:
    from openworkflow_adk.translator import NodeBuilderRegistry


def _function_builder(
    name: str,
    task: Task,
    functions: dict[str, Callable[..., Any]],
    function_tasks: dict[str, Task] | None = None,
    registry: NodeBuilderRegistry | None = None,
) -> FunctionNode:
    function = functions.get(task.call or "")
    reusable = (function_tasks or {}).get(task.call or "")
    if function is None and (reusable is None or registry is None):
        raise KeyError(f"function {task.call!r} is not registered")

    async def call_function(ctx: Any) -> Any:
        arguments = bind(task.with_ or {}, ctx.state.to_dict())
        cache = registry.memoization if registry is not None else None

        async def compute() -> Any:
            if function is None and reusable is not None and registry is not None:
                node = _dynamic(registry.build(f"{name}__function", reusable))
                return await ctx.run_node(node, node_input=arguments)
            result = function(**arguments)
            return await result if inspect.isawaitable(result) else result

        if cache is not None:
            return await cache.get_or_compute(cache.key(task.call or name, arguments), compute)
        return await compute()

    return _dynamic(FunctionNode(func=call_function, name=name))


def _resource_base_dir() -> Path:
    """Return the directory local resource reads are confined to."""
    return Path(os.environ.get("WORKFLOW_RESOURCE_BASE_DIR", str(Path.cwd()))).resolve()


def _resolve_resource_path(source: str) -> Path:
    """Resolve a local resource path and ensure it stays inside the base dir."""
    base_dir = _resource_base_dir()
    path = Path(source)
    if not path.is_absolute():
        path = base_dir / path
    path = path.resolve()
    try:
        path.relative_to(base_dir)
    except ValueError as exc:
        raise ValueError(
            f"resource path {source!r} escapes the configured resource base directory"
        ) from exc
    return path


async def _read_resource(resource: Any) -> dict[str, Any]:
    source = resource
    if isinstance(source, dict):
        source = source.get("uri") or source.get("endpoint")
        if isinstance(source, dict):
            source = source.get("uri")
    if not isinstance(source, str):
        raise ValueError("OpenAPI document requires a URI or local path")
    if source.startswith(("http://", "https://")):
        validate_egress(source)
        async with guarded_async_client(follow_redirects=False) as client:
            response = await client.get(source)
            response.raise_for_status()
            return response.json()
    path = _resolve_resource_path(source)
    text = path.read_text()
    return json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)


async def _read_resource_bytes(resource: Any) -> bytes:
    source = resource
    if isinstance(source, dict):
        source = source.get("uri") or source.get("endpoint")
        if isinstance(source, dict):
            source = source.get("uri")
    if not isinstance(source, str):
        raise ValueError("protocol resource requires an endpoint URI or local path")
    if source.startswith(("http://", "https://")):
        validate_egress(source)
        async with guarded_async_client(follow_redirects=False) as client:
            response = await client.get(source)
            response.raise_for_status()
            content = response.content
    else:
        content = _resolve_resource_path(source).read_bytes()
    if isinstance(resource, dict) and resource.get("sha256"):
        digest = hashlib.sha256(content).hexdigest()
        if digest != resource["sha256"]:
            raise ValueError(
                f"protocol resource digest mismatch: expected {resource['sha256']}, got {digest}"
            )
    return content


def _grpc_message_class(descriptor: Any) -> Any:
    from google.protobuf.message_factory import GetMessageClass

    return GetMessageClass(descriptor)


def _compile_grpc_proto(proto: bytes, temporary_directory: str) -> tuple[Any, Any]:
    """Compile a gRPC proto resource and import the generated modules.

    The proto bytes are treated as trusted input: they are written to disk and
    executed indirectly via protoc-generated Python modules. Callers must
    validate the source of proto bytes before invoking a gRPC call.
    """
    # Unique module name derived from the proto bytes avoids collisions under
    # concurrent gRPC calls that share the same temporary directory namespace.
    digest = hashlib.sha256(proto).hexdigest()
    basename = f"owf_grpc_{digest[:16]}"
    path = Path(temporary_directory) / f"{basename}.proto"
    path.write_bytes(proto)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "grpc_tools.protoc",
            f"-I{temporary_directory}",
            f"--python_out={temporary_directory}",
            f"--grpc_python_out={temporary_directory}",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(
            f"could not compile gRPC proto resource "
            f"(protoc exit {result.returncode}): {result.stderr.strip()}"
        )
    importlib.invalidate_caches()
    return (
        importlib.import_module(f"{basename}_pb2"),
        importlib.import_module(f"{basename}_pb2_grpc"),
    )


def _grpc_builder(name: str, task: Task) -> FunctionNode:
    async def call_grpc(ctx: Any) -> Any:
        import grpc
        from google.protobuf.json_format import MessageToDict, ParseDict

        arguments = bind(task.with_ or {}, ctx.state.to_dict())
        service = arguments.get("service") or {}
        service_name = service.get("name")
        method_name = arguments.get("method")
        if not service_name or not method_name:
            raise ValueError("gRPC call requires service.name and method")
        proto = await _read_resource_bytes(arguments.get("proto"))
        with tempfile.TemporaryDirectory(prefix="owf-grpc-") as directory:
            sys.path.insert(0, directory)
            try:
                proto_module, service_module = _compile_grpc_proto(proto, directory)
                descriptor = proto_module.DESCRIPTOR.services_by_name.get(service_name)
                if descriptor is None:
                    raise KeyError(f"gRPC service {service_name!r} was not found")
                method = descriptor.methods_by_name.get(method_name)
                if method is None:
                    raise KeyError(f"gRPC method {method_name!r} was not found")
                request_class = _grpc_message_class(method.input_type)
                request = ParseDict(arguments.get("arguments") or {}, request_class())
                host = service.get("host")
                port = service.get("port", 443)
                if not host:
                    raise ValueError("gRPC call requires service.host")
                validate_egress(f"https://{host}:{port}")
                target = f"{host}:{port}"
                if service.get("tls", False):
                    credentials = grpc.ssl_channel_credentials()
                    channel = grpc.aio.secure_channel(target, credentials)
                else:
                    channel = grpc.aio.insecure_channel(target)
                try:
                    stub_class = getattr(service_module, f"{service_name}Stub")
                    response = await asyncio.wait_for(
                        getattr(stub_class(channel), method_name)(request),
                        float(service.get("timeout", 30)),
                    )
                finally:
                    await channel.close()
                return MessageToDict(response, preserving_proto_field_name=True)
            finally:
                try:
                    sys.path.remove(directory)
                except ValueError:
                    pass

    return _dynamic(FunctionNode(func=call_grpc, name=name))
