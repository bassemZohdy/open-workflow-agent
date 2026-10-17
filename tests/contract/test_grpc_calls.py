"""Actual unary SDK peers and native, database-backed workflow recovery."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib
import json
import os
from contextlib import asynccontextmanager

import httpx
import pytest
from engine_cases import engine_cases
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory, struct_pb2
from open_workflow_agent import external_catalog, grpc_client
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import ExternalCatalogConfig, RuntimeConfig
from open_workflow_agent.effects import EffectIdentity, ReceiptDependency
from open_workflow_agent.errors import InvocationStateConflict, OwaError, UnsupportedWorkflowFeature
from open_workflow_agent.grpc_client import APPLICATION_STATUS_HEADER, APPLICATION_STATUS_PROFILE
from open_workflow_agent.protocols import HttpClient
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow, resolve_and_compile_workflow
from test_deadline_recovery import _policy_records
from test_durable_timers import _use_clock
from test_native_checkpoint_fencing import _snapshot

pytestmark = pytest.mark.usefixtures("explicit_workflow_delivery")
ENGINES = [case for case in engine_cases() if case[0] in {"adk", "langgraph"}]
PROTO = b"""syntax = "proto3"; package contract;
import "google/protobuf/struct.proto";
message Payload { int64 number = 1; bytes data = 2; google.protobuf.Value value = 3; }
service Peer { rpc Call(Payload) returns (Payload); }
"""
URI = "https://metadata.test/contract.proto"


def descriptor():
    file = descriptor_pb2.FileDescriptorProto(
        name="contract.proto", package="contract", syntax="proto3"
    )
    file.dependency.append("google/protobuf/struct.proto")
    message = file.message_type.add(name="Payload")
    message.field.add(name="number", number=1, type=3, label=1)
    message.field.add(name="data", number=2, type=12, label=1)
    message.field.add(name="value", number=3, type=11, label=1, type_name=".google.protobuf.Value")
    file.service.add(name="Peer").method.add(
        name="Call", input_type=".contract.Payload", output_type=".contract.Payload"
    )
    result = descriptor_pb2.FileDescriptorSet()
    result.file.add().ParseFromString(struct_pb2.DESCRIPTOR.serialized_pb)
    result.file.add().CopyFrom(file)
    return result.SerializeToString(deterministic=True)


class Peer:
    def __init__(self, outcome="value"):
        self.outcome = outcome
        self.calls = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    @asynccontextmanager
    async def run(self):
        grpc_client.verify_sdk()
        grpc = importlib.import_module("grpc")
        pool = descriptor_pool.DescriptorPool()
        for file in descriptor_pb2.FileDescriptorSet.FromString(descriptor()).file:
            pool.Add(file)
        message = message_factory.GetMessageClass(pool.FindMessageTypeByName("contract.Payload"))

        async def invoke(request, context):
            metadata = dict(context.invocation_metadata())
            self.calls.append((request, metadata))
            self.entered.set()
            if self.outcome in {"stall", "late"}:
                await self.release.wait()
            if self.outcome in {"fault", "uncertain"}:
                if self.outcome == "fault":
                    proof = {
                        "profile": APPLICATION_STATUS_PROFILE,
                        "operation_key": metadata["x-owa-operation-id"],
                        "status": "NOT_FOUND",
                    }
                    context.set_trailing_metadata(((APPLICATION_STATUS_HEADER, json.dumps(proof)),))
                await context.abort(grpc.StatusCode.NOT_FOUND, "private controlled remote detail")
            response = message()
            response.CopyFrom(request)
            if self.outcome == "null":
                response.value.null_value = 0
            return response

        server = grpc.aio.server(options=(("grpc.enable_retries", 0),))
        server.add_generic_rpc_handlers(
            (
                grpc.method_handlers_generic_handler(
                    "contract.Peer",
                    {
                        "Call": grpc.unary_unary_rpc_method_handler(
                            invoke,
                            request_deserializer=message.FromString,
                            response_serializer=lambda value: value.SerializeToString(
                                deterministic=True
                            ),
                        )
                    },
                ),
            )
        )
        port = server.add_insecure_port("127.0.0.1:0")
        assert port > 0
        self.endpoint = f"http://127.0.0.1:{port}"
        self.port = port
        await asyncio.wait_for(server.start(), 3)
        try:
            yield self
        finally:
            self.release.set()
            await asyncio.wait_for(server.stop(0), 3)

    def config(self):
        encoded = descriptor()
        return {
            "proto_uri": URI,
            "proto_filename": "contract.proto",
            "proto_source": PROTO.decode(),
            "proto_sha256": hashlib.sha256(PROTO).hexdigest(),
            "descriptor_set_base64": base64.b64encode(encoded).decode(),
            "descriptor_sha256": hashlib.sha256(encoded).hexdigest(),
            "endpoint": self.endpoint,
            "service": "contract.Peer",
            "method": "Call",
            "allow_insecure_loopback": True,
            "application_status_profile": True,
        }


def runtime(path, backend, engine_name, peer, *, enabled=True):
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "persistence": {"datasource": datasource},
            "protocols": {"grpc_bindings": {"original-peer": peer.config()} if enabled else {}},
        }
    )
    services = RuntimeServices(
        config, model=FakeModel(), database_root=None if datasource else path
    )

    async def resolve(endpoint):
        assert endpoint == peer.endpoint
        return ("127.0.0.1",)

    services.protocols.http = HttpClient(allowed_hosts={"127.0.0.1"}, destination_resolver=resolve)
    assert (
        services.invocations.connection.kind == services.effect_journal.connection.kind == backend
    )
    if backend == "postgresql":
        assert services.database_root is None
        assert services.engine_database_path(engine_name, create_namespace=False).startswith(
            "postgres"
        )
    return services


def actual(engine, name):
    available = engine.native.available if name == "adk" else engine.native.task_nodes_available
    if not available:
        pytest.skip(f"actual {name} SDK is unavailable in this independent environment")


def source(peer):
    return {
        "document": {
            "dsl": "1.0.3",
            "namespace": "grpc-contract",
            "name": "peer",
            "version": "1.0.0",
        },
        "do": [
            {
                "peer": {
                    "call": "grpc",
                    "with": {
                        "proto": {"endpoint": URI},
                        "service": {
                            "name": "contract.Peer",
                            "host": "127.0.0.1",
                            "port": peer.port,
                        },
                        "method": "Call",
                        "arguments": {
                            "number": "9007199254740993",
                            "data": "AAE=",
                            "value": "${ . }",
                        },
                    },
                    "output": {"as": "${ .value }"},
                }
            }
        ],
    }


def handle(services, name, plan):
    return services.invocations.create(
        engine=name,
        session_id=None,
        user_id="original-user",
        owner_principal="original-owner",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )


def receipts(services, invocation):
    rows = services.effect_journal.connection.execute(
        "SELECT payload FROM workflow_effect_invocations WHERE invocation_id=?", (invocation,)
    ).fetchall()
    return [
        identity
        for row in rows
        if (identity := EffectIdentity.from_value(json.loads(row[0]))).operation == "call:grpc"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("route", ["inline", "captured-catalog"])
async def test_exact_native_route_protojson_and_stable_operation_key(
    tmp_path, monkeypatch, engine_name, engine_type, backend, route
):
    async with Peer().run() as peer:
        services = runtime(tmp_path, backend, engine_name, peer)
        engine = engine_type()
        actual(engine, engine_name)
        try:
            await engine.initialize(services)
            catalog_requests = []
            if route == "captured-catalog":
                remote = source(peer)["do"][0]["peer"]

                async def metadata(request):
                    assert request.method == "GET" and request.url.host == "metadata.test"
                    catalog_requests.append(request)
                    return httpx.Response(200, json=remote)

                async def resolve(endpoint, policy):
                    # The actual SDK oracle is local. Its exact approved
                    # destination seam does not relax production public DNS.
                    assert endpoint == peer.endpoint
                    assert "127.0.0.1" in policy.allowed_hosts
                    return ("127.0.0.1",)

                policy = ExternalCatalogConfig(allowed_hosts=["metadata.test", "127.0.0.1"])
                monkeypatch.setattr(external_catalog, "_resolve_external_destination", resolve)
                services.external_catalogs.policies = {"trusted": policy}
                services.external_catalogs.http = HttpClient(
                    transport=httpx.MockTransport(metadata)
                )
                definition = source(peer)
                definition["use"] = {"catalogs": {"trusted": {"endpoint": URI}}}
                definition["do"] = [{"remote": {"call": "peer:1.0.0@trusted"}}]
                plan = await asyncio.wait_for(
                    resolve_and_compile_workflow(
                        definition,
                        resolver=services.external_catalogs,
                        catalog=services.catalog,
                        trusted_catalogs=services.external_catalogs.policies,
                        grpc_enabled=services.grpc_enabled,
                    ),
                    15,
                )
                assert len(plan.imports) == len(catalog_requests) == 1
            else:
                plan = compile_workflow(source(peer), grpc_enabled=services.grpc_enabled)
            invocation = handle(services, engine_name, plan)
            result = await asyncio.wait_for(
                engine.invoke(await engine.compile(plan), invocation, {"original": None}), 15
            )
            assert result.status == "completed" and result.output == {"original": None}, (
                result.error
            )
            assert len(peer.calls) == 1 and services.model.calls == []
            assert len(catalog_requests) == (1 if route == "captured-catalog" else 0)
            request, metadata = peer.calls[0]
            assert request.number == 9007199254740993 and request.data == b"\x00\x01"
            business = receipts(services, invocation.invocation_id)
            assert len(business) == 1
            known = services.effect_journal.get(business[0])
            assert (
                known.status == "completed" and known.operation_id == metadata["x-owa-operation-id"]
            )
            assert metadata["idempotency-key"] == known.operation_id
            assert await engine.resume(invocation, {"changed": True}, plan) == result
            assert len(peer.calls) == 1 and services.invocations.read_input(invocation) == {
                "original": None
            }
            assert len(catalog_requests) == (1 if route == "captured-catalog" else 0)
        finally:
            await engine.shutdown()
            services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("outcome", ["value", "null", "fault"])
async def test_known_business_receipt_before_native_commit_survives_reopen(
    tmp_path, engine_name, engine_type, backend, outcome
):
    async with Peer(outcome).run() as peer:
        services = runtime(tmp_path, backend, engine_name, peer)
        engine = engine_type()
        actual(engine, engine_name)
        await engine.initialize(services)
        plan = compile_workflow(source(peer), grpc_enabled=True)
        invocation = handle(services, engine_name, plan)
        entered = asyncio.Event()
        original = engine.executor._call

        async def after_receipt(call, payload, state):
            try:
                output = await original(call, payload, state)
            except OwaError:
                entered.set()
                await asyncio.Event().wait()
                raise
            entered.set()
            await asyncio.Event().wait()
            return output

        engine.executor._call = after_receipt
        running = asyncio.create_task(engine.invoke(plan, invocation, {"original": None}))
        try:
            await asyncio.wait_for(entered.wait(), 15)
            business = receipts(services, invocation.invocation_id)
            assert len(business) == 1 and len(peer.calls) == 1
            known = services.effect_journal.get(business[0])
            assert known.status == ("faulted" if outcome == "fault" else "completed")
            proof = ReceiptDependency.capture(business[0], known).receipt_digest
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(running, 15)
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
            await engine.shutdown()
            services.close()
        recovered = runtime(tmp_path, backend, engine_name, peer)
        resumed = engine_type()
        try:
            await resumed.initialize(recovered)
            loaded = recovered.invocations.get(invocation.invocation_id)
            result = await asyncio.wait_for(
                resumed.resume(
                    loaded, {"changed": True}, recovered.resolve_original_definition(loaded)
                ),
                15,
            )
            assert result.status == ("faulted" if outcome == "fault" else "completed"), result.error
            if outcome != "fault":
                assert result.output == (None if outcome == "null" else {"original": None})
            else:
                assert result.error["code"] == "tool_error"
                assert "private controlled" not in json.dumps(result.error)
            assert len(peer.calls) == 1 and recovered.model.calls == []
            assert (
                ReceiptDependency.capture(
                    business[0], recovered.effect_journal.get(business[0])
                ).receipt_digest
                == proof
            )
            assert recovered.invocations.read_input(loaded) == {"original": None}
            assert (
                loaded.session_id == invocation.session_id
                and loaded.owner_principal == "original-owner"
            )
        finally:
            await resumed.shutdown()
            recovered.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_unproven_remote_status_bypasses_catch_retry_and_stays_pending(
    tmp_path, engine_name, engine_type, backend
):
    async with Peer("uncertain").run() as peer:
        services = runtime(tmp_path, backend, engine_name, peer)
        engine = engine_type()
        actual(engine, engine_name)
        try:
            await engine.initialize(services)
            definition = source(peer)
            definition["do"] = [
                {
                    "guard": {
                        "try": definition["do"],
                        "catch": {
                            "errors": {"with": {"type": "${ true }"}},
                            "retry": {
                                "limit": {"attempt": {"count": 3}},
                                "delay": {"milliseconds": 1},
                                "backoff": {"constant": {}},
                            },
                        },
                    }
                }
            ]
            plan = compile_workflow(definition, grpc_enabled=True)
            invocation = handle(services, engine_name, plan)
            result = await asyncio.wait_for(engine.invoke(plan, invocation, {"original": None}), 15)
            assert result.status == "faulted" and len(peer.calls) == 1
            business = receipts(services, invocation.invocation_id)
            assert (
                len(business) == 1 and services.effect_journal.get(business[0]).status == "pending"
            )
            assert (
                "private controlled" not in json.dumps(result.error) and services.model.calls == []
            )
            assert await engine.resume(invocation, {}, plan) == result and len(peer.calls) == 1
        finally:
            await engine.shutdown()
            services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("expired", [False, True])
async def test_original_workflow_deadline_survives_native_reopen(
    tmp_path, monkeypatch, engine_name, engine_type, backend, expired
):
    clock = [1000.0]
    _use_clock(monkeypatch, lambda: clock[0])
    async with Peer().run() as peer:
        services = runtime(tmp_path, backend, engine_name, peer)
        engine = engine_type()
        actual(engine, engine_name)
        await engine.initialize(services)
        definition = source(peer)
        definition["do"] = [
            {
                "scope": {
                    "timeout": {"after": {"seconds": 30}},
                    "do": [{"pause": {"wait": {"seconds": 10}}}, *definition["do"]],
                }
            }
        ]
        plan = compile_workflow(definition, grpc_enabled=True)
        invocation = handle(services, engine_name, plan)
        try:
            result = await asyncio.wait_for(engine.invoke(plan, invocation, {"original": None}), 15)
            assert result.status == "waiting" and peer.calls == []
            timing = _policy_records(services, invocation.invocation_id)
            assert len(timing) == 2
        finally:
            await engine.shutdown()
            services.close()
        clock[0] = 1031.0 if expired else 1010.0
        recovered = runtime(tmp_path, backend, engine_name, peer)
        resumed = engine_type()
        try:
            await resumed.initialize(recovered)
            loaded = recovered.invocations.get(invocation.invocation_id)
            result = await asyncio.wait_for(resumed.resume(loaded, {"changed": True}, plan), 15)
            if expired:
                assert result.status == "faulted" and result.error["details"]["status"] == 408
                assert peer.calls == [] and receipts(recovered, loaded.invocation_id) == []
            else:
                assert result.status == "completed" and len(peer.calls) == 1
                assert result.output == {"original": None}
            assert _policy_records(recovered, loaded.invocation_id) == timing
            assert (
                recovered.invocations.read_input(loaded) == {"original": None}
                and recovered.model.calls == []
            )
        finally:
            await resumed.shutdown()
            recovered.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("control", ["host-drain", "deadline"])
async def test_host_drain_does_not_forge_remote_fault_or_operator_cancel(
    tmp_path, engine_name, engine_type, backend, control
):
    async with Peer("stall").run() as peer:
        services = runtime(tmp_path, backend, engine_name, peer)
        engine = engine_type()
        actual(engine, engine_name)
        await engine.initialize(services)
        definition = source(peer)
        if control == "deadline":
            definition["do"][0]["peer"]["timeout"] = {"after": {"seconds": 1}}
        plan = compile_workflow(definition, grpc_enabled=True)
        invocation = handle(services, engine_name, plan)
        running = asyncio.create_task(engine.invoke(plan, invocation, {}))
        try:
            await asyncio.wait_for(peer.entered.wait(), 15)
            if control == "host-drain":
                await asyncio.wait_for(engine.shutdown(), 15)
                with pytest.raises(asyncio.CancelledError):
                    await running
            else:
                result = await asyncio.wait_for(running, 15)
                assert result.status == "faulted"
                assert "unresolved" in json.dumps(result.error).lower()
            loaded = services.invocations.get(invocation.invocation_id)
            if control == "host-drain":
                assert loaded.status == "running" and loaded.error is None
            else:
                assert loaded.status == "faulted"
            business = receipts(services, invocation.invocation_id)
            assert (
                len(business) == 1 and services.effect_journal.get(business[0]).status == "pending"
            )
            assert len(peer.calls) == 1 and services.model.calls == []
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
            await engine.shutdown()
            services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_distinct_loop_visits_have_distinct_authoritative_operation_keys(
    tmp_path, engine_name, engine_type, backend
):
    async with Peer().run() as peer:
        services = runtime(tmp_path, backend, engine_name, peer)
        engine = engine_type()
        actual(engine, engine_name)
        try:
            await engine.initialize(services)
            definition = source(peer)
            action = definition["do"][0]["peer"]
            action["with"]["arguments"]["value"] = "${ $number }"
            definition["do"] = [
                {"loop": {"for": {"each": "number", "in": [10, 20]}, "do": [{"peer": action}]}}
            ]
            plan = compile_workflow(definition, grpc_enabled=True)
            invocation = handle(services, engine_name, plan)
            result = await asyncio.wait_for(engine.invoke(plan, invocation, {}), 15)
            assert result.status == "completed" and len(peer.calls) == 2, result.error
            keys = [metadata["x-owa-operation-id"] for _, metadata in peer.calls]
            assert len(set(keys)) == 2 and len(receipts(services, invocation.invocation_id)) == 2
            assert services.model.calls == []
        finally:
            await engine.shutdown()
            services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("drift", ["registry", "source", "sdk"])
async def test_changed_deployment_profile_fails_before_resume_lease_or_native_write(
    tmp_path, monkeypatch, engine_name, engine_type, backend, drift
):
    async with Peer().run() as peer:
        services = runtime(tmp_path, backend, engine_name, peer)
        engine = engine_type()
        actual(engine, engine_name)
        try:
            await engine.initialize(services)
            definition = source(peer)
            definition["do"].insert(0, {"pause": {"wait": {"seconds": 100}}})
            plan = compile_workflow(definition, grpc_enabled=True)
            invocation = handle(services, engine_name, plan)
            result = await asyncio.wait_for(engine.invoke(plan, invocation, {}), 15)
            assert result.status == "waiting" and peer.calls == []
            native_before = _snapshot(services, invocation, backend)
            row_before = services.invocations.get(invocation.invocation_id)._persisted_payload
            ownership_before = services.execution_ownership.connection.execute(
                "SELECT * FROM invocation_ownership WHERE invocation_id=?",
                (invocation.invocation_id,),
            ).fetchone()
            if drift == "registry":
                services.protocols.grpc_registry = grpc_client.GrpcRegistry()
            elif drift == "source":
                old = services.config.protocols.grpc_bindings["original-peer"]
                services.config.protocols.grpc_bindings["original-peer"] = old.model_copy(
                    update={"proto_source": old.proto_source + "// changed"}
                )
            else:
                original = grpc_client.version
                monkeypatch.setattr(
                    grpc_client,
                    "version",
                    lambda name: "unreviewed" if name == "grpcio" else original(name),
                )
            with pytest.raises((OwaError, ValueError)):
                await asyncio.wait_for(engine.resume(invocation, {"changed": True}, plan), 15)
            assert _snapshot(services, invocation, backend) == native_before
            assert (
                services.invocations.get(invocation.invocation_id)._persisted_payload == row_before
            )
            assert (
                services.execution_ownership.connection.execute(
                    "SELECT * FROM invocation_ownership WHERE invocation_id=?",
                    (invocation.invocation_id,),
                ).fetchone()
                == ownership_before
            )
            assert peer.calls == [] and services.model.calls == []
        finally:
            await engine.shutdown()
            services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_late_grpc_worker_cannot_finish_pending_receipt_after_real_takeover(
    tmp_path, engine_name, engine_type, backend
):
    async with Peer("late").run() as peer:
        services = runtime(tmp_path, backend, engine_name, peer)
        successor_services = runtime(tmp_path, backend, engine_name, peer)
        engine = engine_type()
        actual(engine, engine_name)
        await engine.initialize(services)
        plan = compile_workflow(source(peer), grpc_enabled=True)
        invocation = handle(services, engine_name, plan)
        running = asyncio.create_task(engine.invoke(plan, invocation, {}))
        successor = None
        try:
            await asyncio.wait_for(peer.entered.wait(), 15)
            native_before = _snapshot(services, invocation, backend)
            business = receipts(services, invocation.invocation_id)
            assert (
                len(business) == 1 and services.effect_journal.get(business[0]).status == "pending"
            )
            old = engine._execution_leases[invocation.invocation_id]
            services.execution_ownership.release(old)
            loaded = successor_services.invocations.get(invocation.invocation_id)
            successor = successor_services.execution_ownership.acquire(loaded)
            assert successor.generation > old.generation and successor.owner != old.owner
            peer.release.set()
            with pytest.raises(InvocationStateConflict):
                await asyncio.wait_for(running, 15)
            assert services.effect_journal.get(business[0]).status == "pending"
            assert _snapshot(services, invocation, backend) == native_before
            assert successor_services.invocations.get(invocation.invocation_id).status == "running"
            assert len(peer.calls) == 1 and services.model.calls == []
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
            if successor is not None:
                successor_services.execution_ownership.release(successor)
            await engine.shutdown()
            successor_services.close()
            services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("rejection", ["disabled", "foreign-method"])
async def test_deployment_admission_rejects_before_execution_ownership_or_business(
    tmp_path, engine_name, engine_type, backend, rejection
):
    async with Peer().run() as peer:
        services = runtime(tmp_path, backend, engine_name, peer, enabled=rejection != "disabled")
        engine = engine_type()
        actual(engine, engine_name)
        try:
            await engine.initialize(services)
            definition = source(peer)
            with pytest.raises(UnsupportedWorkflowFeature):
                compile_workflow(definition)
            if rejection == "foreign-method":
                definition["do"][0]["peer"]["with"]["method"] = "Foreign"
            plan = compile_workflow(definition, grpc_enabled=True)
            invocation = handle(services, engine_name, plan)
            with pytest.raises(OwaError):
                await asyncio.wait_for(engine.invoke(plan, invocation, {}), 15)
            assert (
                services.execution_ownership.connection.execute(
                    "SELECT 1 FROM invocation_ownership WHERE invocation_id=?",
                    (invocation.invocation_id,),
                ).fetchone()
                is None
            )
            assert receipts(services, invocation.invocation_id) == [] and peer.calls == []
            assert services.model.calls == []
        finally:
            await engine.shutdown()
            services.close()
