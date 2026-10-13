"""Actual pinned unary peer and strict deployment profile controls; no protoc."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib
import json
import os
from contextlib import asynccontextmanager
from dataclasses import replace
from importlib.metadata import PackageNotFoundError
from pathlib import PurePosixPath
from urllib.parse import urlparse

import pytest
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory, struct_pb2
from open_workflow_agent import grpc_client
from open_workflow_agent.config import GrpcBindingConfig, ProtocolConfig, RuntimeConfig
from open_workflow_agent.errors import InvocationStateError, ToolError, UnsupportedWorkflowFeature
from open_workflow_agent.grpc_client import (
    APPLICATION_STATUS_HEADER,
    APPLICATION_STATUS_PROFILE,
    GrpcBinding,
    GrpcRegistry,
    _GrpcOutcomeUncertain,
    call_grpc,
    verify_sdk,
)
from open_workflow_agent.protocols import HttpClient
from open_workflow_agent.security import AuthenticationBinding
from open_workflow_agent.services import RuntimeServices
from pydantic import ValidationError

PROTO = b"""syntax = "proto3"; package bound;
import "google/protobuf/struct.proto";
enum Mode { NONE = 0; ACTIVE = 1; }
message Request { int64 count = 1; bytes raw_data = 2; Mode mode = 3;
double ratio = 4; oneof choice { string choice_text = 5; int32 choice_number = 6; }
map<string,int64> counts = 7; google.protobuf.Value payload = 8; }
service Peer { rpc Echo(Request) returns (Request); }
"""


def descriptor_bytes(*, streaming=False, missing_import=False):
    """Trusted fixture's compiled descriptor artifact matches the original text."""
    file = descriptor_pb2.FileDescriptorProto(name="peer.proto", package="bound", syntax="proto3")
    file.dependency.append("google/protobuf/struct.proto")
    enum = file.enum_type.add(name="Mode")
    enum.value.add(name="NONE", number=0)
    enum.value.add(name="ACTIVE", number=1)
    request = file.message_type.add(name="Request")
    request.oneof_decl.add(name="choice")
    for number, name, kind in [
        (1, "count", 3),
        (2, "raw_data", 12),
        (3, "mode", 14),
        (4, "ratio", 1),
        (5, "choice_text", 9),
        (6, "choice_number", 5),
    ]:
        field = request.field.add(name=name, number=number, type=kind, label=1)
        if kind == 14:
            field.type_name = ".bound.Mode"
        if number in {5, 6}:
            field.oneof_index = 0
    entry = request.nested_type.add(name="CountsEntry")
    entry.options.map_entry = True
    entry.field.add(name="key", number=1, type=9, label=1)
    entry.field.add(name="value", number=2, type=3, label=1)
    request.field.add(
        name="counts", number=7, type=11, label=3, type_name=".bound.Request.CountsEntry"
    )
    request.field.add(
        name="payload", number=8, type=11, label=1, type_name=".google.protobuf.Value"
    )
    service = file.service.add(name="Peer")
    service.method.add(
        name="Echo",
        input_type=".bound.Request",
        output_type=".bound.Request",
        server_streaming=streaming,
    )
    if missing_import:
        file.dependency.append("untrusted.proto")
    files = descriptor_pb2.FileDescriptorSet()
    files.file.add().ParseFromString(struct_pb2.DESCRIPTOR.serialized_pb)
    files.file.add().CopyFrom(file)
    return files.SerializeToString(deterministic=True)


def binding(endpoint="http://127.0.0.1:12345", **overrides):
    descriptor = descriptor_bytes()
    return replace(
        GrpcBinding(
            id="fixture-v1",
            proto_uri="https://metadata.test/peer.proto",
            proto_filename="peer.proto",
            proto_source=PROTO,
            proto_sha256=hashlib.sha256(PROTO).hexdigest(),
            descriptor_set=descriptor,
            descriptor_sha256=hashlib.sha256(descriptor).hexdigest(),
            endpoint=endpoint,
            service="bound.Peer",
            method="Echo",
            allow_insecure_loopback=True,
        ),
        **overrides,
    )


def payload(selected, arguments=None):
    endpoint = urlparse(selected.endpoint)
    return {
        "proto": {"endpoint": {"uri": selected.proto_uri}},
        "service": {"name": selected.service, "host": endpoint.hostname, "port": endpoint.port},
        "method": selected.method,
        "arguments": arguments or {},
    }


@asynccontextmanager
async def peer(*, outcome="echo", tls=None):
    # Absence/wrong SDK is a failure, never a passing native/server skip.
    verify_sdk()
    grpc = importlib.import_module("grpc")
    pool = descriptor_pool.DescriptorPool()
    files = descriptor_pb2.FileDescriptorSet.FromString(descriptor_bytes())
    for file in files.file:
        pool.Add(file)
    message = message_factory.GetMessageClass(pool.FindMessageTypeByName("bound.Request"))
    calls = []
    entered = asyncio.Event()

    async def handle(request, context):
        metadata = dict(context.invocation_metadata())
        calls.append({"request": request, "metadata": metadata, "auth": context.auth_context()})
        entered.set()
        if outcome in {"status", "status-unproved", "wrong-status-key"}:
            if outcome != "status-unproved":
                receipt = {
                    "profile": APPLICATION_STATUS_PROFILE,
                    "operation_key": metadata["x-owa-operation-id"]
                    if outcome == "status"
                    else "foreign",
                    "status": "NOT_FOUND",
                }
                context.set_trailing_metadata(((APPLICATION_STATUS_HEADER, json.dumps(receipt)),))
            await context.abort(grpc.StatusCode.NOT_FOUND, "private-remote-detail")
        if outcome == "stall":
            await asyncio.Event().wait()
        if outcome == "large":
            request.raw_data = b"x" * 2048
        if outcome == "nonfinite":
            request.ratio = float("inf")
        return request

    def serialize(message):
        data = message.SerializeToString(deterministic=True)
        if outcome == "unknown-field":
            data += b"\x98\x06\x01"
        if outcome == "malformed":
            data = b"\x80"
        return data

    server = grpc.aio.server()
    server.add_generic_rpc_handlers(
        (
            grpc.method_handlers_generic_handler(
                "bound.Peer",
                {
                    "Echo": grpc.unary_unary_rpc_method_handler(
                        handle,
                        request_deserializer=message.FromString,
                        response_serializer=serialize,
                    ),
                },
            ),
        )
    )
    if tls:
        certificate, private_key = tls.certificates["localhost"]
        credentials = grpc.ssl_server_credentials(
            ((private_key.read_bytes(), certificate.read_bytes()),),
            root_certificates=b"\n".join(
                tls.certificates[name][0].read_bytes() for name in ("client-A", "client-B")
            ),
            require_client_auth=True,
        )
        port = server.add_secure_port("127.0.0.1:0", credentials)
    else:
        port = server.add_insecure_port("127.0.0.1:0")
    assert port > 0
    await server.start()
    selected = binding(
        f"{'https://localhost' if tls else 'http://127.0.0.1'}:{port}",
        application_status_profile=True,
    )

    async def resolve(endpoint):
        assert endpoint == selected.endpoint
        return ("127.0.0.1",)

    http = HttpClient(
        timeout=2, allowed_hosts={"127.0.0.1", "localhost"}, destination_resolver=resolve
    )
    try:
        yield selected, http, calls, entered
    finally:
        await asyncio.wait_for(server.stop(0), timeout=3)


@pytest.mark.asyncio
async def test_actual_unary_preserves_protojson_bytes_int64_enum_oneof_map_and_key(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("grpc_proxy", "http://127.0.0.1:1")
    async with peer() as (selected, http, calls, _entered):
        arguments = {
            "count": "9223372036854775807",
            "rawData": "AP8=",
            "mode": "ACTIVE",
            "choiceText": "original",
            "counts": {"large": "9007199254740993"},
            "payload": None,
        }
        result = await asyncio.wait_for(
            call_grpc(
                http,
                payload(selected, arguments),
                registry=GrpcRegistry([selected]),
                operation_key="original-effect-key",
            ),
            timeout=3,
        )
        assert result == arguments
        assert len(calls) == 1
        assert calls[0]["metadata"]["x-owa-operation-id"] == "original-effect-key"
        assert calls[0]["metadata"]["idempotency-key"] == "original-effect-key"


@pytest.mark.parametrize(
    "damage",
    [
        "source",
        "descriptor",
        "source-name",
        "service",
        "method",
        "streaming",
        "missing-import",
        "plaintext",
        "duplicate",
    ],
)
def test_invalid_registered_descriptor_profile_never_requires_channel_or_io(damage):
    selected = binding()
    if damage == "source":
        selected = replace(selected, proto_source=PROTO + b"changed")
    elif damage == "descriptor":
        selected = replace(selected, descriptor_sha256="0" * 64)
    elif damage == "source-name":
        selected = replace(selected, proto_filename="other.proto")
    elif damage == "service":
        selected = replace(selected, service="bound.Foreign")
    elif damage == "method":
        selected = replace(selected, method="Foreign")
    elif damage in {"streaming", "missing-import"}:
        data = descriptor_bytes(
            streaming=damage == "streaming", missing_import=damage == "missing-import"
        )
        selected = replace(
            selected, descriptor_set=data, descriptor_sha256=hashlib.sha256(data).hexdigest()
        )
    elif damage == "plaintext":
        selected = replace(selected, endpoint="http://business.test:80")
    with pytest.raises(ToolError):
        GrpcRegistry([selected, selected] if damage == "duplicate" else [selected])


@pytest.mark.parametrize(
    "damage",
    [
        "proto",
        "host",
        "port",
        "service",
        "method",
        "unknown",
        "alias",
        "bytes",
        "enum",
        "oneof",
        "nonfinite",
        "size",
    ],
)
def test_mismatched_identity_and_invalid_protojson_fail_before_dispatch(damage):
    selected = binding(max_request_bytes=128)
    value = payload(selected)
    if damage == "proto":
        value["proto"]["endpoint"]["uri"] = "https://foreign.test/peer.proto"
    elif damage in {"host", "port"}:
        value["service"][damage] = "foreign.test" if damage == "host" else 54321
    elif damage == "service":
        value["service"]["name"] = "bound.Foreign"
    elif damage == "method":
        value["method"] = "Foreign"
    else:
        value["arguments"] = {
            "unknown": {"extra": True},
            "alias": {"rawData": "AA==", "raw_data": "AA=="},
            "bytes": {"rawData": "!!!!"},
            "enum": {"mode": 999},
            "oneof": {"choiceText": "first", "choiceNumber": 1},
            "nonfinite": {"ratio": "Infinity"},
            "size": {"choiceText": "x" * 200},
        }[damage]
    with pytest.raises(ToolError):
        GrpcRegistry([selected]).prepare(value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome",
    ["status-unproved", "wrong-status-key", "malformed", "unknown-field", "nonfinite", "large"],
)
async def test_post_dispatch_unproven_result_is_uncertain_without_business_retry(outcome):
    async with peer(outcome=outcome) as (selected, http, calls, _entered):
        if outcome == "large":
            selected = replace(selected, max_response_bytes=64)
        with pytest.raises(_GrpcOutcomeUncertain) as failure:
            await call_grpc(
                http,
                payload(selected, {"choiceText": "committed"}),
                registry=GrpcRegistry([selected]),
                operation_key="one-accepted-operation",
            )
        assert len(calls) == 1
        assert "private-remote-detail" not in str(failure.value.as_dict())


@pytest.mark.asyncio
async def test_correlated_declared_remote_application_status_is_known_and_sanitized():
    async with peer(outcome="status") as (selected, http, calls, _entered):
        with pytest.raises(ToolError) as failure:
            await call_grpc(
                http,
                payload(selected),
                registry=GrpcRegistry([selected]),
                operation_key="known-operation",
            )
        assert failure.value.details == {"reason": "application_status", "status": "NOT_FOUND"}
        assert len(calls) == 1 and "private-remote-detail" not in str(failure.value.as_dict())


@pytest.mark.asyncio
@pytest.mark.parametrize("control", ["deadline", "cancel"])
async def test_deadline_is_uncertain_and_host_cancellation_propagates(control):
    async with peer(outcome="stall") as (selected, http, calls, entered):
        task = asyncio.create_task(
            call_grpc(
                http,
                payload(selected),
                registry=GrpcRegistry([selected]),
                remaining_timeout=1 if control == "deadline" else 2,
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=2)
        if control == "cancel":
            task.cancel()
        expected = asyncio.CancelledError if control == "cancel" else _GrpcOutcomeUncertain
        with pytest.raises(expected):
            await asyncio.wait_for(task, timeout=3)
        assert len(calls) == 1


@pytest.mark.asyncio
async def test_whole_dns_deadline_is_known_before_business_dispatch():
    async with peer() as (selected, http, calls, _entered):

        async def slow(_endpoint):
            await asyncio.Event().wait()

        http.destination_resolver = slow
        with pytest.raises(ToolError) as failure:
            await asyncio.wait_for(
                call_grpc(
                    http,
                    payload(selected),
                    registry=GrpcRegistry([selected]),
                    remaining_timeout=0.05,
                ),
                timeout=1,
            )
        assert failure.value.details["reason"] == "pre_dispatch_failure" and calls == []


@pytest.mark.asyncio
async def test_credentials_need_exact_declared_grant_before_preparation_or_dns():
    selected = binding("https://localhost:443")
    touched = []

    class ForbiddenAuthentication:
        def headers(self, endpoint):
            touched.append(endpoint)
            raise AssertionError("credentials must remain untouched")

    async def forbidden(endpoint):
        touched.append(endpoint)
        raise AssertionError("DNS must remain untouched")

    with pytest.raises(ToolError) as failure:
        await call_grpc(
            HttpClient(destination_resolver=forbidden),
            payload(selected),
            registry=GrpcRegistry([selected]),
            authentication=ForbiddenAuthentication(),
        )
    assert failure.value.details["reason"] == "unbound_credentials" and touched == []


@pytest.mark.parametrize("package", ["grpcio", "protobuf"])
@pytest.mark.parametrize("installation", ["missing", "wrong"])
def test_unsupported_sdk_installation_has_no_dispatch_claim(monkeypatch, package, installation):
    def distribution(name):
        if name == package:
            if installation == "missing":
                raise PackageNotFoundError(name)
            return "unreviewed"
        return grpc_client.GRPC_SDK_VERSION if name == "grpcio" else grpc_client.PROTOBUF_VERSION

    monkeypatch.setattr(grpc_client, "version", distribution)
    with pytest.raises(ToolError) as failure:
        verify_sdk()
    assert failure.value.details == {"reason": "unsupported_sdk"}


@pytest.mark.asyncio
async def test_actual_mtls_authority_and_selected_credentials_are_call_local(tmp_path):
    from _auth_tls_fixture import auth_tls_receiver

    with auth_tls_receiver(tmp_path) as tls:
        async with peer(tls=tls) as (selected, http, calls, _entered):
            original = tuple(str(path) for path in tls.certificates["client-B"])
            http.client_cert = original

            class SelectedAuthentication:
                def headers(self, endpoint):
                    raise AssertionError("async call-local binding required")

                async def prepare(self, endpoint, *, remaining_timeout):
                    assert endpoint == selected.endpoint and 0 < remaining_timeout <= 2
                    return AuthenticationBinding(
                        {"Authorization": "Bearer controlled-test-secret"},
                        client_cert=tuple(str(path) for path in tls.certificates["client-A"]),
                        ca_bundle=tls.ca_bundle,
                    )

            result = await call_grpc(
                http,
                payload(selected, {"choiceText": "mtls"}),
                registry=GrpcRegistry([selected]),
                authentication=SelectedAuthentication(),
                credential_endpoints=frozenset({selected.endpoint}),
            )
            assert result == {"choiceText": "mtls"} and len(calls) == 1
            assert calls[0]["auth"]["x509_common_name"] == [b"client-A"]
            assert calls[0]["metadata"]["authorization"] == "Bearer controlled-test-secret"
            assert http.client_cert == original


@pytest.mark.asyncio
async def test_wrong_tls_authority_never_dispatches_business(tmp_path):
    from _auth_tls_fixture import auth_tls_receiver

    with auth_tls_receiver(tmp_path) as tls:
        async with peer(tls=tls) as (selected, http, calls, _entered):
            port = urlparse(selected.endpoint).port
            selected = replace(selected, endpoint=f"https://foreign.test:{port}")
            http.allowed_hosts = {"foreign.test"}

            async def pinned(endpoint):
                assert endpoint == selected.endpoint
                return ("127.0.0.1",)

            http.destination_resolver = pinned

            class Identity:
                def headers(self, endpoint):
                    raise AssertionError("selected asynchronous binding required")

                async def prepare(self, endpoint, *, remaining_timeout):
                    return AuthenticationBinding(
                        {},
                        client_cert=tuple(str(path) for path in tls.certificates["client-A"]),
                        ca_bundle=tls.ca_bundle,
                    )

            with pytest.raises(ToolError) as failure:
                await call_grpc(
                    http,
                    payload(selected),
                    registry=GrpcRegistry([selected]),
                    authentication=Identity(),
                    credential_endpoints=frozenset({selected.endpoint}),
                    remaining_timeout=0.3,
                )
            assert failure.value.details["reason"] == "pre_dispatch_failure" and calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("control", ["tls-disabled", "unresolved-auth", "response-limit"])
async def test_unsupported_security_and_limits_fail_before_dns_or_business(control):
    selected = binding("https://localhost:443")
    value = payload(selected)
    touched = []

    async def forbidden(endpoint):
        touched.append(endpoint)
        raise AssertionError("no DNS before valid admission")

    http = HttpClient(destination_resolver=forbidden)
    if control == "tls-disabled":
        http.verify_tls = False
    elif control == "unresolved-auth":
        value["service"]["authentication"] = "deployment-profile"
    else:
        http.max_response_bytes = 0
    with pytest.raises(ToolError):
        await call_grpc(http, value, registry=GrpcRegistry([selected]))
    assert touched == []


@pytest.mark.asyncio
async def test_reserved_metadata_cannot_forge_transport_or_operation_status():
    selected = binding("https://localhost:443")
    touched = []

    async def forbidden(endpoint):
        touched.append(endpoint)
        raise AssertionError("metadata fails before DNS")

    class InvalidMetadata:
        def headers(self, endpoint):
            return {"grpc-status": "0", "Authorization": "controlled-test-secret"}

    with pytest.raises(ToolError) as failure:
        await call_grpc(
            HttpClient(destination_resolver=forbidden),
            payload(selected),
            registry=GrpcRegistry([selected]),
            authentication=InvalidMetadata(),
            credential_endpoints=frozenset({selected.endpoint}),
        )
    assert failure.value.details["reason"] == "invalid_metadata" and touched == []
    assert "controlled-test-secret" not in str(failure.value.as_dict())


def deployment_binding():
    selected = binding()
    return {
        "proto_uri": selected.proto_uri,
        "proto_filename": selected.proto_filename,
        "proto_source": selected.proto_source.decode("utf-8"),
        "proto_sha256": selected.proto_sha256,
        "descriptor_set_base64": base64.b64encode(selected.descriptor_set).decode("ascii"),
        "descriptor_sha256": selected.descriptor_sha256,
        "endpoint": selected.endpoint,
        "service": selected.service,
        "method": selected.method,
        "allow_insecure_loopback": True,
    }


@pytest.mark.parametrize("corruption", ["source", "descriptor", "base64"])
def test_deployment_binding_rejects_changed_artifacts_before_sdk_or_dispatch(
    monkeypatch, corruption
):
    touched = []

    def forbidden():
        touched.append("sdk")
        raise AssertionError("deployment metadata validation does not activate the SDK")

    monkeypatch.setattr(grpc_client, "verify_sdk", forbidden)
    value = deployment_binding()
    if corruption == "source":
        value["proto_source"] += "// changed original source"
    elif corruption == "descriptor":
        value["descriptor_set_base64"] = base64.b64encode(b"changed descriptor").decode("ascii")
    else:
        value["descriptor_set_base64"] = "not-base64!"
    with pytest.raises(ValidationError):
        ProtocolConfig.model_validate({"grpc_bindings": {"fixture-v1": value}})
    assert touched == []


def test_recovery_registry_is_compact_and_rechecks_bytes_without_protocol_imports(monkeypatch):
    selected = GrpcBindingConfig.model_validate(deployment_binding())
    snapshot = selected.recovery_snapshot("fixture-v1")
    assert snapshot["proto_sha256"] == hashlib.sha256(PROTO).hexdigest()
    assert snapshot["descriptor_sha256"] == hashlib.sha256(descriptor_bytes()).hexdigest()
    assert "proto_source" not in snapshot and "descriptor_set_base64" not in snapshot
    assert len(json.dumps(snapshot)) < 2048

    import builtins

    original = builtins.__import__

    def no_protocol_import(name, *args, **kwargs):
        assert name not in {"grpc", "grpc_client", "google.protobuf"}
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_protocol_import)
    assert selected.recovery_snapshot("fixture-v1") == snapshot
    damaged = selected.model_copy(update={"proto_source": selected.proto_source + "// mutation"})
    with pytest.raises(ValueError, match="integrity"):
        damaged.recovery_snapshot("fixture-v1")


@pytest.mark.parametrize("package", ["grpcio", "protobuf"])
def test_recovery_profile_pins_actual_native_sdk_binary_bytes(tmp_path, monkeypatch, package):
    from open_workflow_agent import recovery_definitions

    code = tmp_path / "codec.py"
    binary = tmp_path / "codec.pyd"
    code.write_bytes(b"# codec\n")
    binary.write_bytes(b"original-native-codec")

    class Installed:
        files = [PurePosixPath("codec.py"), PurePosixPath("codec.pyd")]

        def locate_file(self, relative):
            return tmp_path / str(relative)

    monkeypatch.setattr(
        recovery_definitions.importlib.metadata, "distribution", lambda _: Installed()
    )
    original = recovery_definitions._sdk_digest(package)
    before = binary.stat()
    binary.write_bytes(b"replaced-native-codec")
    os.utime(binary, ns=(before.st_atime_ns, before.st_mtime_ns + 2_000_000_000))
    assert recovery_definitions._sdk_digest(package) != original
    binary.unlink()
    with pytest.raises(InvocationStateError) as failure:
        recovery_definitions._sdk_digest(package)
    assert failure.value.details["reason"] == "runtime_profile_unavailable"


@pytest.mark.parametrize("root", ["input_type", "output_type"])
def test_root_value_codec_rejected_at_descriptor_admission(root):
    selected = binding()
    files = descriptor_pb2.FileDescriptorSet.FromString(selected.descriptor_set)
    method = files.file[-1].service[0].method[0]
    setattr(method, root, ".google.protobuf.Value")
    raw = files.SerializeToString(deterministic=True)
    selected = replace(
        selected, descriptor_set=raw, descriptor_sha256=hashlib.sha256(raw).hexdigest()
    )
    with pytest.raises(ToolError) as failure:
        GrpcRegistry([selected])
    assert failure.value.details["reason"] == "invalid_descriptor"


@pytest.mark.parametrize("enabled", [False, True])
def test_only_activated_registry_requires_reviewed_sdk_at_startup(tmp_path, monkeypatch, enabled):
    def missing(name):
        raise PackageNotFoundError(name)

    monkeypatch.setattr(grpc_client, "version", missing)
    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "protocols": {"grpc_bindings": {"fixture-v1": deployment_binding()} if enabled else {}},
        }
    )
    if enabled:
        with pytest.raises(ToolError) as failure:
            RuntimeServices(config, database_root=tmp_path)
        assert failure.value.details["reason"] == "unsupported_sdk"
        assert not (tmp_path / "runtime.sqlite3").exists()
    else:
        services = RuntimeServices(config, database_root=tmp_path)
        try:
            assert services.grpc_enabled is False and services.grpc_registry.snapshot() == []
        finally:
            services.close()


@pytest.mark.parametrize("enabled", [False, True])
def test_captured_catalog_grpc_requires_same_private_activation_gate(enabled):
    from open_workflow_agent.external_catalog import (
        _validate_function_definition,
        parse_catalog_function_reference,
    )

    definition = {"call": "grpc", "with": payload(binding())}
    reference = parse_catalog_function_reference("peer:1.0.0@trusted")
    assert reference is not None
    if not enabled:
        with pytest.raises(UnsupportedWorkflowFeature):
            _validate_function_definition(definition, reference)
    else:
        call, parameters = _validate_function_definition(definition, reference, grpc_enabled=True)
        assert call == "grpc" and parameters == definition["with"]
