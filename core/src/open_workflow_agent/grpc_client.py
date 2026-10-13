"""Private deployment-bound unary gRPC; no runtime compilation or discovery."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib
import ipaddress
import json
import math
import re
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse
from uuid import uuid4

from google.protobuf import descriptor_pb2, descriptor_pool, json_format, message_factory

from .errors import OwaError, ToolError
from .expressions import validate_expression_data

if TYPE_CHECKING:
    from .protocols import AuthenticationProvider, HttpClient

GRPC_SDK_VERSION = "1.84.0"
PROTOBUF_VERSION = "7.36.1"
APPLICATION_STATUS_PROFILE = "owa/grpc-application-status/v1"
APPLICATION_STATUS_HEADER = "x-owa-grpc-application-status"
MAX_DESCRIPTOR_BYTES = 1_048_576
MAX_MESSAGE_BYTES = 4_000_000
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
_KEY = re.compile(r"[A-Za-z0-9._:/-]{1,256}")
_KNOWN_STATUSES = frozenset(
    {
        "INVALID_ARGUMENT",
        "NOT_FOUND",
        "ALREADY_EXISTS",
        "PERMISSION_DENIED",
        "FAILED_PRECONDITION",
        "ABORTED",
        "OUT_OF_RANGE",
        "UNIMPLEMENTED",
        "UNAUTHENTICATED",
    }
)


class _GrpcOutcomeUncertain(OwaError):
    """Business dispatch happened; no authoritative result permits redelivery."""

    code = "grpc_outcome_uncertain"
    status_code = 409


def _failure(reason: str) -> ToolError:
    return ToolError("gRPC call failed", details={"reason": reason})


def verify_sdk() -> None:
    for package, required in (("grpcio", GRPC_SDK_VERSION), ("protobuf", PROTOBUF_VERSION)):
        try:
            installed = version(package)
        except PackageNotFoundError:
            installed = None
        if installed != required:
            raise _failure("unsupported_sdk")


@dataclass(frozen=True, slots=True)
class GrpcBinding:
    """Trusted deployment declaration, never taken from workflow/tool arguments."""

    id: str
    proto_uri: str
    proto_filename: str
    proto_source: bytes
    proto_sha256: str
    descriptor_set: bytes
    descriptor_sha256: str
    endpoint: str
    service: str
    method: str
    allow_insecure_loopback: bool = False
    application_status_profile: bool = False
    max_request_bytes: int = MAX_MESSAGE_BYTES
    max_response_bytes: int = MAX_MESSAGE_BYTES


@dataclass(frozen=True, slots=True)
class PreparedGrpcCall:
    binding: GrpcBinding
    request: bytes
    pool: Any
    output_descriptor: Any


def _endpoint(binding: GrpcBinding) -> tuple[str, int, bool]:
    try:
        parsed = urlparse(binding.endpoint)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if (
            not host
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or parsed.scheme not in {"https", "http"}
            or not 1 <= port <= 65535
        ):
            raise ValueError("endpoint")
        secure = parsed.scheme == "https"
        if not secure and not (
            binding.allow_insecure_loopback and ipaddress.ip_address(host).is_loopback
        ):
            raise ValueError("TLS")
        return host.lower(), port, secure
    except (ValueError, TypeError):
        raise _failure("invalid_binding") from None


def _finite_message(message: Any) -> None:
    for field, value in message.ListFields():
        values = value if field.is_repeated else [value]
        if field.message_type is not None and field.message_type.GetOptions().map_entry:
            entry = field.message_type.fields_by_name["value"]
            for item in value.values():
                if entry.message_type is not None:
                    _finite_message(item)
                elif entry.cpp_type in {5, 6} and not math.isfinite(item):
                    raise ValueError("nonfinite")
                elif entry.enum_type is not None and item not in entry.enum_type.values_by_number:
                    raise ValueError("enum")
        elif field.message_type is not None:
            for item in values:
                _finite_message(item)
        elif field.cpp_type in {5, 6} and any(not math.isfinite(item) for item in values):
            raise ValueError("nonfinite")
        elif field.enum_type is not None and any(
            item not in field.enum_type.values_by_number for item in values
        ):
            raise ValueError("enum")


def _bytes_value(value: Any) -> None:
    if not isinstance(value, str) or not value.isascii():
        raise ValueError("bytes encoding")
    padding = "=" * (-len(value) % 4)
    base64.b64decode(value.translate(str.maketrans("-_", "+/")) + padding, validate=True)


def _guard_scalar(value: Any, field: Any) -> None:
    if field.type == 12:
        _bytes_value(value)
    if field.enum_type is not None and (
        type(value) not in {str, int, float}
        or type(value) is float
        and (not math.isfinite(value) or not value.is_integer())
    ):
        # The SDK calls int(value); bools and fractional floats can otherwise
        # silently become a different, valid enum member before dispatch.
        raise ValueError("enum")


def _guard_aliases(value: Any, descriptor: Any) -> None:
    if descriptor.full_name == "google.protobuf.BytesValue" and value is not None:
        _bytes_value(value)
    if not isinstance(value, dict) or descriptor.full_name.startswith("google.protobuf."):
        return
    seen: set[str] = set()
    aliases = {
        alias: field for field in descriptor.fields for alias in (field.name, field.json_name)
    }
    for name, item in value.items():
        field = aliases.get(name)
        if field is None or field.name in seen:
            raise ValueError("unknown or repeated field")
        seen.add(field.name)
        if item is not None and field.message_type is None:
            for scalar in item if field.is_repeated and isinstance(item, list) else [item]:
                _guard_scalar(scalar, field)
        if field.message_type is not None and item is not None:
            if field.message_type.GetOptions().map_entry:
                entry_field = field.message_type.fields_by_name["value"]
                child = entry_field.message_type
                if isinstance(item, dict):
                    for entry in item.values():
                        if child is not None:
                            _guard_aliases(entry, child)
                        else:
                            _guard_scalar(entry, entry_field)
            elif field.is_repeated:
                if isinstance(item, list):
                    for entry in item:
                        _guard_aliases(entry, field.message_type)
            else:
                _guard_aliases(item, field.message_type)


def _guard_descriptor_closure(*roots: Any) -> None:
    """Selected messages only; packed Any needs a separately qualified codec."""
    pending = list(roots)
    visited: set[str] = set()
    while pending:
        message = pending.pop()
        if message.full_name in visited:
            continue
        visited.add(message.full_name)
        if message.full_name == "google.protobuf.Any":
            raise ValueError("unsupported packed codec")
        pending.extend(
            field.message_type for field in message.fields if field.message_type is not None
        )


def _descriptor(binding: GrpcBinding) -> tuple[Any, Any]:
    try:
        if (
            type(binding.allow_insecure_loopback) is not bool
            or type(binding.application_status_profile) is not bool
            or type(binding.max_request_bytes) is not int
            or type(binding.max_response_bytes) is not int
            or not _KEY.fullmatch(binding.id)
            or not binding.proto_uri
            or len(binding.proto_uri) > 2048
            or not _NAME.fullmatch(binding.service)
            or not _NAME.fullmatch(binding.method)
            or "." in binding.method
            or not binding.proto_filename
            or ".." in binding.proto_filename.split("/")
            or binding.proto_filename.startswith(("/", "\\"))
            or not 0 < len(binding.proto_source) <= MAX_DESCRIPTOR_BYTES
            or not 0 < len(binding.descriptor_set) <= MAX_DESCRIPTOR_BYTES
            or not 0 < binding.max_request_bytes <= MAX_MESSAGE_BYTES
            or not 0 < binding.max_response_bytes <= MAX_MESSAGE_BYTES
        ):
            raise ValueError("binding")
        binding.proto_source.decode("utf-8")
        for value, digest in (
            (binding.proto_source, binding.proto_sha256),
            (binding.descriptor_set, binding.descriptor_sha256),
        ):
            if (
                not re.fullmatch(r"[a-f0-9]{64}", digest)
                or hashlib.sha256(value).hexdigest() != digest
            ):
                raise ValueError("digest")
        _endpoint(binding)
        files = descriptor_pb2.FileDescriptorSet.FromString(binding.descriptor_set)
        if not 1 <= len(files.file) <= 64:
            raise ValueError("files")
        by_name = {file.name: file for file in files.file}
        if len(by_name) != len(files.file) or binding.proto_filename not in by_name:
            raise ValueError("names")
        pool = descriptor_pool.DescriptorPool()
        pending = dict(by_name)
        installed: set[str] = set()
        while pending:
            ready = [file for file in pending.values() if set(file.dependency) <= installed]
            if not ready:
                raise ValueError("missing or cyclic imports")
            for file in ready:
                pool.Add(file)
                installed.add(file.name)
                del pending[file.name]
        service = pool.FindServiceByName(binding.service)
        if service.file.name != binding.proto_filename:
            raise ValueError("service origin")
        method = service.methods_by_name[binding.method]
        if method.client_streaming or method.server_streaming:
            raise ValueError("streaming")
        if any(
            item.full_name.startswith("google.protobuf.")
            for item in (method.input_type, method.output_type)
        ):
            raise ValueError("unsupported root codec")
        _guard_descriptor_closure(method.input_type, method.output_type)
        return pool, method
    except Exception as error:
        if isinstance(error, ToolError):
            raise
        raise _failure("invalid_descriptor") from None


def _proto_uri(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and set(value) == {"endpoint"}:
        endpoint = value["endpoint"]
        if isinstance(endpoint, str):
            return endpoint
        if (
            isinstance(endpoint, dict)
            and set(endpoint) == {"uri"}
            and isinstance(endpoint["uri"], str)
        ):
            return endpoint["uri"]
    raise _failure("invalid_parameters")


class GrpcRegistry:
    def __init__(self, bindings: Iterable[GrpcBinding] = ()) -> None:
        selected: dict[tuple[str, str, str, str, int], tuple[GrpcBinding, Any, Any]] = {}
        identifiers: set[str] = set()
        for index, binding in enumerate(bindings):
            if index >= 128:
                raise _failure("registry_limit")
            pool, method = _descriptor(binding)
            host, port, _ = _endpoint(binding)
            key = binding.proto_uri, binding.service, binding.method, host, port
            if key in selected or binding.id in identifiers:
                raise _failure("duplicate_binding")
            identifiers.add(binding.id)
            selected[key] = binding, pool, method
        self._bindings = MappingProxyType(selected)

    def snapshot(self) -> list[dict[str, Any]]:
        """Pure nonsecret deployment pins for original-definition recovery."""
        return [
            {
                "profile": "owa/grpc-binding/v1",
                "id": binding.id,
                "proto_uri": binding.proto_uri,
                "proto_filename": binding.proto_filename,
                "proto_sha256": binding.proto_sha256,
                "descriptor_sha256": binding.descriptor_sha256,
                "endpoint": binding.endpoint,
                "service": binding.service,
                "method": binding.method,
                "allow_insecure_loopback": binding.allow_insecure_loopback,
                "application_status_profile": binding.application_status_profile,
                "max_request_bytes": binding.max_request_bytes,
                "max_response_bytes": binding.max_response_bytes,
            }
            for binding, _pool, _method in sorted(
                self._bindings.values(), key=lambda value: value[0].id
            )
        ]

    def prepare(self, payload: Mapping[str, Any], *, _route_only: bool = False) -> PreparedGrpcCall:
        try:
            if set(payload) - {"proto", "service", "method", "arguments"}:
                raise ValueError("fields")
            service = payload.get("service")
            if not isinstance(service, dict) or set(service) - {
                "name",
                "host",
                "port",
                "authentication",
            }:
                raise ValueError("service")
            host, name, method = service.get("host"), service.get("name"), payload.get("method")
            port = service.get("port", 443)
            if (
                not isinstance(host, str)
                or not isinstance(name, str)
                or not isinstance(method, str)
                or type(port) is not int
                or not 1 <= port <= 65535
            ):
                raise ValueError("identity")
            # The selected authentication reference is handled by RuntimeServices;
            # it can never supply arbitrary metadata or a channel option here.
            binding, pool, descriptor = self._bindings[
                (_proto_uri(payload.get("proto")), name, method, host.lower(), port)
            ]
            if _route_only:
                return PreparedGrpcCall(binding, b"", pool, descriptor.output_type)
            arguments = payload.get("arguments", {})
            if not isinstance(arguments, dict):
                raise ValueError("arguments")
            validate_expression_data(arguments)
            if len(json.dumps(arguments, allow_nan=False).encode()) > binding.max_request_bytes:
                raise ValueError("request size")
            _guard_aliases(arguments, descriptor.input_type)
            message = message_factory.GetMessageClass(descriptor.input_type)()
            json_format.ParseDict(
                arguments,
                message,
                descriptor_pool=pool,
                ignore_unknown_fields=False,
                max_recursion_depth=64,
            )
            _finite_message(message)
            if not message.IsInitialized():
                raise ValueError("required field")
            data = message.SerializeToString(deterministic=True)
            if len(data) > binding.max_request_bytes:
                raise ValueError("request size")
            return PreparedGrpcCall(binding, data, pool, descriptor.output_type)
        except (KeyError, ValueError, TypeError, RecursionError, json_format.ParseError):
            raise _failure("invalid_parameters") from None


def _read_trust_file(path: str) -> bytes:
    with Path(path).open("rb") as source:
        content = source.read(2_097_153)
    if not content or len(content) > 2_097_152:
        raise _failure("invalid_tls_binding")
    return content


def _status_proven(error: Any, operation_key: str, binding: GrpcBinding) -> str | None:
    if not binding.application_status_profile:
        return None
    try:
        code = error.code().name
        values = [
            value
            for name, value in error.trailing_metadata() or ()
            if name == APPLICATION_STATUS_HEADER
        ]
        if (
            code not in _KNOWN_STATUSES
            or len(values) != 1
            or not isinstance(values[0], str)
            or len(values[0]) > 512
        ):
            return None

        def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for name, value in items:
                if name in result:
                    raise ValueError("duplicate receipt field")
                result[name] = value
            return result

        receipt = json.loads(values[0], object_pairs_hook=pairs)
        if receipt != {
            "profile": APPLICATION_STATUS_PROFILE,
            "operation_key": operation_key,
            "status": code,
        }:
            return None
        return str(code)
    except (TypeError, ValueError, AttributeError):
        return None


async def call_grpc(
    http: HttpClient,
    payload: Mapping[str, Any],
    *,
    registry: GrpcRegistry,
    authentication: AuthenticationProvider | None = None,
    credential_endpoints: frozenset[str] = frozenset(),
    remaining_timeout: float | None = None,
    operation_key: str | None = None,
) -> dict[str, Any]:
    """One unary dispatch, bounded preflight/connect/auth/DNS/body and cleanup."""
    verify_sdk()
    prepared = registry.prepare(payload)
    selected = prepared.binding
    host, port, secure = _endpoint(selected)
    key = operation_key or str(uuid4())
    timeout = min(
        http.timeout, remaining_timeout if remaining_timeout is not None else http.timeout
    )
    if not _KEY.fullmatch(key) or not math.isfinite(timeout) or timeout <= 0:
        raise _failure("invalid_deadline_or_operation")
    if secure and http.verify_tls is False:
        raise _failure("tls_verification_required")
    if http.client_cert is not None and authentication is None:
        raise _failure("unbound_client_identity")
    if (
        isinstance(payload.get("service"), dict)
        and "authentication" in payload["service"]
        and authentication is None
    ):
        raise _failure("unresolved_authentication")
    if type(http.max_response_bytes) is not int or http.max_response_bytes <= 0:
        raise _failure("invalid_response_limit")
    if authentication is not None and (not secure or selected.endpoint not in credential_endpoints):
        raise _failure("unbound_credentials")
    started = time.monotonic()
    dispatched = False
    channel: Any = None
    call: Any = None
    grpc: Any = None
    try:
        async with asyncio.timeout(timeout):
            http._check_endpoint(selected.endpoint)
            metadata: dict[str, str] = {}
            client_cert = None
            inherited = authentication is None or authentication is http.authentication
            ca_bundle = http.verify_tls if inherited and isinstance(http.verify_tls, str) else None
            if authentication is not None:
                prepare = getattr(authentication, "prepare", None)
                if prepare is None:
                    metadata = dict(authentication.headers(selected.endpoint))
                else:
                    auth = await prepare(
                        selected.endpoint, remaining_timeout=timeout - (time.monotonic() - started)
                    )
                    metadata = dict(auth.headers)
                    client_cert = auth.client_cert
                    ca_bundle = auth.ca_bundle or ca_bundle
            lowered: dict[str, str] = {}
            for name, value in metadata.items():
                normalized = name.lower()
                if (
                    not re.fullmatch(r"[a-z0-9_.-]{1,64}", normalized)
                    or normalized.startswith("grpc-")
                    or normalized.endswith("-bin")
                    or normalized
                    in {"x-owa-operation-id", "idempotency-key", APPLICATION_STATUS_HEADER}
                    or normalized in lowered
                    or not isinstance(value, str)
                    or not value.isascii()
                    or any(ord(char) < 32 or ord(char) == 127 for char in value)
                ):
                    raise _failure("invalid_metadata")
                lowered[normalized] = value
            if (
                len(lowered) > 32
                or sum(len(name) + len(value) for name, value in lowered.items()) > 8192
            ):
                raise _failure("metadata_limit")
            lowered.update({"x-owa-operation-id": key, "idempotency-key": key})
            resolver = http.destination_resolver
            addresses: tuple[str, ...]
            if resolver is None and not secure:
                # This is an explicitly opted-in literal loopback binding,
                # already verified by _endpoint and common host policy. It
                # never resolves an untrusted hostname to a private address.
                addresses = (host,)
            elif resolver is None:
                from .protocols import resolve_public_addresses_async

                addresses = await resolve_public_addresses_async(
                    selected.endpoint, timeout=timeout - (time.monotonic() - started)
                )
            else:
                addresses = await resolver(selected.endpoint)
            if not addresses or len(addresses) > 8:
                raise _failure("invalid_destination")
            parsed_addresses = [ipaddress.ip_address(address) for address in addresses]
            if not secure and any(not address.is_loopback for address in parsed_addresses):
                raise _failure("invalid_destination")
            address = parsed_addresses[0]
            target = (
                f"ipv4:{address}:{port}" if address.version == 4 else f"ipv6:[{address}]:{port}"
            )
            options = (
                ("grpc.enable_retries", 0),
                ("grpc.enable_http_proxy", 0),
                ("grpc.service_config_disable_resolution", 1),
                ("grpc.max_send_message_length", selected.max_request_bytes),
                (
                    "grpc.max_receive_message_length",
                    min(selected.max_response_bytes, http.max_response_bytes),
                ),
                ("grpc.max_metadata_size", 16_384),
                ("grpc.default_authority", f"{host}:{port}"),
                ("grpc.ssl_target_name_override", host),
            )
            grpc = importlib.import_module("grpc")
            if secure:
                # Explicit certifi bytes prevent ambient gRPC root-file overrides.
                roots = _read_trust_file(ca_bundle or importlib.import_module("certifi").where())
                certificate = _read_trust_file(client_cert[0]) if client_cert else None
                private_key = _read_trust_file(client_cert[1]) if client_cert else None
                credentials = grpc.ssl_channel_credentials(roots, private_key, certificate)
                channel = grpc.aio.secure_channel(target, credentials, options=options)
            else:
                channel = grpc.aio.insecure_channel(target, options=options)
            await channel.channel_ready()
            unary = channel.unary_unary(f"/{selected.service}/{selected.method}")
            dispatched = True
            call = unary(
                prepared.request,
                timeout=timeout - (time.monotonic() - started),
                metadata=tuple(lowered.items()),
                wait_for_ready=False,
            )
            content = await call
            if len(content) > min(selected.max_response_bytes, http.max_response_bytes):
                raise ValueError("response limit")
            message = message_factory.GetMessageClass(prepared.output_descriptor)()
            message.ParseFromString(content)
            clean = message_factory.GetMessageClass(prepared.output_descriptor)()
            clean.CopyFrom(message)
            clean.DiscardUnknownFields()
            if clean.SerializeToString(deterministic=True) != message.SerializeToString(
                deterministic=True
            ):
                raise ValueError("unknown response fields")
            _finite_message(message)
            if not message.IsInitialized():
                raise ValueError("required response fields")
            result = json_format.MessageToDict(message, descriptor_pool=prepared.pool)
            validate_expression_data(result)
            if not isinstance(result, dict) or len(
                json.dumps(result, allow_nan=False).encode()
            ) > min(selected.max_response_bytes, http.max_response_bytes):
                raise ValueError("response JSON limit")
            return dict(result)
    except BaseException as error:
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        if dispatched:
            if call is not None:
                call.cancel()
            if isinstance(error, asyncio.CancelledError):
                # Host drain/ownership loss is execution interruption. The
                # workflow deadline owner separately records unknown outcomes.
                raise
            known = (
                _status_proven(error, key, selected)
                if grpc is not None and isinstance(error, grpc.aio.AioRpcError)
                else None
            )
            if known is not None:
                raise ToolError(
                    "gRPC application rejected the request",
                    details={"reason": "application_status", "status": known},
                ) from None
            raise _GrpcOutcomeUncertain(
                "gRPC outcome remains unresolved", details={"reason": "unproven_result"}
            ) from None
        if isinstance(error, (ToolError, asyncio.CancelledError)):
            raise
        raise _failure("pre_dispatch_failure") from None
    finally:
        if channel is not None:
            try:
                async with asyncio.timeout(1):
                    await channel.close()
            except (Exception, asyncio.CancelledError):
                # Cleanup cannot invent a new business result or replace a known
                # response; the transport close itself is finite and cancels calls.
                pass
