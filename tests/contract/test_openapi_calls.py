"""Actual native engines retain admitted operations and common HTTP outcomes."""

from __future__ import annotations

import asyncio
import copy
import json
import os
from dataclasses import replace

import httpx
import pytest
from engine_cases import engine_cases
from open_workflow_agent import external_catalog
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import ExternalCatalogConfig, RuntimeConfig
from open_workflow_agent.effects import EffectIdentity, ReceiptDependency, effect_request_digest
from open_workflow_agent.errors import OwaError
from open_workflow_agent.openapi_admission import OPERATION_POLICY
from open_workflow_agent.openapi_client import OpenApiDescription
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow, resolve_and_compile_workflow
from openapi_fixtures import operation_document
from test_deadline_recovery import _policy_records
from test_durable_timers import _use_clock
from test_native_checkpoint_fencing import _snapshot as native_snapshot

pytestmark = pytest.mark.usefixtures("explicit_workflow_delivery")
ENGINES = [case for case in engine_cases() if case[0] in {"adk", "langgraph"}]
DOCUMENT = "https://metadata.test/spec.json"
SERVER = "https://business.test"


def runtime(path, backend, engine_name, *, model=None, options=None):
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "persistence": {"datasource": datasource},
            **(options or {}),
        }
    )
    services = RuntimeServices(
        config,
        model=model or FakeModel(),
        database_root=None if datasource else path,
        recovery_profile_provider=lambda engine: {"id": "openapi-native-fixture", "version": "1"},
    )
    assert (
        services.invocations.connection.kind == services.effect_journal.connection.kind == backend
    )
    if backend == "postgresql":
        assert services.database_root is None
        assert services.engine_database_path(engine_name, create_namespace=False).startswith(
            "postgres"
        )
    return services


def actual_native(engine, name):
    if not (engine.native.available if name == "adk" else engine.native.task_nodes_available):
        pytest.skip(f"actual {name} SDK is unavailable in this independent environment")


def source():
    return {
        "document": {
            "dsl": "1.0.3",
            "namespace": "openapi-contract",
            "name": "operation",
            "version": "1.0.0",
        },
        "do": [
            {
                "peer": {
                    "call": "openapi",
                    "with": {
                        "document": {"endpoint": DOCUMENT},
                        "operationId": "getItem",
                        "parameters": {"id": "original/value"},
                    },
                }
            }
        ],
    }


def handle(services, engine_name, plan):
    return services.invocations.create(
        engine=engine_name,
        session_id="original-session",
        user_id="original-user",
        owner_principal="original-owner",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )


def identities(services, invocation_id, operation="call:openapi"):
    rows = services.effect_journal.connection.execute(
        "SELECT payload FROM workflow_effect_invocations WHERE invocation_id=?", (invocation_id,)
    ).fetchall()
    return [
        identity
        for row in rows
        if (identity := EffectIdentity.from_value(json.loads(row[0]))).operation == operation
    ]


def imported_fixture(services, receiver, monkeypatch):
    """A real catalog closure with deterministic, deployment-approved mock DNS."""
    fetched = []
    inline = source()["do"][0]["peer"]
    inline["with"]["parameters"]["id"] = "${ .id }"
    policy = ExternalCatalogConfig(
        allowed_hosts=["catalog.test", "metadata.test", "business.test"], cache_ttl_seconds=0
    )

    async def request(message):
        if message.url.host == "catalog.test":
            assert "authorization" not in message.headers
            fetched.append(message)
            return httpx.Response(404 if receiver.deleted else 200, json=inline)
        return await receiver(message)

    async def resolve(endpoint, declaration):
        assert httpx.URL(endpoint).host in policy.allowed_hosts
        assert declaration == policy
        # Only MockTransport consumes this deterministic address; public-DNS
        # production admission and real-socket controls remain independent.
        return ("93.184.216.34",)

    monkeypatch.setattr(external_catalog, "_resolve_external_destination", resolve)
    client = HttpClient(transport=httpx.MockTransport(request))
    services.protocols = ProtocolServices(client)
    services.tools.protocols = services.protocols
    services.external_catalogs.policies = {"trusted": policy}
    services.external_catalogs.http = client
    definition = source()
    definition["use"] = {"catalogs": {"trusted": {"endpoint": "https://catalog.test/root"}}}
    definition["do"] = [
        {
            "remote": {
                "call": "operation:1.0.0@trusted",
                "with": {"id": "${ .id }", "untouched": "${ .untouched }"},
            }
        }
    ]
    return definition, fetched


class Receiver:
    def __init__(self, outcome="value"):
        self.outcome = outcome
        self.calls = []
        self.deleted = False
        self.expected_authorization = None
        self.dispatch_entered = asyncio.Event()
        self.dispatch_release = asyncio.Event()
        self.description = operation_document(
            SERVER,
            operation_id="getItem",
            path="/items/{id}",
            parameters=(("id", "path", "string"),),
        )

    async def __call__(self, request):
        self.calls.append(request)
        if str(request.url) == DOCUMENT:
            assert request.method == "GET" and "authorization" not in request.headers
            return (
                httpx.Response(404) if self.deleted else httpx.Response(200, json=self.description)
            )
        assert request.method == "GET" and request.url.host == "business.test"
        assert request.headers.get("authorization") == self.expected_authorization
        if self.outcome == "blocked":
            self.dispatch_entered.set()
            await self.dispatch_release.wait()
        if self.outcome == "uncertain":
            raise httpx.ReadError("private-remote-detail")
        if self.outcome == "fault":
            return httpx.Response(503, json={"error": "controlled peer fault"})
        if self.outcome == "null":
            return httpx.Response(
                200, content=b"null", headers={"Content-Type": "application/json"}
            )
        return httpx.Response(200, json={"id": "original/value"})

    def install(self, services):
        services.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(self)))
        services.tools.protocols = services.protocols


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_registered_import_uses_original_parent_authority_for_inner_operation_admission(
    tmp_path, monkeypatch, engine_name, engine_type, backend
):
    engine = engine_type()
    actual_native(engine, engine_name)
    receiver = Receiver()
    services = runtime(tmp_path, backend, engine_name)
    definition, fetched = imported_fixture(services, receiver, monkeypatch)
    observed = []
    try:
        await services.external_catalogs.resolve_workflow(definition, services.catalog)
        plan = compile_workflow(definition, trusted_catalogs=services.external_catalogs.policies)
        assert plan.imports == ()
        await engine.initialize(services)
        invocation = handle(services, engine_name, plan)
        invoke_frozen = services.external_catalogs.invoke_frozen
        invoke_prepared = services.external_catalogs.invoke_prepared
        forwarded = []

        async def observe_prepared(binding, call, payload, **kwargs):
            selection = kwargs.get("_openapi_selection")
            assert call == "openapi" and selection is not None
            # The catalog task is transformed into an immutable execution
            # binding, but its operation selection still comes from the
            # original invocation's durable admission receipt.
            forwarded.append((binding, selection))
            return await invoke_prepared(binding, call, payload, **kwargs)

        services.external_catalogs.invoke_prepared = observe_prepared

        async def observe_inner(binding, payload, **kwargs):
            state = kwargs["parent_state"]
            original = state.metadata["_invocation_handle"]
            services.execution_ownership.verify(original._execution_lease)
            assert kwargs["_runtime_services"] is services
            assert original.invocation_id == invocation.invocation_id
            assert state.context == {"id": "original/value", "untouched": None}
            assert services.invocations.read_input(original) == state.context
            native_observer = state.metadata["_effect_receipt_observer"]

            async def admission(identity, receipt):
                await native_observer(identity, receipt)
                if identity.operation == OPERATION_POLICY:
                    assert (
                        receipt.output["invocation_binding"]["owner_principal"] == "original-owner"
                    )
                    assert (
                        receipt.output["invocation_binding"]["session_id"] == invocation.session_id
                    )
                    assert len(receiver.calls) == 1 and str(receiver.calls[0].url) == DOCUMENT
                    observed.append(ReceiptDependency.capture(identity, receipt))

            # Retain the real native observer while checking the copied inner
            # executor forwards its proof before any business request.
            state.metadata["_effect_receipt_observer"] = admission
            return await invoke_frozen(binding, payload, **kwargs)

        services.external_catalogs.invoke_frozen = observe_inner
        original = {"id": "original/value", "untouched": None}
        result = await asyncio.wait_for(engine.invoke(plan, invocation, original), 15)
        assert result.status == "completed" and result.output == {"id": "original/value"}, (
            result.error
        )
        policies = identities(services, invocation.invocation_id, OPERATION_POLICY)
        business = identities(services, invocation.invocation_id)
        assert len(policies) == len(business) == len(observed) == 1 and len(fetched) == 1
        assert len(forwarded) == 1
        forwarded_binding, forwarded_selection = forwarded[0]
        from open_workflow_agent.openapi_client import OpenApiOperationSelection

        verified_selection = OpenApiOperationSelection.from_value(forwarded_selection.as_value())
        assert verified_selection.operation_id == "getItem"
        assert verified_selection.description.uri == DOCUMENT
        assert (
            verified_selection.description.digest
            == OpenApiDescription.capture(DOCUMENT, receiver.description).digest
        )
        assert forwarded_binding.reference == "operation:1.0.0@trusted"
        assert forwarded_binding.execution_definition["call"] == "openapi"
        assert business[0].task_reference == "/do/0/remote"
        stored = services.effect_journal.get(policies[0])
        assert verified_selection.as_value() == stored.output["selection"]
        assert stored.output["invocation_binding"]["invocation_id"] == invocation.invocation_id
        assert stored.output["invocation_binding"]["owner_principal"] == "original-owner"
        ReceiptDependency.capture(policies[0], stored).verify(services.effect_journal)
        assert (
            services.invocations.read_input(invocation) == original and services.model.calls == []
        )
        assert (
            len(receiver.calls) == 2
            and str(receiver.calls[1].url) == SERVER + "/items/original%2Fvalue"
        )
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_registered_import_forwards_named_openapi_authentication_without_catalog_auth_leak(
    tmp_path, monkeypatch, engine_name, engine_type, backend
):
    monkeypatch.setenv("OPENAPI_TEST_TOKEN", "operation-only-secret")
    engine = engine_type()
    actual_native(engine, engine_name)
    receiver = Receiver()
    receiver.expected_authorization = "Bearer operation-only-secret"
    receiver.description["components"] = {
        "securitySchemes": {"OperationBearer": {"type": "http", "scheme": "bearer"}}
    }
    receiver.description["paths"]["/items/{id}"]["get"]["security"] = [{"OperationBearer": []}]
    endpoint = "https://business.test/items/original%2Fvalue"
    security_value = {
        "profiles": {
            "operation": {
                "type": "bearer",
                "token": {"from_env": "OPENAPI_TEST_TOKEN"},
            }
        }
    }
    services = runtime(
        tmp_path,
        backend,
        engine_name,
        options={
            "security": security_value,
            "protocols": {
                "security_profile": "operation",
                "openapi_credential_endpoints": {"operation": (endpoint,)},
            },
        },
    )
    definition, fetched = imported_fixture(services, receiver, monkeypatch)
    client = services.protocols.http
    services.protocols = ProtocolServices(
        client,
        security=services.config.security,
        security_profile="operation",
        openapi_credential_endpoints={"operation": (endpoint,)},
    )
    services.tools.protocols = services.protocols
    try:
        await services.external_catalogs.resolve_workflow(definition, services.catalog)
        plan = compile_workflow(definition, trusted_catalogs=services.external_catalogs.policies)
        await engine.initialize(services)
        invocation = handle(services, engine_name, plan)
        result = await asyncio.wait_for(
            engine.invoke(plan, invocation, {"id": "original/value", "untouched": None}), 15
        )
        assert result.status == "completed" and result.output == {"id": "original/value"}, (
            result.error
        )
        assert len(fetched) == 1
        assert [str(request.url) for request in receiver.calls if str(request.url) != DOCUMENT] == [
            "https://business.test/items/original%2Fvalue"
        ]
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_archived_import_and_operation_replay_without_catalog_or_description_refetch(
    tmp_path, monkeypatch, engine_name, engine_type, backend
):
    engine = engine_type()
    actual_native(engine, engine_name)
    receiver = Receiver()
    services = runtime(tmp_path, backend, engine_name)
    definition, fetched = imported_fixture(services, receiver, monkeypatch)
    plan = await resolve_and_compile_workflow(
        definition,
        resolver=services.external_catalogs,
        catalog=services.catalog,
        trusted_catalogs=services.external_catalogs.policies,
    )
    assert len(plan.imports) == len(fetched) == 1
    await engine.initialize(services)
    invocation = handle(services, engine_name, plan)
    entered = asyncio.Event()
    original_call = engine.executor._call

    async def after_receipt(call, payload, state):
        output = await original_call(call, payload, state)
        entered.set()
        await asyncio.Event().wait()
        return output

    engine.executor._call = after_receipt
    running = asyncio.create_task(engine.invoke(plan, invocation, {"id": "original/value"}))
    try:
        await asyncio.wait_for(entered.wait(), 15)
        business = identities(services, invocation.invocation_id)
        policies = identities(services, invocation.invocation_id, OPERATION_POLICY)
        assert len(business) == len(policies) == 1 and len(receiver.calls) == 2
        proofs = [
            ReceiptDependency.capture(identity, services.effect_journal.get(identity))
            for identity in (*business, *policies)
        ]
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(running, 15)
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()
        services.close()
    receiver.deleted = True
    recovered = runtime(tmp_path, backend, engine_name)
    _, recovered_fetches = imported_fixture(recovered, receiver, monkeypatch)
    resumed = engine_type()
    try:
        await resumed.initialize(recovered)
        loaded = recovered.invocations.get(invocation.invocation_id)
        archived = recovered.resolve_original_definition(loaded)
        assert archived.imports == plan.imports and archived.fingerprint == plan.fingerprint
        result = await asyncio.wait_for(resumed.resume(loaded, {}, archived), 15)
        assert result.status == "completed" and result.output == {"id": "original/value"}, (
            result.error
        )
        assert recovered_fetches == [] and len(fetched) == 1 and len(receiver.calls) == 2
        assert recovered.invocations.read_input(loaded) == {"id": "original/value"}
        assert loaded.owner_principal == "original-owner" and recovered.model.calls == []
        for proof in proofs:
            proof.verify(recovered.effect_journal)
    finally:
        await resumed.shutdown()
        recovered.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_registered_import_uncertainty_stays_pending_and_bypasses_catch_retry(
    tmp_path, monkeypatch, engine_name, engine_type, backend
):
    engine = engine_type()
    actual_native(engine, engine_name)
    receiver = Receiver("uncertain")
    services = runtime(tmp_path, backend, engine_name)
    definition, fetched = imported_fixture(services, receiver, monkeypatch)
    await services.external_catalogs.resolve_workflow(definition, services.catalog)
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
    plan = compile_workflow(definition, trusted_catalogs=services.external_catalogs.policies)
    try:
        await engine.initialize(services)
        invocation = handle(services, engine_name, plan)
        result = await asyncio.wait_for(
            engine.invoke(plan, invocation, {"id": "original/value"}), 15
        )
        assert result.status == "faulted" and len(receiver.calls) == 2 and len(fetched) == 1
        assert result.error["code"] == "workflow_execution_error"
        assert result.error["details"]["reason"] == "transport_or_response_failure"
        assert "private-remote-detail" not in json.dumps(result.error)
        business = identities(services, invocation.invocation_id)
        policies = identities(services, invocation.invocation_id, OPERATION_POLICY)
        assert len(business) == len(policies) == 1
        assert services.effect_journal.get(business[0]).status == "pending"
        ReceiptDependency.capture(policies[0], services.effect_journal.get(policies[0])).verify(
            services.effect_journal
        )
        assert services.model.calls == [] and services.invocations.read_input(invocation) == {
            "id": "original/value"
        }
        assert await engine.resume(invocation, {}, plan) == result and len(receiver.calls) == 2
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_native_operation_fetches_description_and_dispatches_exact_admitted_route(
    tmp_path, engine_name, engine_type, backend
):
    engine = engine_type()
    actual_native(engine, engine_name)
    receiver = Receiver()
    services = runtime(tmp_path, backend, engine_name)
    receiver.install(services)
    try:
        await engine.initialize(services)
        plan = compile_workflow(source())
        invocation = handle(services, engine_name, plan)
        result = await asyncio.wait_for(
            engine.invoke(await engine.compile(plan), invocation, {"original": None}), 15
        )
        assert result.status == "completed" and result.output == {"id": "original/value"}, (
            result.error
        )
        assert [(request.method, str(request.url)) for request in receiver.calls] == [
            ("GET", DOCUMENT),
            ("GET", SERVER + "/items/original%2Fvalue"),
        ]
        business = identities(services, invocation.invocation_id)
        policy = identities(services, invocation.invocation_id, OPERATION_POLICY)
        assert len(business) == len(policy) == 1 and business[0].task_reference == "/do/0/peer"
        receipt = services.effect_journal.get(business[0])
        assert receipt.status == "completed" and receipt.output == result.output
        assert receipt.operation_id == receiver.calls[1].headers["Idempotency-Key"]
        ReceiptDependency.capture(policy[0], services.effect_journal.get(policy[0])).verify(
            services.effect_journal
        )
        assert (
            services.invocations.read_input(invocation) == {"original": None}
            and services.model.calls == []
        )
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_native_configured_tool_uses_immutable_deployment_operation_without_metadata_fetch(
    tmp_path, engine_name, engine_type, backend
):
    engine = engine_type()
    actual_native(engine, engine_name)
    receiver = Receiver()
    description = OpenApiDescription.capture(DOCUMENT, receiver.description)
    model = FakeModel(
        [
            {"tool_call": {"name": "peer", "arguments": {"parameters": {"id": "original/value"}}}},
            {"response": "tool-result-used"},
        ]
    )
    services = runtime(
        tmp_path,
        backend,
        engine_name,
        model=model,
        options={
            "tools": [
                {
                    "type": "openapi",
                    "name": "peer",
                    "options": {"operationId": "getItem"},
                    "openapi_binding": {
                        "id": "original-peer",
                        "version": "1",
                        "document_uri": description.uri,
                        "document": receiver.description,
                        "digest": description.digest,
                    },
                }
            ]
        },
    )
    receiver.install(services)
    receiver.deleted = True
    try:
        await engine.initialize(services)
        assert "peer" in {getattr(tool, "name", "") for tool in engine.agent.tools}
        plan = compile_workflow()
        invocation = handle(services, engine_name, plan)
        result = await asyncio.wait_for(
            engine.invoke(await engine.compile(plan), invocation, {}), 15
        )
        assert result.status == "completed" and result.output == {"response": "tool-result-used"}, (
            result.error
        )
        assert len(model.calls) == 2
        assert [(request.method, str(request.url)) for request in receiver.calls] == [
            ("GET", SERVER + "/items/original%2Fvalue")
        ]
        assert (
            services.tools.tools["peer"].openapi_selection.description.digest == description.digest
        )
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("outcome", ["value", "null", "fault"])
@pytest.mark.parametrize("metadata", ["changed", "deleted"])
async def test_known_operation_receipt_and_original_selection_survive_reopen_without_any_refetch(
    tmp_path, engine_name, engine_type, backend, outcome, metadata
):
    engine = engine_type()
    actual_native(engine, engine_name)
    receiver = Receiver(outcome)
    services = runtime(tmp_path, backend, engine_name)
    receiver.install(services)
    await engine.initialize(services)
    plan = compile_workflow(source())
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
    running = asyncio.create_task(
        engine.invoke(await engine.compile(plan), invocation, {"original": None})
    )
    try:
        await asyncio.wait_for(entered.wait(), 15)
        business = identities(services, invocation.invocation_id)
        policy = identities(services, invocation.invocation_id, OPERATION_POLICY)
        assert len(business) == len(policy) == 1 and len(receiver.calls) == 2
        known = services.effect_journal.get(business[0])
        assert known.status == ("faulted" if outcome == "fault" else "completed")
        proofs = {
            identity.key: ReceiptDependency.capture(
                identity, services.effect_journal.get(identity)
            ).receipt_digest
            for identity in (*business, *policy)
        }
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(running, 15)
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()
        services.close()
    receiver.deleted = metadata == "deleted"
    receiver.description = operation_document(
        "https://changed.test",
        operation_id="getItem",
        path="/different",
        parameters=(("id", "query", "string"),),
    )
    recovered = runtime(tmp_path, backend, engine_name)
    receiver.install(recovered)
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
            assert result.output == (None if outcome == "null" else {"id": "original/value"})
        else:
            assert result.error["code"] == known.error["code"]
            assert result.error["message"] == known.error["message"]
            assert result.error["details"] == {**known.error["details"], "instance": "/do/0/peer"}
        assert len(receiver.calls) == 2 and recovered.model.calls == []
        assert recovered.invocations.read_input(loaded) == {"original": None}
        assert (
            loaded.owner_principal,
            loaded.user_id,
            loaded.session_id,
            loaded.workflow_fingerprint,
        ) == ("original-owner", "original-user", "original-session", plan.fingerprint)
        for identity in (*business, *policy):
            assert (
                ReceiptDependency.capture(
                    identity, recovered.effect_journal.get(identity)
                ).receipt_digest
                == proofs[identity.key]
            )
    finally:
        await resumed.shutdown()
        recovered.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_uncertain_dispatched_operation_stays_pending_without_catch_or_retry_replay(
    tmp_path, engine_name, engine_type, backend
):
    engine = engine_type()
    actual_native(engine, engine_name)
    receiver = Receiver("uncertain")
    services = runtime(tmp_path, backend, engine_name)
    receiver.install(services)
    try:
        await engine.initialize(services)
        definition = source()
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
        plan = compile_workflow(definition)
        invocation = handle(services, engine_name, plan)
        result = await asyncio.wait_for(engine.invoke(plan, invocation, {}), 15)
        assert result.status == "faulted" and len(receiver.calls) == 2
        assert result.error["code"] == "workflow_execution_error"
        assert result.error["details"]["reason"] == "transport_or_response_failure"
        assert "private-remote-detail" not in json.dumps(result.error)
        business = identities(services, invocation.invocation_id)
        assert len(business) == 1 and services.effect_journal.get(business[0]).status == "pending"
        assert services.model.calls == []
        assert await engine.resume(invocation, {}, plan) == result and len(receiver.calls) == 2
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_cancellation_after_openapi_dispatch_keeps_pending_intent_without_replay(
    tmp_path, engine_name, engine_type, backend
):
    engine = engine_type()
    actual_native(engine, engine_name)
    receiver = Receiver("blocked")
    services = runtime(tmp_path, backend, engine_name)
    receiver.install(services)
    engine_closed = False
    services_closed = False
    try:
        await engine.initialize(services)
        plan = compile_workflow(source())
        invocation = handle(services, engine_name, plan)
        running = asyncio.create_task(engine.invoke(plan, invocation, {}))
        await asyncio.wait_for(receiver.dispatch_entered.wait(), 15)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(running, 15)
        business = identities(services, invocation.invocation_id)
        policies = identities(services, invocation.invocation_id, OPERATION_POLICY)
        assert len(business) == len(policies) == 1
        pending = services.effect_journal.get(business[0])
        assert pending is not None and pending.status == "pending"
        assert len(receiver.calls) == 2  # one discovery GET and one dispatched business call
        await engine.shutdown()
        engine_closed = True
        services.close()
        services_closed = True

        receiver.outcome = "value"
        recovered = runtime(tmp_path, backend, engine_name)
        receiver.install(recovered)
        resumed = engine_type()
        try:
            await resumed.initialize(recovered)
            loaded = recovered.invocations.get(invocation.invocation_id)
            result = await asyncio.wait_for(resumed.resume(loaded, {}, plan), 15)
            assert result.status == "faulted"
            assert (
                len(receiver.calls) == 2
            )  # neither mutable metadata nor business was fetched again
            assert recovered.effect_journal.get(business[0]).status == "pending"
            assert recovered.model.calls == []
        finally:
            await resumed.shutdown()
            recovered.close()
    finally:
        receiver.dispatch_release.set()
        if not engine_closed:
            await engine.shutdown()
        if not services_closed:
            services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_loop_visits_have_distinct_operation_and_policy_keys_after_native_restart(
    tmp_path, engine_name, engine_type, backend
):
    engine = engine_type()
    actual_native(engine, engine_name)
    receiver = Receiver()
    services = runtime(tmp_path, backend, engine_name)
    receiver.install(services)
    await engine.initialize(services)
    definition = source()
    action = definition["do"][0]["peer"]
    action["with"]["parameters"]["id"] = "${ $number | tostring }"
    definition["do"] = [
        {"repeat": {"for": {"each": "number", "in": [10, 20]}, "do": [{"peer": action}]}}
    ]
    plan = compile_workflow(definition)
    invocation = handle(services, engine_name, plan)
    entered = asyncio.Event()
    original = engine.executor._call

    async def after_second(call, payload, state):
        output = await original(call, payload, state)
        if len([request for request in receiver.calls if str(request.url) != DOCUMENT]) == 2:
            entered.set()
            await asyncio.Event().wait()
        return output

    engine.executor._call = after_second
    running = asyncio.create_task(engine.invoke(plan, invocation, {"original": None}))
    try:
        await asyncio.wait_for(entered.wait(), 15)
        requests = [request for request in receiver.calls if str(request.url) != DOCUMENT]
        assert [request.url.path for request in requests] == ["/items/10", "/items/20"]
        assert len({request.headers["Idempotency-Key"] for request in requests}) == 2
        business = identities(services, invocation.invocation_id)
        policy = identities(services, invocation.invocation_id, OPERATION_POLICY)
        assert len(business) == len(policy) == 2
        proofs = {
            identity.key: ReceiptDependency.capture(
                identity, services.effect_journal.get(identity)
            ).receipt_digest
            for identity in (*business, *policy)
        }
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(running, 15)
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()
        services.close()
    receiver.deleted = True
    recovered = runtime(tmp_path, backend, engine_name)
    receiver.install(recovered)
    resumed = engine_type()
    try:
        await resumed.initialize(recovered)
        loaded = recovered.invocations.get(invocation.invocation_id)
        result = await asyncio.wait_for(resumed.resume(loaded, {}, plan), 15)
        assert result.status == "completed" and len(receiver.calls) == 4
        for identity in (*business, *policy):
            assert (
                ReceiptDependency.capture(
                    identity, recovered.effect_journal.get(identity)
                ).receipt_digest
                == proofs[identity.key]
            )
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
@pytest.mark.parametrize("expired", [False, True])
async def test_original_task_deadline_governs_operation_after_native_reopen(
    tmp_path, monkeypatch, engine_name, engine_type, backend, expired
):
    clock = [1000.0]
    _use_clock(monkeypatch, lambda: clock[0])
    engine = engine_type()
    actual_native(engine, engine_name)
    receiver = Receiver()
    services = runtime(tmp_path, backend, engine_name)
    receiver.install(services)
    await engine.initialize(services)
    definition = source()
    definition["do"] = [
        {
            "frame": {
                "timeout": {"after": {"seconds": 30}},
                "do": [{"pause": {"wait": {"seconds": 10}}}, *definition["do"]],
            }
        }
    ]
    plan = compile_workflow(definition)
    invocation = handle(services, engine_name, plan)
    try:
        result = await asyncio.wait_for(engine.invoke(plan, invocation, {"original": None}), 15)
        assert result.status == "waiting" and receiver.calls == []
        timing = _policy_records(services, invocation.invocation_id)
        assert len(timing) == 2
    finally:
        await engine.shutdown()
        services.close()
    clock[0] = 1031.0 if expired else 1010.0
    recovered = runtime(tmp_path, backend, engine_name)
    receiver.install(recovered)
    resumed = engine_type()
    try:
        await resumed.initialize(recovered)
        loaded = recovered.invocations.get(invocation.invocation_id)
        result = await asyncio.wait_for(resumed.resume(loaded, {"changed": True}, plan), 15)
        if expired:
            assert result.status == "faulted" and result.error["details"]["status"] == 408
            assert receiver.calls == [] and identities(recovered, loaded.invocation_id) == []
        else:
            assert result.status == "completed" and len(receiver.calls) == 2
            policy = identities(recovered, loaded.invocation_id, OPERATION_POLICY)
            assert len(policy) == 1
            record = recovered.effect_journal.get(policy[0]).output
            assert record["deadline_at"] == record["task_deadline_at"] == 1030.0
        assert _policy_records(recovered, invocation.invocation_id) == timing
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
@pytest.mark.parametrize("damage", ["decision", "receipt", "index", "selection"])
async def test_native_checkpoint_cannot_skip_missing_or_forged_operation_policy(
    tmp_path, monkeypatch, engine_name, engine_type, backend, damage
):
    clock = [1000.0]
    _use_clock(monkeypatch, lambda: clock[0])
    engine = engine_type()
    actual_native(engine, engine_name)
    receiver = Receiver()
    services = runtime(tmp_path, backend, engine_name)
    receiver.install(services)
    await engine.initialize(services)
    definition = source()
    definition["do"].append({"hold": {"wait": {"seconds": 10}}})
    plan = compile_workflow(definition)
    invocation = handle(services, engine_name, plan)
    try:
        result = await asyncio.wait_for(engine.invoke(plan, invocation, {"original": None}), 15)
        assert result.status == "waiting" and len(receiver.calls) == 2
        business = identities(services, invocation.invocation_id)
        policy = identities(services, invocation.invocation_id, OPERATION_POLICY)
        assert len(business) == len(policy) == 1
        assert any(native_snapshot(services, invocation, backend).values())
    finally:
        await engine.shutdown()
        services.close()
    recovered = runtime(tmp_path, backend, engine_name)
    receiver.install(recovered)
    resumed = engine_type()
    clock[0] = 1010.0
    try:
        await resumed.initialize(recovered)
        loaded = recovered.invocations.get(invocation.invocation_id)
        connection = recovered.effect_journal.connection
        if damage == "decision":
            connection.execute(
                "DELETE FROM openapi_operation_admissions WHERE business_key=?", (business[0].key,)
            )
        elif damage == "receipt":
            connection.execute("DELETE FROM workflow_effects WHERE identity=?", (policy[0].key,))
        elif damage == "index":
            connection.execute(
                "DELETE FROM workflow_effect_invocations WHERE identity=?", (policy[0].key,)
            )
        else:
            known = recovered.effect_journal.get(policy[0])
            value = copy.deepcopy(known.output)
            value["selection"]["endpoint"] = "https://forged.test/items/{id}"
            changed = replace(
                known, output=value, request_digest=effect_request_digest(OPERATION_POLICY, value)
            )
            connection.execute(
                "UPDATE openapi_operation_admissions SET payload=?,digest=? WHERE business_key=?",
                (json.dumps(value), changed.request_digest, business[0].key),
            )
            record = json.loads(
                connection.execute(
                    "SELECT payload FROM workflow_effects WHERE identity=?", (policy[0].key,)
                ).fetchone()[0]
            )
            record.update(output=value, request_digest=changed.request_digest)
            connection.execute(
                "UPDATE workflow_effects SET payload=? WHERE identity=?",
                (json.dumps(record), policy[0].key),
            )
        connection.commit()
        native_before = native_snapshot(recovered, loaded, backend)
        invocation_before = connection.execute(
            "SELECT payload,status FROM invocations WHERE invocation_id=?", (loaded.invocation_id,)
        ).fetchone()
        intent_before = recovered.effect_journal.get(business[0])
        with pytest.raises(OwaError):
            await asyncio.wait_for(resumed.resume(loaded, {}, plan), 15)
        assert native_snapshot(recovered, loaded, backend) == native_before
        assert (
            connection.execute(
                "SELECT payload,status FROM invocations WHERE invocation_id=?",
                (loaded.invocation_id,),
            ).fetchone()
            == invocation_before
        )
        assert recovered.effect_journal.get(business[0]) == intent_before
        assert len(receiver.calls) == 2 and recovered.model.calls == []
        assert recovered.invocations.read_input(loaded) == {"original": None}
    finally:
        await resumed.shutdown()
        recovered.close()
