"""Trusted receiver outcomes resolve only originally certified HTTP intents."""

from __future__ import annotations

import asyncio
import json
import os
import threading
from dataclasses import replace

import pytest
from contract._external_receiver import PRINCIPAL, RECEIVER_ID, TOKEN, TOKEN_ENV, receiver
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.effects import (
    EffectIdentity,
    EffectJournal,
    ReceiptDependency,
    effect_request_digest,
)
from open_workflow_agent.errors import (
    ConfigurationError,
    InvocationStateConflict,
    ToolError,
    WorkflowExecutionError,
)
from open_workflow_agent.external_recovery import (
    ADMISSION_OPERATION,
    RECOVERY_OPERATION,
    HttpExternalActionRecovery,
    TrustedHttpReceiver,
)
from open_workflow_agent.persistence import InvocationStore
from open_workflow_agent.protocols import HttpClient
from open_workflow_agent.security import ProfileAuthentication, SecurityConfig
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow


@pytest.fixture(params=["sqlite", "postgresql"])
def trusted_action(tmp_path, request, monkeypatch):
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if request.param == "postgresql" else None
    if request.param == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    services = RuntimeServices(
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}}),
        model=FakeModel(),
        database_root=None if datasource else tmp_path / "runtime",
    )
    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "receiver",
                "name": "caller",
                "version": "1.0.0",
            },
            "do": [{"finish": {"set": {"done": True}}}],
        }
    )
    parent = services.invocations.create(
        engine="adk",
        session_id=None,
        user_id="correlation",
        owner_principal="caller",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    services.invocations.bind_input(parent, {"original": True})
    parent._execution_lease = services.execution_ownership.acquire(parent, seconds=300)
    identity = EffectIdentity(
        parent.invocation_id,
        parent.workflow_fingerprint,
        "/do/0/action",
        (("/do/0/action", 1),),
        "call:http",
    )
    with receiver(tmp_path) as remote:
        security = SecurityConfig.model_validate(
            {
                "profiles": {
                    "receiver": {
                        "type": "bearer",
                        "principal": PRINCIPAL,
                        "token": {"from_env": TOKEN_ENV},
                    }
                }
            }
        )
        http = HttpClient(
            timeout=5,
            max_response_bytes=65536,
            ca_bundle=str(remote.certificate),
            allowed_hosts={"127.0.0.1"},
            authentication=ProfileAuthentication(security, "receiver"),
        )
        binding = TrustedHttpReceiver(
            RECEIVER_ID, remote.business_endpoint, remote.lookup_endpoint, "receiver", PRINCIPAL
        )
        helper = HttpExternalActionRecovery(
            services.effect_journal, services.invocations, http, (binding,)
        )
        payload = {
            "method": "POST",
            "endpoint": remote.business_endpoint,
            "body": {"original": True},
            "operation_id": identity.key,
        }
        try:
            yield services, parent, identity, payload, helper, remote
        finally:
            services.invocations.refresh(parent)
            if parent.status not in {"completed", "faulted", "cancelled"}:
                services.invocations.update(parent, status="cancelled")
            if parent._execution_lease is not None:
                try:
                    services.execution_ownership.release(parent._execution_lease)
                except InvocationStateConflict:
                    pass
            services.close()


def _admit(tree):
    _, parent, identity, payload, helper, _ = tree
    return helper.admit(
        identity,
        payload,
        owner="original-dispatch",
        operation_id=identity.key,
        execution_lease=parent._execution_lease,
    )


async def _commit(tree):
    _, _, identity, payload, helper, _ = tree
    try:
        return await helper.http.request(
            "POST", payload["endpoint"], json=payload["body"], operation_id=identity.key
        )
    except ToolError:
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["value", "null", "fault"])
async def test_query_proof_retains_first_owner_and_known_value_null_or_fault(
    trusted_action, outcome
):
    services, parent, identity, payload, helper, remote = trusted_action
    remote.outcome = outcome
    intent, trust = _admit(trusted_action)
    native_trust = ReceiptDependency.capture(
        replace(identity, operation=ADMISSION_OPERATION), trust
    )
    await _commit(trusted_action)
    # The receiver also proves a repeated business POST is deduplicated.
    await _commit(trusted_action)
    assert remote.commits() == 1
    resolved, proof = await helper.recover(
        identity, payload, execution_lease=parent._execution_lease
    )
    assert resolved.owner == intent.owner == "original-dispatch"
    assert resolved.operation_id == intent.operation_id == identity.key
    assert resolved.request_digest == intent.request_digest
    assert resolved.status == ("faulted" if outcome == "fault" else "completed")
    assert resolved.output == (
        None if outcome in {"null", "fault"} else {"committed": True, "operation_id": identity.key}
    )
    assert resolved.external_outcome is not None
    native_trust.verify(services.effect_journal)
    ReceiptDependency.capture(identity, resolved).verify(services.effect_journal)
    ReceiptDependency.capture(replace(identity, operation=RECOVERY_OPERATION), proof).verify(
        services.effect_journal
    )
    before = list(remote.requests)
    assert await helper.recover(identity, payload, execution_lease=parent._execution_lease) == (
        resolved,
        proof,
    )
    assert remote.requests == before
    serialized = json.dumps(trust.output)
    assert TOKEN not in serialized and TOKEN_ENV in serialized
    assert services.model.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("unknown", ["missing", "pending", "unknown"])
async def test_unknown_receiver_outcome_keeps_intent_pending_without_business_replay(
    trusted_action, unknown
):
    services, parent, identity, payload, helper, remote = trusted_action
    intent, _ = _admit(trusted_action)
    if unknown != "missing":
        remote.replacement_proof = {"status": unknown}
    assert await helper.recover(identity, payload, execution_lease=parent._execution_lease) is None
    assert services.effect_journal.get(identity) == intent
    assert remote.commits() == 0
    assert [method for method, _ in remote.requests] == ["GET"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage",
    [
        "request",
        "client-limit",
        "client-timeout",
        "client-tls",
        "credential-reference",
        "credential-source",
        "lookup-endpoint",
        "admission-index",
        "action-index",
        "principal",
        "input",
        "missing-trust",
        "trust-digest",
        "missing-policy",
    ],
)
async def test_changed_original_admission_rejects_before_lookup_or_state_mutation(
    trusted_action, damage
):
    services, parent, identity, payload, helper, remote = trusted_action
    intent, _ = _admit(trusted_action)
    connection = services.effect_journal.connection
    if damage == "request":
        payload = {**payload, "body": {"changed": True}}
    elif damage == "client-limit":
        helper.http.max_response_bytes = 12345
    elif damage == "client-timeout":
        helper.http.timeout = 10
    elif damage == "client-tls":
        helper.http.verify_tls = False
    elif damage == "credential-reference":
        helper.http.authentication.profile_name = "different"
    elif damage == "credential-source":
        helper.http.authentication.security.profiles["receiver"].token.from_env = "CHANGED_SOURCE"
    elif damage == "lookup-endpoint":
        helper.receivers = (
            replace(helper.receivers[0], lookup_endpoint=remote.lookup_endpoint + "-changed"),
        )
    elif damage in {"admission-index", "action-index"}:
        connection.execute(
            "DELETE FROM workflow_effect_invocations WHERE identity=?",
            (
                replace(identity, operation=ADMISSION_OPERATION).key
                if damage == "admission-index"
                else identity.key,
            ),
        )
    elif damage == "principal":
        helper.http.authentication.security.profiles["receiver"].principal = "different"
    elif damage == "input":
        connection.execute(
            "UPDATE invocation_inputs SET payload='null' WHERE invocation_id=?",
            (parent.invocation_id,),
        )
    elif damage == "missing-trust":
        connection.execute("DELETE FROM external_http_admissions WHERE identity=?", (identity.key,))
    elif damage == "trust-digest":
        connection.execute(
            "UPDATE external_http_admissions SET digest='invalid' WHERE identity=?", (identity.key,)
        )
    else:
        connection.execute(
            "DELETE FROM workflow_effects WHERE identity=?",
            (replace(identity, operation=ADMISSION_OPERATION).key,),
        )
    connection.commit()
    with pytest.raises((WorkflowExecutionError, ConfigurationError)):
        await helper.recover(identity, payload, execution_lease=parent._execution_lease)
    assert remote.requests == []
    assert services.effect_journal.get(identity) == intent


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage",
    [
        "operation_id",
        "wire_request_digest",
        "principal_reference",
        "contract_version",
        "receiver_id",
        "extra",
    ],
)
async def test_lookup_proof_must_match_immutable_original_business_commit(trusted_action, damage):
    services, parent, identity, payload, helper, remote = trusted_action
    intent, _ = _admit(trusted_action)
    await _commit(trusted_action)
    proof = remote.proof(identity.key)
    proof[damage] = "changed"
    remote.replacement_proof = proof
    with pytest.raises(WorkflowExecutionError):
        await helper.recover(identity, payload, execution_lease=parent._execution_lease)
    assert services.effect_journal.get(identity) == intent
    assert remote.commits() == 1
    assert [method for method, _ in remote.requests] == ["POST", "GET"]


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["admission", "outcome", "outcome-digest", "recovery-receipt"])
async def test_known_native_outcome_fails_closed_when_common_proof_is_deleted_or_changed(
    trusted_action, damage
):
    services, parent, identity, payload, helper, _ = trusted_action
    _admit(trusted_action)
    await _commit(trusted_action)
    resolved, _ = await helper.recover(identity, payload, execution_lease=parent._execution_lease)
    dependency = ReceiptDependency.capture(identity, resolved)
    connection = services.effect_journal.connection
    if damage == "admission":
        connection.execute("DELETE FROM external_http_admissions WHERE identity=?", (identity.key,))
    elif damage == "outcome":
        connection.execute("DELETE FROM external_http_outcomes WHERE identity=?", (identity.key,))
    elif damage == "outcome-digest":
        connection.execute(
            "UPDATE external_http_outcomes SET digest='changed' WHERE identity=?", (identity.key,)
        )
    else:
        connection.execute(
            "DELETE FROM workflow_effects WHERE identity=?",
            (replace(identity, operation=RECOVERY_OPERATION).key,),
        )
    connection.commit()
    with pytest.raises(WorkflowExecutionError):
        dependency.verify(services.effect_journal)


@pytest.mark.asyncio
async def test_stale_generation_performs_no_lookup_and_cancel_during_lookup_cannot_commit(
    trusted_action,
):
    services, parent, identity, payload, helper, remote = trusted_action
    intent, _ = _admit(trusted_action)
    await _commit(trusted_action)
    with pytest.raises(InvocationStateConflict):
        await helper.recover(
            identity,
            payload,
            execution_lease=replace(
                parent._execution_lease, generation=parent._execution_lease.generation + 1
            ),
        )
    assert [method for method, _ in remote.requests] == ["POST"]
    remote.lookup_barrier = threading.Event()
    pending = asyncio.create_task(
        helper.recover(identity, payload, execution_lease=parent._execution_lease)
    )
    async with asyncio.timeout(10):
        while not remote.lookup_started.is_set():
            await asyncio.sleep(0.01)
    services.invocations.update(parent, status="cancelled")
    remote.lookup_barrier.set()
    with pytest.raises(InvocationStateConflict):
        await pending
    assert services.effect_journal.get(identity) == intent
    assert services.invocations.get(parent.invocation_id).status == "cancelled"


@pytest.mark.asyncio
async def test_two_queries_reconcile_one_immutable_outcome_without_second_business_action(
    trusted_action,
):
    _, parent, identity, payload, helper, remote = trusted_action
    _admit(trusted_action)
    await _commit(trusted_action)
    first, second = await asyncio.gather(
        helper.recover(identity, payload, execution_lease=parent._execution_lease),
        helper.recover(identity, payload, execution_lease=parent._execution_lease),
    )
    assert first == second
    assert remote.commits() == 1
    assert [method for method, _ in remote.requests].count("POST") == 1


@pytest.mark.asyncio
async def test_independent_connections_reconcile_concurrent_query_proofs_once(trusted_action):
    services, parent, identity, payload, helper, remote = trusted_action
    _admit(trusted_action)
    await _commit(trusted_action)
    database = (
        services.database_root / "runtime.sqlite3"
        if services.database_root is not None
        else services.datasource
    )
    remote.lookup_barrier = threading.Event()

    def independent_lookup():
        invocations = InvocationStore(database)
        journal = EffectJournal(database, invocations=invocations)
        peer = HttpExternalActionRecovery(journal, invocations, helper.http, helper.receivers)
        try:
            return asyncio.run(
                peer.recover(identity, payload, execution_lease=parent._execution_lease)
            )
        finally:
            journal.close()
            invocations.close()

    pending = [asyncio.create_task(asyncio.to_thread(independent_lookup)) for _ in range(2)]
    try:
        async with asyncio.timeout(15):
            while [method for method, _ in remote.requests].count("GET") < 2:
                await asyncio.sleep(0.01)
            remote.lookup_barrier.set()
            first, second = await asyncio.gather(*pending)
        assert first == second
        assert remote.commits() == 1
        assert [method for method, _ in remote.requests].count("POST") == 1
        ReceiptDependency.capture(identity, first[0]).verify(services.effect_journal)
    finally:
        remote.lookup_barrier.set()
        await asyncio.gather(*pending, return_exceptions=True)


def test_admission_rolls_back_trust_policy_and_intent_together(trusted_action, monkeypatch):
    import open_workflow_agent.external_recovery as module

    services, _, identity, _, _, remote = trusted_action
    insert = module.insert_completed_receipt

    def broken(*args, **kwargs):
        insert(*args, **kwargs)
        raise WorkflowExecutionError("injected trust receipt failure")

    monkeypatch.setattr(module, "insert_completed_receipt", broken)
    with pytest.raises(WorkflowExecutionError, match="injected"):
        _admit(trusted_action)
    assert services.effect_journal.get(identity) is None
    assert (
        services.effect_journal.connection.execute(
            "SELECT 1 FROM external_http_admissions WHERE identity=?", (identity.key,)
        ).fetchone()
        is None
    )
    assert remote.requests == []


@pytest.mark.asyncio
async def test_reconciliation_rolls_back_proof_policy_and_outcome_together(
    trusted_action, monkeypatch
):
    import open_workflow_agent.external_recovery as module

    services, parent, identity, payload, helper, remote = trusted_action
    intent, _ = _admit(trusted_action)
    await _commit(trusted_action)
    insert = module.insert_completed_receipt

    def broken(*args, **kwargs):
        insert(*args, **kwargs)
        raise WorkflowExecutionError("injected reconciliation receipt failure")

    monkeypatch.setattr(module, "insert_completed_receipt", broken)
    with pytest.raises(WorkflowExecutionError, match="injected"):
        await helper.recover(identity, payload, execution_lease=parent._execution_lease)
    connection = services.effect_journal.connection
    assert services.effect_journal.get(identity) == intent
    assert services.effect_journal.get(replace(identity, operation=RECOVERY_OPERATION)) is None
    assert (
        connection.execute(
            "SELECT 1 FROM external_http_outcomes WHERE identity=?", (identity.key,)
        ).fetchone()
        is None
    )
    assert remote.commits() == 1


@pytest.mark.asyncio
async def test_receiver_rejects_same_operation_key_with_different_business_request(trusted_action):
    services, _, identity, payload, helper, remote = trusted_action
    intent, _ = _admit(trusted_action)
    await _commit(trusted_action)
    original = remote.proof(identity.key)
    with pytest.raises(ToolError) as caught:
        await helper.http.request(
            "POST", payload["endpoint"], json={"different": True}, operation_id=identity.key
        )
    assert caught.value.details["status"] == 409
    assert remote.commits() == 1 and remote.proof(identity.key) == original
    assert services.effect_journal.get(identity) == intent


def test_runtime_services_external_recovery_is_disabled_without_deployment_binding(tmp_path):
    services = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
    try:
        assert services.external_action_recovery is None
    finally:
        services.close()


def test_runtime_services_uses_its_bound_http_client_for_explicit_receiver(tmp_path):
    binding = TrustedHttpReceiver(
        "declared", "https://receiver.test/action", "https://receiver.test/outcome", "none", "owner"
    )
    services = RuntimeServices(
        RuntimeConfig(),
        model=FakeModel(),
        database_root=tmp_path,
        trusted_http_receivers=(binding,),
    )
    try:
        assert services.external_action_recovery is not None
        assert services.external_action_recovery.http is services.protocols.http
        assert services.external_action_recovery.receivers == (binding,)
        assert services.external_action_recovery.http.timeout == 30
    finally:
        services.close()


def test_configured_receiver_cannot_retrofit_legacy_pending_intent_or_bind_other_actions(
    trusted_action,
):
    services, parent, identity, payload, helper, _ = trusted_action
    legacy = services.effect_journal.begin(
        identity,
        effect_request_digest("http", payload),
        "legacy-owner",
        operation_id=identity.key,
        execution_lease=parent._execution_lease,
    )
    original = ReceiptDependency._digest(legacy)
    assert _admit(trusted_action) is None
    assert services.effect_journal.get(identity) == legacy
    assert ReceiptDependency._digest(services.effect_journal.get(identity)) == original
    assert (
        helper.admit(
            replace(identity, operation="call:mcp"),
            payload,
            owner="new",
            operation_id=identity.key,
            execution_lease=parent._execution_lease,
        )
        is None
    )
    assert (
        helper.admit(
            identity,
            {**payload, "endpoint": "https://unknown.test/action"},
            owner="new",
            operation_id=identity.key,
            execution_lease=parent._execution_lease,
        )
        is None
    )
