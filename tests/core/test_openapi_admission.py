"""OpenAPI operation decisions retain leased original source and recovery authority."""

from __future__ import annotations

import asyncio
import copy
import json
import os
from dataclasses import replace

import httpx
import pytest
from open_workflow_agent import openapi_admission
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.effects import EffectIdentity, ReceiptDependency, effect_request_digest
from open_workflow_agent.errors import (
    InvocationStateConflict,
    InvocationStateError,
    ToolError,
    WorkflowExecutionError,
)
from open_workflow_agent.openapi_admission import OPERATION_POLICY, OpenApiOperationAdmission
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.security import ProfileAuthentication, SecurityConfig
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow

DOCUMENT_URI = "https://metadata.test/openapi.json"
TARGET = "https://business.test/items/{itemId}"


def _description(endpoint):
    return {
        "openapi": "3.0.3",
        "info": {"title": "Original operation", "version": "1.0.0"},
        "servers": [{"url": endpoint.split("/items/")[0]}],
        "paths": {
            "/items/{itemId}": {
                "get": {
                    "operationId": "readItem",
                    "parameters": [
                        {
                            "name": "itemId",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {"200": {"description": "Known value"}},
                }
            }
        },
    }


def _source():
    return {
        "document": {
            "dsl": "1.0.3",
            "namespace": "openapi",
            "name": "admission",
            "version": "1.0.0",
        },
        "do": [
            {
                "action": {
                    "call": "openapi",
                    "with": {
                        "document": {"endpoint": DOCUMENT_URI},
                        "operationId": "readItem",
                        "parameters": {"itemId": "original/value"},
                    },
                }
            }
        ],
    }


@pytest.fixture(params=["sqlite", "postgresql"])
def admitted_parent(tmp_path, request):
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if request.param == "postgresql" else None
    if request.param == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    services = RuntimeServices(
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}}),
        model=FakeModel(),
        database_root=None if datasource else tmp_path,
    )
    # Isolated common-store seam: this suite never claims native SDK/profile
    # qualification. Actual producer/native evidence has independent fixtures.
    services.recovery_definitions.profile_provider = lambda engine: {
        "profile": "openapi-store-test",
        "engine": engine,
    }
    plan = compile_workflow(_source())
    handle = services.invocations.create(
        engine="portable",
        session_id="original-session",
        user_id="correlation-user",
        owner_principal="original-owner",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    lease = services.execution_ownership.acquire(handle, seconds=300)
    handle._execution_lease = lease
    services.recovery_definitions.admit(plan, handle, {"original": None}, execution_lease=lease)
    identity = EffectIdentity(
        handle.invocation_id,
        plan.fingerprint,
        "/do/0/action",
        (("/do/0/action", 1),),
        "call:openapi",
    )
    payload = copy.deepcopy(_source()["do"][0]["action"]["with"])
    calls = []
    state = {"document": _description(TARGET), "clock": 1000.0}

    async def receiver(request):
        calls.append(request)
        assert request.method == "GET" and str(request.url) == DOCUMENT_URI
        assert "authorization" not in request.headers
        return httpx.Response(200, json=state["document"])

    services.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(receiver)))
    services.openapi_operation_admission = OpenApiOperationAdmission(services)
    try:
        yield services, handle, identity, payload, calls, state
    finally:
        if handle._execution_lease is not None:
            try:
                services.execution_ownership.release(handle._execution_lease)
            except InvocationStateConflict:
                pass
        services.close()


async def _prepare(tree, **overrides):
    services, handle, identity, payload, _, state = tree
    return await services.openapi_operation_admission.prepare(
        identity,
        payload,
        handle,
        execution_lease=handle._execution_lease,
        deadline_at=overrides.pop("deadline_at", 1040.0),
        now=lambda: state["clock"],
        **overrides,
    )


def _rows(services, identity):
    connection = services.effect_journal.connection
    policy = replace(identity, operation=OPERATION_POLICY)
    return tuple(
        connection.execute(sql, args).fetchall()
        for sql, args in (
            ("SELECT * FROM openapi_operation_admissions WHERE business_key=?", (identity.key,)),
            ("SELECT * FROM workflow_effects WHERE identity=?", (policy.key,)),
            ("SELECT * FROM workflow_effect_invocations WHERE identity=?", (policy.key,)),
        )
    )


@pytest.mark.asyncio
async def test_operation_decision_is_immutable_and_preserves_original_budget_without_refetch(
    admitted_parent,
):
    services, handle, identity, _, calls, state = admitted_parent
    original = await _prepare(admitted_parent)
    assert original.selection.endpoint == TARGET and original.selection.endpoint != DOCUMENT_URI
    assert original.receipt.output["deadline_at"] == 1030.0
    dependency = ReceiptDependency.capture(original.identity, original.receipt)
    before = _rows(services, identity)
    state["document"] = _description("https://changed.test/items/{itemId}")
    state["clock"] = 1007.0
    recovered = await _prepare(admitted_parent)
    assert recovered.selection.endpoint == TARGET and recovered.selection.remaining_timeout == 23.0
    assert len(calls) == 1 and _rows(services, identity) == before
    dependency.verify(services.effect_journal)
    assert services.invocations.read_input(handle) == {"original": None}
    assert handle.owner_principal == "original-owner" and handle.session_id == "original-session"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage",
    [
        "decision",
        "receipt",
        "index",
        "selection",
        "deadline",
        "input",
        "definition",
        "identity-index",
    ],
)
async def test_missing_or_changed_operation_policy_dependency_fails_without_new_fetch(
    admitted_parent, damage
):
    services, handle, identity, _, calls, _ = admitted_parent
    decision = await _prepare(admitted_parent)
    dependency = ReceiptDependency.capture(decision.identity, decision.receipt)
    connection = services.effect_journal.connection
    if damage == "decision":
        connection.execute(
            "DELETE FROM openapi_operation_admissions WHERE business_key=?", (identity.key,)
        )
    elif damage == "receipt":
        connection.execute(
            "DELETE FROM workflow_effects WHERE identity=?", (decision.identity.key,)
        )
    elif damage == "index":
        connection.execute(
            "DELETE FROM workflow_effect_invocations WHERE identity=?", (decision.identity.key,)
        )
    elif damage == "input":
        connection.execute(
            "UPDATE invocation_inputs SET payload=? WHERE invocation_id=?",
            (json.dumps({"changed": True}), handle.invocation_id),
        )
    elif damage == "definition":
        connection.execute(
            "DELETE FROM recovery_invocation_definitions WHERE invocation_id=?",
            (handle.invocation_id,),
        )
    elif damage == "identity-index":
        connection.execute(
            "UPDATE workflow_effect_invocations SET fingerprint=? WHERE identity=?",
            ("other-definition", decision.identity.key),
        )
    else:
        value = copy.deepcopy(decision.receipt.output)
        if damage == "selection":
            value["selection"]["endpoint"] = "https://forged.test/items/{itemId}"
        else:
            value["deadline_at"] = 9999.0
        receipt = replace(
            decision.receipt,
            output=value,
            request_digest=effect_request_digest(OPERATION_POLICY, value),
        )
        connection.execute(
            "UPDATE openapi_operation_admissions SET payload=?,digest=? WHERE business_key=?",
            (json.dumps(value), receipt.request_digest, identity.key),
        )
        saved = json.loads(
            connection.execute(
                "SELECT payload FROM workflow_effects WHERE identity=?", (decision.identity.key,)
            ).fetchone()[0]
        )
        saved.update(output=value, request_digest=receipt.request_digest)
        connection.execute(
            "UPDATE workflow_effects SET payload=? WHERE identity=?",
            (json.dumps(saved), decision.identity.key),
        )
    connection.commit()
    before = _rows(services, identity)
    invocation_before = connection.execute(
        "SELECT payload,status FROM invocations WHERE invocation_id=?", (handle.invocation_id,)
    ).fetchone()
    ownership_before = connection.execute(
        "SELECT * FROM invocation_ownership WHERE invocation_id=?", (handle.invocation_id,)
    ).fetchone()
    input_before = connection.execute(
        "SELECT fingerprint,payload FROM invocation_inputs WHERE invocation_id=?",
        (handle.invocation_id,),
    ).fetchone()
    definition_before = connection.execute(
        "SELECT * FROM recovery_invocation_definitions WHERE invocation_id=?",
        (handle.invocation_id,),
    ).fetchone()
    with pytest.raises(WorkflowExecutionError):
        dependency.verify(services.effect_journal)
    if damage in {"input", "definition"}:
        with pytest.raises(InvocationStateError) as rejected:
            await _prepare(admitted_parent)
        assert rejected.value.details == {
            "reason": "input_changed" if damage == "input" else "binding_missing"
        }
    else:
        with pytest.raises(WorkflowExecutionError):
            await _prepare(admitted_parent)
    assert [request.method for request in calls] == ["GET"]
    assert _rows(services, identity) == before
    assert services.effect_journal.get(identity) is None and services.model.calls == []
    assert (
        connection.execute(
            "SELECT payload,status FROM invocations WHERE invocation_id=?", (handle.invocation_id,)
        ).fetchone()
        == invocation_before
    )
    assert (
        connection.execute(
            "SELECT * FROM invocation_ownership WHERE invocation_id=?", (handle.invocation_id,)
        ).fetchone()
        == ownership_before
    )
    assert (
        connection.execute(
            "SELECT fingerprint,payload FROM invocation_inputs WHERE invocation_id=?",
            (handle.invocation_id,),
        ).fetchone()
        == input_before
    )
    assert (
        connection.execute(
            "SELECT * FROM recovery_invocation_definitions WHERE invocation_id=?",
            (handle.invocation_id,),
        ).fetchone()
        == definition_before
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["payload", "deadline", "grant"])
async def test_changed_request_or_deployment_binding_cannot_replace_original_decision(
    admitted_parent, change
):
    services, _, identity, payload, calls, _ = admitted_parent
    await _prepare(admitted_parent)
    before = _rows(services, identity)
    if change == "payload":
        payload["parameters"]["itemId"] = "other-value"
    elif change == "grant":
        services.config.protocols.openapi_credential_endpoints["other-profile"] = (
            "https://other.test/items/value",
        )
    with pytest.raises(WorkflowExecutionError):
        await _prepare(admitted_parent, deadline_at=1050.0 if change == "deadline" else 1040.0)
    assert len(calls) == 1 and _rows(services, identity) == before


@pytest.mark.asyncio
async def test_existing_legacy_pending_intent_never_adopts_new_operation_admission(admitted_parent):
    services, handle, identity, payload, calls, _ = admitted_parent
    original = services.effect_journal.begin(
        identity,
        effect_request_digest("openapi", payload),
        "original-owner",
        execution_lease=handle._execution_lease,
    )
    with pytest.raises(WorkflowExecutionError, match="original admission"):
        await _prepare(admitted_parent)
    assert calls == [] and services.effect_journal.get(identity) == original
    assert _rows(services, identity) == ([], [], [])


@pytest.mark.asyncio
async def test_stale_execution_generation_rejects_before_metadata_io(admitted_parent):
    services, handle, identity, payload, calls, state = admitted_parent
    previous = handle._execution_lease
    services.execution_ownership.release(previous)
    handle._execution_lease = services.execution_ownership.acquire(handle, seconds=300)
    with pytest.raises(InvocationStateConflict):
        await services.openapi_operation_admission.prepare(
            identity,
            payload,
            handle,
            execution_lease=previous,
            deadline_at=1040.0,
            now=lambda: state["clock"],
        )
    assert calls == [] and _rows(services, identity) == ([], [], [])


@pytest.mark.asyncio
async def test_lost_ownership_after_fetch_cannot_publish_a_policy_receipt(admitted_parent):
    services, handle, identity, _, calls, state = admitted_parent

    async def receiver(request):
        calls.append(request)
        services.execution_ownership.release(handle._execution_lease)
        return httpx.Response(200, json=state["document"])

    services.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(receiver)))
    with pytest.raises(InvocationStateConflict):
        await _prepare(admitted_parent)
    assert len(calls) == 1 and _rows(services, identity) == ([], [], [])


@pytest.mark.asyncio
async def test_failed_policy_transaction_rolls_back_decision_receipt_and_index(
    admitted_parent, monkeypatch
):
    services, handle, identity, _, calls, _ = admitted_parent
    insert = openapi_admission.insert_completed_receipt

    def interrupted(connection, policy, receipt):
        insert(connection, policy, receipt)
        raise WorkflowExecutionError("injected transaction interruption")

    monkeypatch.setattr(openapi_admission, "insert_completed_receipt", interrupted)
    with pytest.raises(WorkflowExecutionError, match="interruption"):
        await _prepare(admitted_parent)
    assert len(calls) == 1 and _rows(services, identity) == ([], [], [])
    assert services.invocations.read_input(handle) == {"original": None}
    services.recovery_definitions.verify_admission(handle)


@pytest.mark.asyncio
async def test_expired_original_budget_after_fetch_never_admits_or_dispatches(admitted_parent):
    services, _, identity, _, calls, state = admitted_parent

    async def receiver(request):
        calls.append(request)
        state["clock"] = 1035.0
        return httpx.Response(200, json=state["document"])

    services.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(receiver)))
    with pytest.raises(ToolError, match="deadline expired"):
        await _prepare(admitted_parent)
    assert len(calls) == 1 and _rows(services, identity) == ([], [], [])


@pytest.mark.asyncio
async def test_slow_metadata_obeys_explicit_call_timeout_before_any_business_dispatch(
    admitted_parent,
):
    services, _, identity, payload, calls, state = admitted_parent
    payload["timeout"] = {"seconds": 1}
    cancelled = []

    async def receiver(request):
        calls.append(request)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(True)
            raise
        return httpx.Response(200, json=state["document"])

    services.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(receiver)))
    with pytest.raises(ToolError):
        await _prepare(admitted_parent)
    assert [request.method for request in calls] == ["GET"] and cancelled == [True]
    assert _rows(services, identity) == ([], [], [])


@pytest.mark.asyncio
async def test_explicit_call_timeout_remains_absolute_after_metadata_returns(admitted_parent):
    services, _, identity, payload, calls, state = admitted_parent
    payload["timeout"] = {"seconds": 1}

    async def receiver(request):
        calls.append(request)
        state["clock"] = 1000.75
        return httpx.Response(200, json=state["document"])

    services.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(receiver)))
    decision = await _prepare(admitted_parent)
    assert decision.receipt.output["deadline_at"] == 1001.0
    assert decision.selection.remaining_timeout == 0.25
    before = _rows(services, identity)
    state["clock"] = 1001.01
    with pytest.raises(ToolError, match="original deadline expired"):
        await _prepare(admitted_parent)
    assert len(calls) == 1 and _rows(services, identity) == before


@pytest.mark.asyncio
async def test_public_description_discovery_retains_only_named_auth_reference(
    admitted_parent, monkeypatch
):
    services, _, identity, _, calls, _ = admitted_parent
    monkeypatch.delenv("OPENAPI_ADMISSION_TOKEN", raising=False)
    security = SecurityConfig.model_validate(
        {
            "profiles": {
                "named": {
                    "type": "bearer",
                    "token": {"from_env": "OPENAPI_ADMISSION_TOKEN"},
                }
            }
        }
    )
    auth = ProfileAuthentication(security, "named")
    services.protocols.http.authentication = auth
    actual_endpoint = "https://business.test/items/original%2Fvalue"
    services.config.protocols.openapi_credential_endpoints["named"] = (actual_endpoint,)
    services.protocols._openapi_credential_endpoints["named"] = (actual_endpoint,)
    decision = await _prepare(admitted_parent)
    assert decision.selection.credential_profile == "named"
    assert decision.receipt.output["transport_binding"]["authentication"]["profile"]["token"] == {
        "from_env": "OPENAPI_ADMISSION_TOKEN"
    }
    assert len(calls) == 1 and "authorization" not in calls[0].headers
    ReceiptDependency.capture(decision.identity, decision.receipt).verify(services.effect_journal)
    assert len(_rows(services, identity)[0]) == 1


@pytest.mark.asyncio
async def test_changed_applied_credential_grant_cannot_reuse_saved_operation(admitted_parent):
    services, _, identity, _, calls, _ = admitted_parent
    await _prepare(admitted_parent)
    before = _rows(services, identity)
    services.protocols._openapi_credential_endpoints["forged"] = (TARGET,)
    with pytest.raises(WorkflowExecutionError, match="applied credential authority changed"):
        await _prepare(admitted_parent)
    assert len(calls) == 1 and _rows(services, identity) == before


@pytest.mark.asyncio
async def test_applied_ca_must_match_its_named_reference_before_metadata_io(
    admitted_parent, monkeypatch
):
    services, _, identity, _, calls, _ = admitted_parent
    monkeypatch.setenv("OPENAPI_ADMISSION_CA", "declared-ca.pem")
    security = SecurityConfig.model_validate(
        {
            "profiles": {
                "named": {
                    "type": "mtls",
                    "certificate": {"from_env": "OPENAPI_ADMISSION_CERT"},
                    "private_key": {"from_env": "OPENAPI_ADMISSION_KEY"},
                    "ca_bundle": {"from_env": "OPENAPI_ADMISSION_CA"},
                }
            }
        }
    )
    services.protocols.http.authentication = ProfileAuthentication(security, "named")
    services.protocols.http.verify_tls = "other-ca.pem"
    with pytest.raises(ToolError, match="applied CA differs"):
        await _prepare(admitted_parent)
    assert calls == [] and _rows(services, identity) == ([], [], [])


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["value", "null", "fault"])
async def test_known_business_receipt_replays_original_operation_after_deadline_without_refetch(
    admitted_parent, outcome
):
    services, handle, identity, payload, calls, state = admitted_parent
    decision = await _prepare(admitted_parent)
    services.effect_journal.begin(
        identity,
        effect_request_digest("openapi", payload),
        "original-effect-owner",
        execution_lease=handle._execution_lease,
    )
    known = services.effect_journal.finish(
        identity,
        "original-effect-owner",
        output={"id": "task-id"} if outcome == "value" else None,
        error={"code": "tool_error", "message": "known peer fault", "details": {}}
        if outcome == "fault"
        else None,
    )
    before = _rows(services, identity)
    state["clock"] = 1045.0
    recovered = await _prepare(admitted_parent, allow_fetch=False)
    assert recovered.receipt == decision.receipt and recovered.selection.remaining_timeout == -15.0
    assert len(calls) == 1 and _rows(services, identity) == before
    assert services.effect_journal.get(identity) == known
    ReceiptDependency.capture(recovered.identity, recovered.receipt).verify(services.effect_journal)


@pytest.mark.asyncio
async def test_another_invocations_live_lease_cannot_authorize_metadata_io(admitted_parent):
    services, handle, identity, payload, calls, state = admitted_parent
    other = services.invocations.create(
        engine=handle.engine,
        session_id="other-session",
        user_id=handle.user_id,
        owner_principal=handle.owner_principal,
        workflow_name=handle.workflow_name,
        workflow_version=handle.workflow_version,
        workflow_fingerprint=handle.workflow_fingerprint,
    )
    lease = services.execution_ownership.acquire(other, seconds=300)
    try:
        with pytest.raises(InvocationStateConflict, match="authority is inconsistent"):
            await services.openapi_operation_admission.prepare(
                identity,
                payload,
                handle,
                execution_lease=lease,
                deadline_at=1040.0,
                now=lambda: state["clock"],
            )
        assert calls == [] and _rows(services, identity) == ([], [], [])
    finally:
        services.execution_ownership.release(lease)


@pytest.mark.asyncio
async def test_changed_parent_identity_rejects_before_metadata_io(admitted_parent):
    services, handle, identity, _, calls, _ = admitted_parent
    handle.owner_principal = "other-principal"
    with pytest.raises(InvocationStateConflict, match="identity changed"):
        await _prepare(admitted_parent)
    assert calls == [] and _rows(services, identity) == ([], [], [])


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", [None, [], ""])
async def test_invalid_operation_identifier_has_no_discovery_or_decision(
    admitted_parent, operation
):
    services, _, identity, payload, calls, _ = admitted_parent
    payload["operationId"] = operation
    with pytest.raises(ToolError, match="operationId"):
        await _prepare(admitted_parent)
    assert calls == [] and _rows(services, identity) == ([], [], [])


@pytest.mark.asyncio
@pytest.mark.parametrize("unsupported", ["request-body", "external-reference"])
async def test_unqualified_description_has_only_metadata_io_and_no_decision(
    admitted_parent, unsupported
):
    services, _, identity, _, calls, state = admitted_parent
    operation = state["document"]["paths"]["/items/{itemId}"]["get"]
    if unsupported == "request-body":
        operation["requestBody"] = {"content": {"application/json": {"schema": {"type": "object"}}}}
    else:
        operation["parameters"][0]["schema"] = {"$ref": "https://external.test/schema.json"}
    with pytest.raises(ToolError):
        await _prepare(admitted_parent)
    assert [request.method for request in calls] == ["GET"]
    assert _rows(services, identity) == ([], [], [])


@pytest.mark.asyncio
async def test_reopened_services_use_original_description_without_metadata_redelivery(
    admitted_parent,
):
    services, handle, identity, _, calls, state = admitted_parent
    original = await _prepare(admitted_parent)
    before = _rows(services, identity)
    datasource = services.config.persistence.datasource
    reopened = RuntimeServices(
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}}),
        model=FakeModel(),
        database_root=services.database_root if not datasource else None,
    )
    assert reopened.invocations.connection.kind == services.invocations.connection.kind
    reopened.recovery_definitions.profile_provider = services.recovery_definitions.profile_provider

    async def forbidden(request):
        raise AssertionError("recovery cannot refetch or dispatch")

    reopened.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(forbidden)))
    admission = OpenApiOperationAdmission(reopened)
    services.execution_ownership.release(handle._execution_lease)
    handle._execution_lease = None
    recovered = reopened.invocations.get(handle.invocation_id)
    recovered._execution_lease = reopened.execution_ownership.acquire(recovered, seconds=300)
    try:
        state["clock"] = 1007.0
        payload = copy.deepcopy(_source()["do"][0]["action"]["with"])
        value = await admission.prepare(
            identity,
            payload,
            recovered,
            execution_lease=recovered._execution_lease,
            deadline_at=1040.0,
            now=lambda: state["clock"],
            allow_fetch=False,
        )
        assert value.receipt == original.receipt and value.selection.remaining_timeout == 23.0
        assert value.selection.description.digest == original.selection.description.digest
        assert _rows(reopened, identity) == before and len(calls) == 1
        assert reopened.invocations.read_input(recovered) == {"original": None}
    finally:
        reopened.execution_ownership.release(recovered._execution_lease)
        recovered._execution_lease = None
        reopened.close()


@pytest.mark.asyncio
async def test_oas_32_decision_retains_original_version_on_recovery(admitted_parent):
    services, _, identity, _, calls, state = admitted_parent
    state["document"]["openapi"] = "3.2.0"
    original = await _prepare(admitted_parent)
    before = _rows(services, identity)
    state["document"]["openapi"] = "3.0.3"
    recovered = await _prepare(admitted_parent, allow_fetch=False)
    assert original.selection.version == recovered.selection.version == "3.2.0"
    assert recovered.receipt == original.receipt and len(calls) == 1
    assert _rows(services, identity) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("record", ["decision", "receipt", "identity-index"])
@pytest.mark.parametrize("damage", ["duplicate-fields", "oversized"])
async def test_ambiguous_or_oversized_saved_json_rejects_without_mutation_or_refetch(
    admitted_parent, record, damage
):
    services, _, identity, _, calls, _ = admitted_parent
    original = await _prepare(admitted_parent)
    table, column, key, match = {
        "decision": ("openapi_operation_admissions", "payload", "business_key", identity.key),
        "receipt": ("workflow_effects", "payload", "identity", original.identity.key),
        "identity-index": (
            "workflow_effect_invocations",
            "payload",
            "identity",
            original.identity.key,
        ),
    }[record]
    connection = services.effect_journal.connection
    encoded = connection.execute(
        f"SELECT {column} FROM {table} WHERE {key}=?", (match,)
    ).fetchone()[0]
    if damage == "oversized":
        corrupted = " " * (openapi_admission._MAX_RECORD_BYTES + 1) + encoded
    else:
        name = {"decision": "profile", "receipt": "owner", "identity-index": "invocation_id"}[
            record
        ]
        corrupted = "{" + json.dumps(name) + ':"foreign",' + encoded.lstrip()[1:]
    connection.execute(f"UPDATE {table} SET {column}=? WHERE {key}=?", (corrupted, match))
    connection.commit()
    before = _rows(services, identity)
    with pytest.raises(WorkflowExecutionError):
        ReceiptDependency.capture(original.identity, original.receipt).verify(
            services.effect_journal
        )
    with pytest.raises(WorkflowExecutionError):
        await _prepare(admitted_parent)
    assert len(calls) == 1 and _rows(services, identity) == before
