"""Mutable card discovery cannot replace an admitted workflow action target."""

from __future__ import annotations

import asyncio
import copy
import json
import os
from dataclasses import replace

import httpx
import pytest
from _a2a_sdk_fixture import peer_card, sdk_a2a_fixture
from google.protobuf import json_format
from open_workflow_agent import a2a_admission
from open_workflow_agent.a2a_admission import CARD_OPERATION
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.effects import EffectIdentity, ReceiptDependency, effect_request_digest
from open_workflow_agent.engine import PortableWorkflowEngine
from open_workflow_agent.errors import InvocationStateConflict, ToolError, WorkflowExecutionError
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.security import ProfileAuthentication, SecurityConfig
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow

CARD_URI = "https://metadata.test/card"
TARGET = "https://business.test/a2a"


def _source():
    return {
        "document": {"dsl": "1.0.3", "namespace": "cards", "name": "admission", "version": "1.0.0"},
        "do": [
            {
                "action": {
                    "call": "a2a",
                    "with": {
                        "agentCard": {"endpoint": CARD_URI},
                        "server": "https://ignored.test/a2a",
                        "method": "tasks/get",
                        "parameters": {"id": "task-id"},
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
        "profile": "card-store-test",
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
        handle.invocation_id, plan.fingerprint, "/do/0/action", (("/do/0/action", 1),), "call:a2a"
    )
    payload = copy.deepcopy(_source()["do"][0]["action"]["with"])
    calls = []
    state = {"card": json_format.MessageToDict(peer_card(TARGET)), "clock": 1000.0}

    async def receiver(request):
        calls.append(request)
        assert request.method == "GET" and str(request.url) == CARD_URI
        assert "authorization" not in request.headers
        return httpx.Response(200, json=state["card"])

    services.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(receiver)))
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
    return await services.a2a_agent_card_admission.prepare(
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
    policy = replace(identity, operation=CARD_OPERATION)
    return tuple(
        connection.execute(sql, args).fetchall()
        for sql, args in (
            ("SELECT * FROM a2a_agent_card_admissions WHERE business_key=?", (identity.key,)),
            ("SELECT * FROM workflow_effects WHERE identity=?", (policy.key,)),
            ("SELECT * FROM workflow_effect_invocations WHERE identity=?", (policy.key,)),
        )
    )


@pytest.mark.asyncio
async def test_card_decision_is_immutable_and_preserves_original_budget_without_refetch(
    admitted_parent,
):
    services, handle, identity, _, calls, state = admitted_parent
    original = await _prepare(admitted_parent)
    assert (
        original.selection.endpoint == TARGET
        and original.selection.endpoint != "https://ignored.test/a2a"
    )
    assert original.receipt.output["deadline_at"] == 1030.0
    dependency = ReceiptDependency.capture(original.identity, original.receipt)
    before = _rows(services, identity)
    state["card"] = json_format.MessageToDict(peer_card("https://changed.test/a2a"))
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
async def test_missing_or_changed_native_policy_dependency_fails_without_new_fetch(
    admitted_parent, damage
):
    services, handle, identity, _, calls, _ = admitted_parent
    decision = await _prepare(admitted_parent)
    dependency = ReceiptDependency.capture(decision.identity, decision.receipt)
    connection = services.effect_journal.connection
    if damage == "decision":
        connection.execute(
            "DELETE FROM a2a_agent_card_admissions WHERE business_key=?", (identity.key,)
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
            value["selection"]["endpoint"] = "https://forged.test/a2a"
        else:
            value["deadline_at"] = 9999.0
        receipt = replace(
            decision.receipt,
            output=value,
            request_digest=effect_request_digest(CARD_OPERATION, value),
        )
        connection.execute(
            "UPDATE a2a_agent_card_admissions SET payload=?,digest=? WHERE business_key=?",
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
    with pytest.raises(WorkflowExecutionError):
        dependency.verify(services.effect_journal)
    with pytest.raises(WorkflowExecutionError):
        await _prepare(admitted_parent)
    assert len(calls) == 1 and _rows(services, identity) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["payload", "deadline", "grant"])
async def test_changed_request_or_deployment_binding_cannot_replace_original_decision(
    admitted_parent, change
):
    services, _, identity, payload, calls, _ = admitted_parent
    await _prepare(admitted_parent)
    before = _rows(services, identity)
    if change == "payload":
        payload["parameters"]["id"] = "other-task"
    elif change == "grant":
        services.config.protocols.a2a_credential_endpoints["other-profile"] = (
            "https://other.test/a2a",
        )
    with pytest.raises(WorkflowExecutionError):
        await _prepare(admitted_parent, deadline_at=1050.0 if change == "deadline" else 1040.0)
    assert len(calls) == 1 and _rows(services, identity) == before


@pytest.mark.asyncio
async def test_existing_legacy_pending_intent_never_adopts_new_card_admission(admitted_parent):
    services, handle, identity, payload, calls, _ = admitted_parent
    original = services.effect_journal.begin(
        identity,
        effect_request_digest("a2a", payload),
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
        await services.a2a_agent_card_admission.prepare(
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
        return httpx.Response(200, json=state["card"])

    services.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(receiver)))
    with pytest.raises(InvocationStateConflict):
        await _prepare(admitted_parent)
    assert len(calls) == 1 and _rows(services, identity) == ([], [], [])


@pytest.mark.asyncio
async def test_failed_policy_transaction_rolls_back_decision_receipt_and_index(
    admitted_parent, monkeypatch
):
    services, handle, identity, _, calls, _ = admitted_parent
    insert = a2a_admission.insert_completed_receipt

    def interrupted(connection, policy, receipt):
        insert(connection, policy, receipt)
        raise WorkflowExecutionError("injected transaction interruption")

    monkeypatch.setattr(a2a_admission, "insert_completed_receipt", interrupted)
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
        return httpx.Response(200, json=state["card"])

    services.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(receiver)))
    with pytest.raises(ToolError, match="deadline expired"):
        await _prepare(admitted_parent)
    assert len(calls) == 1 and _rows(services, identity) == ([], [], [])


@pytest.mark.asyncio
async def test_explicit_endpoint_is_independent_of_mutable_card_admission(admitted_parent):
    services, handle, identity, _, calls, state = admitted_parent
    result = await services.a2a_agent_card_admission.prepare(
        identity,
        {"server": TARGET, "method": "tasks/get", "parameters": {"id": "task-id"}},
        handle,
        execution_lease=None,
        deadline_at=None,
        now=lambda: state["clock"],
    )
    assert result is None and calls == [] and _rows(services, identity) == ([], [], [])


@pytest.mark.asyncio
async def test_slow_metadata_obeys_explicit_call_timeout_before_any_business_dispatch(
    admitted_parent,
):
    services, _, identity, payload, calls, state = admitted_parent
    payload["timeout"] = {"milliseconds": 20}
    cancelled = []

    async def receiver(request):
        calls.append(request)
        try:
            await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise
        return httpx.Response(200, json=state["card"])

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
        return httpx.Response(200, json=state["card"])

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
async def test_public_card_discovery_retains_only_named_auth_reference(
    admitted_parent, monkeypatch
):
    services, _, identity, _, calls, _ = admitted_parent
    monkeypatch.delenv("CARD_ADMISSION_TOKEN", raising=False)
    security = SecurityConfig.model_validate(
        {
            "profiles": {
                "named": {
                    "type": "bearer",
                    "token": {"from_env": "CARD_ADMISSION_TOKEN"},
                }
            }
        }
    )
    auth = ProfileAuthentication(security, "named")
    services.protocols.http.authentication = auth
    services.config.protocols.a2a_credential_endpoints["named"] = (TARGET,)
    services.protocols._a2a_credential_endpoints["named"] = (TARGET,)
    decision = await _prepare(admitted_parent)
    assert decision.selection.credential_profile == "named"
    assert decision.receipt.output["transport_binding"]["authentication"]["profile"]["token"] == {
        "from_env": "CARD_ADMISSION_TOKEN"
    }
    assert len(calls) == 1 and "authorization" not in calls[0].headers
    ReceiptDependency.capture(decision.identity, decision.receipt).verify(services.effect_journal)
    assert len(_rows(services, identity)[0]) == 1


@pytest.mark.asyncio
async def test_changed_applied_credential_grant_cannot_reuse_saved_card(admitted_parent):
    services, _, identity, _, calls, _ = admitted_parent
    await _prepare(admitted_parent)
    before = _rows(services, identity)
    services.protocols._a2a_credential_endpoints["forged"] = (TARGET,)
    with pytest.raises(WorkflowExecutionError, match="applied credential authority changed"):
        await _prepare(admitted_parent)
    assert len(calls) == 1 and _rows(services, identity) == before


@pytest.mark.asyncio
async def test_applied_ca_must_match_its_named_reference_before_metadata_io(
    admitted_parent, monkeypatch
):
    services, _, identity, _, calls, _ = admitted_parent
    monkeypatch.setenv("CARD_ADMISSION_CA", "declared-ca.pem")
    security = SecurityConfig.model_validate(
        {
            "profiles": {
                "named": {
                    "type": "mtls",
                    "certificate": {"from_env": "CARD_ADMISSION_CERT"},
                    "private_key": {"from_env": "CARD_ADMISSION_KEY"},
                    "ca_bundle": {"from_env": "CARD_ADMISSION_CA"},
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
async def test_known_business_receipt_replays_original_card_after_deadline_without_refetch(
    admitted_parent, outcome
):
    services, handle, identity, payload, calls, state = admitted_parent
    decision = await _prepare(admitted_parent)
    services.effect_journal.begin(
        identity,
        effect_request_digest("a2a", payload),
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
async def test_workflow_retains_policy_dependency_before_official_sdk_business_dispatch(tmp_path):
    async with sdk_a2a_fixture() as (transport, calls, endpoint):
        services = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
        services.recovery_definitions.profile_provider = lambda engine: {
            "profile": "card-store-test",
            "engine": engine,
        }
        services.protocols = ProtocolServices(HttpClient(transport=transport))
        source = _source()
        source["do"][0]["action"]["with"]["agentCard"]["endpoint"] = (
            "https://a2a.test/.well-known/agent-card.json"
        )
        plan = compile_workflow(source)
        engine = PortableWorkflowEngine()
        observed = []

        async def notify(state, identity, receipt):
            if identity.operation == CARD_OPERATION:
                assert [call["method"] for call in calls] == ["GET"]
                ReceiptDependency.capture(identity, receipt).verify(services.effect_journal)
                observed.append(identity)

        await engine.initialize(services)
        engine.executor._notify_receipt = notify
        handle = services.invocations.create(
            engine="portable",
            session_id="session",
            user_id="user",
            workflow_name=plan.name,
            workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
        )
        try:
            result = await engine.invoke(plan, handle, {"original": None})
            assert result.status == "completed", result.error
            assert len(observed) == 1 and [call["method"] for call in calls] == ["GET", "POST"]
            assert calls[1]["body"]["method"] == "GetTask"
            assert services.invocations.read_input(handle) == {"original": None}
            assert services.model.calls == []
            assert result.output["id"] == "task-id"
            assert observed[0].task_reference == "/do/0/action"
            assert endpoint == "https://a2a.test/a2a"
        finally:
            await engine.shutdown()
            services.close()
