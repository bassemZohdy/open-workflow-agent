"""Real common authority/transactions; native qualification has separate fixtures."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import sqlite3
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from open_workflow_agent.api import create_app
from open_workflow_agent.approvals import ApprovalStore
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.effects import EffectIdentity, effect_request_digest
from open_workflow_agent.engine import PortableWorkflowEngine
from open_workflow_agent.errors import (
    ApprovalAuthorizationError,
    ApprovalConflict,
    ApprovalValidationError,
    OwaError,
    WorkflowExecutionError,
)
from open_workflow_agent.events import EventEnvelope
from open_workflow_agent.protected_approval_policy import (
    PROTECTED_APPROVAL_PROFILE,
    ProtectedApprovalPolicy,
    original_expiry,
)
from open_workflow_agent.protected_approval_recovery import (
    protected_approval_event_retained,
    protected_approval_invocation_retained,
    verify_protected_approval_business_receipt,
)
from open_workflow_agent.protected_approval_service import mutate_protected
from open_workflow_agent.protected_approval_store import REQUEST_POLICY
from open_workflow_agent.recovery_definitions import RecoveryDefinitionArchive
from open_workflow_agent.security import Principal
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.storage import StorageConnection
from open_workflow_agent.workflow import compile_workflow


@pytest.fixture(params=["sqlite", "postgresql"])
def approval_runtime(tmp_path, monkeypatch, request):
    backend = request.param
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("actual PostgreSQL is required")
    subject = uuid4().hex
    principals = {key: key + "-" + subject for key in ("a", "b", "outsider", "requester")}
    for name in ("api", "a", "b", "outsider"):
        monkeypatch.setenv("OWA_APPROVAL_" + name.upper(), name + "-controlled-token")
    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "persistence": {"datasource": datasource},
            "server": {"api_security_profile": "api"},
            "security": {
                "profiles": {
                    name: {
                        "type": "bearer",
                        "token": {"from_env": "OWA_APPROVAL_" + name.upper()},
                        "principal": principals["requester" if name == "api" else name],
                    }
                    for name in ("api", "a", "b", "outsider")
                }
            },
            "approvals": {
                "enabled": True,
                "protected": True,
                "operator_security_profile": "api",
                "actor_security_profiles": ["a", "b", "outsider"],
            },
        }
    )
    services = RuntimeServices(
        config, model=FakeModel(), database_root=None if datasource else tmp_path
    )
    assert (
        services.effect_journal.connection.kind == services.invocations.connection.kind == backend
    )
    # Explicit low-level archive seam: these are common store/API unit proofs,
    # never an assertion that an absent adapter/SDK is natively qualified.
    services.recovery_definitions = RecoveryDefinitionArchive(
        services.effect_journal,
        services.invocations,
        profile_provider=lambda engine: {
            "profile": "test-only/common-approval/v1",
            "engine": engine,
            "backend": backend,
            "continuation_supported": True,
        },
    )
    yield services, config, principals
    services.close()


def test_postgresql_approval_timestamps_upgrade_legacy_real_columns(approval_runtime, monkeypatch):
    services, _config, _principals = approval_runtime
    connection = services.effect_journal.connection
    if not connection.is_postgresql:
        pytest.skip("legacy REAL precision migration is PostgreSQL-specific")

    # Exercise a legacy schema without persisting the temporary downgrade or
    # rounding timestamps belonging to other PostgreSQL fixtures in this DB.
    raw = connection.raw
    previous_autocommit = raw.autocommit
    raw.autocommit = False
    monkeypatch.setattr(type(connection), "commit", lambda _self: None)
    try:
        for column in ("requested_at", "expires_at", "decided_at"):
            connection.execute(
                f"ALTER TABLE owa_approvals.approvals ALTER COLUMN {column} "
                f"TYPE REAL USING {column}::real"
            )
        ApprovalStore(connection=connection)

        approval_id = "precision-migration-" + uuid4().hex
        timestamp = 1_791_959_249.8879814
        connection.execute(
            "INSERT INTO owa_approvals.approvals "
            "(approval_id,subject,status,request_event,requested_at,expires_at,decided_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                approval_id,
                approval_id,
                "pending",
                "{}",
                timestamp,
                timestamp,
                timestamp,
            ),
        )
        saved = connection.execute(
            "SELECT requested_at,expires_at,decided_at FROM owa_approvals.approvals "
            "WHERE approval_id=?",
            (approval_id,),
        ).fetchone()
        assert tuple(saved) == (timestamp, timestamp, timestamp)
    finally:
        try:
            raw.rollback()
        finally:
            raw.autocommit = previous_autocommit


async def seed(runtime, *, expired=False, assignee="a"):
    services, _config, principals = runtime
    subject = str(uuid4())
    expiry = datetime.now(UTC) + timedelta(seconds=-1 if expired else 300)
    event = {
        "id": subject,
        "subject": subject,
        "type": "io.openworkflow.agent.approval.requested",
        "approvalexpiresat": expiry.isoformat().replace("+00:00", "Z"),
        "data": {
            "question": "continue?",
            "approvalPolicy": {
                "profile": PROTECTED_APPROVAL_PROFILE,
                "assignee": principals[assignee],
                "reassignmentTargets": [principals["b"]],
                "escalationTargets": [principals["b"]],
                "responseSchema": {
                    "format": "json",
                    "document": {
                        "type": ["object", "null"],
                        "properties": {"allowed": {"const": True}},
                        "additionalProperties": False,
                    },
                },
            },
        },
    }
    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "core-approval",
                "name": "protected",
                "version": "1.0.0",
            },
            "do": [{"request": {"emit": {"event": {"with": event}}}}],
        }
    )
    handle = services.invocations.create(
        engine="adk",
        session_id=None,
        user_id="requester",
        owner_principal=principals["requester"],
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    handle._execution_lease = services.execution_ownership.acquire(handle, seconds=300)
    services.invocations.update(handle, status="running")
    services.recovery_definitions.admit(
        plan,
        handle,
        {"original": subject, "nullValue": None},
        execution_lease=handle._execution_lease,
    )
    reference = "/do/0/request"
    identity = EffectIdentity(
        handle.invocation_id,
        plan.fingerprint,
        reference,
        ((reference, 1), (reference + "/@attempt", 1)),
        "emit",
    )
    await services.event_bus.publish_workflow_event(
        identity,
        effect_request_digest("emit", event),
        event,
        default_source="urn:core:approval",
        scope=principals["requester"],
        execution_lease=handle._execution_lease,
    )
    return subject, handle, identity


def snapshot(services, subject, *, original=None):
    protected = services.approvals.protected_store
    connection = services.effect_journal.connection
    raw = connection.execute(
        f"SELECT payload FROM {protected.requests} WHERE approval_id=?", (subject,)
    ).fetchone()
    original = original or json.loads(raw[0])
    invocation_id = original["origin"]["invocation_id"]
    scope = original["event_scope"]
    return {
        table: connection.execute(statement, parameters).fetchall()
        for table, statement, parameters in (
            (
                "approval",
                f"SELECT * FROM {services.approvals.store._table} WHERE approval_id=?",
                (subject,),
            ),
            ("request", f"SELECT * FROM {protected.requests} WHERE approval_id=?", (subject,)),
            (
                "audit",
                f"SELECT * FROM {protected.audit} WHERE approval_id=? ORDER BY revision",
                (subject,),
            ),
            (
                "inbox",
                "SELECT scope,source,event_id,envelope,digest FROM event_inbox "
                "WHERE scope=? AND (event_id=? OR envelope LIKE ?) ORDER BY sequence",
                (scope, subject, "%" + subject + "%"),
            ),
            (
                "effects",
                "SELECT e.identity,e.payload FROM workflow_effects e "
                "JOIN workflow_effect_invocations i ON i.identity=e.identity "
                "WHERE i.invocation_id=? ORDER BY e.identity",
                (invocation_id,),
            ),
            (
                "index",
                "SELECT * FROM workflow_effect_invocations WHERE invocation_id=? ORDER BY identity",
                (invocation_id,),
            ),
        )
    }


def actor(services, name):
    return services.approvals.authorize_actor("Bearer " + name + "-controlled-token")


@pytest.mark.asyncio
async def test_reassignment_fences_original_actor_and_preserves_original_policy_and_scope(
    approval_runtime,
):
    services, _, principals = approval_runtime
    subject, handle, _ = await seed(approval_runtime)
    original, _ = services.approvals.protected_store.inspect(subject)
    updated = await services.approvals.mutate_protected(
        subject,
        action="reassign",
        actor=actor(services, "a"),
        revision=1,
        operation_key=str(uuid4()),
        assignee=principals["b"],
    )
    assert updated.assignment_revision == 2 and updated.assignee == principals["b"]
    before = snapshot(services, subject)
    with pytest.raises(ApprovalAuthorizationError):
        await services.approvals.decide(
            subject,
            decision="approved",
            operator_id=principals["a"],
            value=None,
            operation_key=str(uuid4()),
            revision=2,
            actor=actor(services, "a"),
        )
    with pytest.raises(ApprovalConflict):
        await services.approvals.decide(
            subject,
            decision="approved",
            operator_id=principals["b"],
            value=None,
            operation_key=str(uuid4()),
            revision=1,
            actor=actor(services, "b"),
        )
    assert snapshot(services, subject) == before
    decided = await services.approvals.decide(
        subject,
        decision="approved",
        operator_id=principals["b"],
        value=None,
        operation_key=str(uuid4()),
        revision=2,
        actor=actor(services, "b"),
    )
    assert (
        decided.status == "approved"
        and decided.assignment_revision == 3
        and decided.decision_event["data"]["value"] is None
    )
    assert services.approvals.protected_store.inspect(subject)[0] == original
    services.verify_protected_approvals(handle)
    assert services.model.calls == []


@pytest.mark.asyncio
async def test_terminal_replay_requires_exact_actor_revision_value_and_action(approval_runtime):
    services, _, principals = approval_runtime
    subject, _, _ = await seed(approval_runtime)
    key = str(uuid4())
    original = await services.approvals.decide(
        subject,
        decision="approved",
        operator_id=principals["a"],
        value={"allowed": True},
        operation_key=key,
        revision=1,
        actor=actor(services, "a"),
    )
    before = snapshot(services, subject)
    replay = await services.approvals.decide(
        subject,
        decision="approved",
        operator_id=principals["a"],
        value={"allowed": True},
        operation_key=key,
        revision=1,
        actor=actor(services, "a"),
    )
    assert replay.as_dict() == original.as_dict() and snapshot(services, subject) == before
    for decision, value, revision, name in (
        ("rejected", {"allowed": True}, 1, "a"),
        ("approved", None, 1, "a"),
        ("approved", {"allowed": True}, 2, "a"),
        ("approved", {"allowed": True}, 1, "b"),
    ):
        with pytest.raises(ApprovalConflict):
            await services.approvals.decide(
                subject,
                decision=decision,
                operator_id=principals[name],
                value=value,
                operation_key=key,
                revision=revision,
                actor=actor(services, name),
            )
        assert snapshot(services, subject) == before


@pytest.mark.asyncio
async def test_invalid_response_schema_keeps_pending_and_scoped_inbox_unchanged(approval_runtime):
    services, _, principals = approval_runtime
    subject, _, _ = await seed(approval_runtime)
    before = snapshot(services, subject)
    with pytest.raises(ApprovalValidationError):
        await services.approvals.decide(
            subject,
            decision="approved",
            operator_id=principals["a"],
            value={"allowed": False},
            revision=1,
            operation_key=str(uuid4()),
            actor=actor(services, "a"),
        )
    assert (
        snapshot(services, subject) == before
        and services.approvals.store.get(subject).status == "pending"
    )


@pytest.mark.asyncio
async def test_due_expiry_persists_first_outcome_and_never_refreshes_original_budget(
    approval_runtime,
):
    services, _, principals = approval_runtime
    subject, handle, _ = await seed(approval_runtime, expired=True)
    original, _ = services.approvals.protected_store.inspect(subject)
    assert services.approvals.store.get(subject).status == "pending"  # GET cannot silently expire.
    assert await services.approvals.expire_due(limit=1000) >= 1
    result = services.approvals.store.get(subject)
    assert result.status == "expired" and result.decision_event["data"]["decision"] == "expired"
    assert result.operator_id == "system:approval-expiry" and result.assignment_revision == 2
    assert services.approvals.protected_store.inspect(subject)[0] == original
    before = snapshot(services, subject)
    await services.approvals.expire_due(limit=1000)
    with pytest.raises(ApprovalConflict):
        await services.approvals.decide(
            subject,
            decision="approved",
            operator_id=principals["a"],
            value=None,
            revision=1,
            operation_key=str(uuid4()),
            actor=actor(services, "a"),
        )
    assert snapshot(services, subject) == before
    services.verify_protected_approvals(handle)


@pytest.mark.asyncio
async def test_protected_api_requires_real_actor_beside_global_server_authentication(
    approval_runtime, monkeypatch
):
    services, config, principals = approval_runtime
    subject, _, _ = await seed(approval_runtime)
    app = create_app(config=config, services=services, engine=PortableWorkflowEngine())
    before = snapshot(services, subject)
    base = {"Authorization": "Bearer api-controlled-token", "Idempotency-Key": str(uuid4())}
    invalid = [
        base,
        {**base, "X-Approval-Authorization": "Bearer wrong"},
        {
            **base,
            "X-Approval-Authorization": "Bearer a-controlled-token",
            "X-Operator-Id": principals["b"],
        },
        {**base, "X-Approval-Authorization": "Bearer outsider-controlled-token"},
    ]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for headers in invalid:
            denied = await client.post(
                f"/v1/approvals/{subject}/decision",
                headers=headers,
                json={"decision": "approved", "value": None, "revision": 1},
            )
            assert denied.status_code == 403
            assert snapshot(services, subject) == before
        with pytest.raises(ApprovalAuthorizationError):
            services.approvals.authorize_actor("Bearer π")
        with monkeypatch.context() as patch:
            patch.setenv("OWA_APPROVAL_B", "a-controlled-token")
            denied = await client.get(
                f"/v1/approvals/{subject}",
                headers={**base, "X-Approval-Authorization": "Bearer a-controlled-token"},
            )
            assert denied.status_code == 403 and snapshot(services, subject) == before
        global_denied = await client.post(
            f"/v1/approvals/{subject}/decision",
            headers={
                "X-Approval-Authorization": "Bearer a-controlled-token",
                "Idempotency-Key": str(uuid4()),
            },
            json={"decision": "approved", "value": None, "revision": 1},
        )
        assert global_denied.status_code == 401 and snapshot(services, subject) == before
        foreign_get = await client.get(
            f"/v1/approvals/{subject}",
            headers={**base, "X-Approval-Authorization": "Bearer outsider-controlled-token"},
        )
        assert foreign_get.status_code == 403 and snapshot(services, subject) == before
        permitted = await client.post(
            f"/v1/approvals/{subject}/decision",
            headers={
                **base,
                "X-Approval-Authorization": "Bearer a-controlled-token",
                "Idempotency-Key": str(uuid4()),
            },
            json={"decision": "approved", "value": None, "revision": 1},
        )
        assert permitted.status_code == 200 and permitted.json()["operator_id"] == principals["a"]
        assert "controlled-token" not in permitted.text


@pytest.mark.asyncio
async def test_protected_approval_api_scopes_actor_lists_and_filters_status(approval_runtime):
    services, config, principals = approval_runtime
    assigned_a, _, _ = await seed(approval_runtime, assignee="a")
    assigned_b, _, _ = await seed(approval_runtime, assignee="b")
    app = create_app(config=config, services=services, engine=PortableWorkflowEngine())
    base = {"Authorization": "Bearer api-controlled-token"}
    actor_a = {**base, "X-Approval-Authorization": "Bearer a-controlled-token"}
    actor_b = {**base, "X-Approval-Authorization": "Bearer b-controlled-token"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        listed_a = await client.get("/v1/approvals", headers=actor_a)
        listed_b = await client.get("/v1/approvals", headers=actor_b)
        assert listed_a.status_code == listed_b.status_code == 200
        assert {item["approval_id"] for item in listed_a.json()} == {assigned_a}
        assert {item["approval_id"] for item in listed_b.json()} == {assigned_b}

        pending_a = await client.get("/v1/approvals?status=pending", headers=actor_a)
        approved_a = await client.get("/v1/approvals?status=approved", headers=actor_a)
        assert {item["approval_id"] for item in pending_a.json()} == {assigned_a}
        assert approved_a.json() == []

        decision = await client.post(
            f"/v1/approvals/{assigned_b}/decision",
            headers={**actor_b, "Idempotency-Key": str(uuid4())},
            json={"decision": "approved", "value": None, "revision": 1},
        )
        assert decision.status_code == 200
        pending_b = await client.get("/v1/approvals?status=pending", headers=actor_b)
        approved_b = await client.get("/v1/approvals?status=approved", headers=actor_b)
        assert pending_b.json() == []
        assert [item["approval_id"] for item in approved_b.json()] == [assigned_b]
        assert decision.json()["operator_id"] == principals["b"]

        invalid_status = await client.get("/v1/approvals?status=unknown", headers=actor_a)
        assert invalid_status.status_code == 400
        assert invalid_status.json() == {
            "error": {
                "code": "approval_validation_error",
                "message": "unsupported approval status",
                "details": {},
            }
        }


@pytest.mark.asyncio
async def test_protected_approval_assignment_api_replays_and_fences_changes(approval_runtime):
    services, config, principals = approval_runtime
    reassigned, _, _ = await seed(approval_runtime, assignee="a")
    escalated, handle, _ = await seed(approval_runtime, assignee="a")
    app = create_app(config=config, services=services, engine=PortableWorkflowEngine())
    base = {"Authorization": "Bearer api-controlled-token"}
    actor_a = {**base, "X-Approval-Authorization": "Bearer a-controlled-token"}
    actor_b = {**base, "X-Approval-Authorization": "Bearer b-controlled-token"}
    key = str(uuid4())
    reassignment = {
        "assignee": principals["b"],
        "revision": 1,
    }

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        before_denied = snapshot(services, reassigned)
        denied = await client.post(
            f"/v1/approvals/{reassigned}/reassign",
            headers={**actor_b, "Idempotency-Key": str(uuid4())},
            json=reassignment,
        )
        assert denied.status_code == 403
        assert snapshot(services, reassigned) == before_denied

        first = await client.post(
            f"/v1/approvals/{reassigned}/reassign",
            headers={**actor_a, "Idempotency-Key": key},
            json=reassignment,
        )
        assert first.status_code == 200
        assert first.json()["assignee"] == principals["b"]
        assert first.json()["revision"] == 2
        after_first = snapshot(services, reassigned)

        replay = await client.post(
            f"/v1/approvals/{reassigned}/reassign",
            headers={**actor_a, "Idempotency-Key": key},
            json=reassignment,
        )
        assert replay.status_code == 200 and replay.json() == first.json()
        assert snapshot(services, reassigned) == after_first

        stale = await client.post(
            f"/v1/approvals/{reassigned}/reassign",
            headers={**actor_b, "Idempotency-Key": str(uuid4())},
            json=reassignment,
        )
        assert stale.status_code == 409
        assert snapshot(services, reassigned) == after_first

        visible_a = await client.get("/v1/approvals?status=pending", headers=actor_a)
        visible_b = await client.get("/v1/approvals?status=pending", headers=actor_b)
        assert reassigned not in {item["approval_id"] for item in visible_a.json()}
        assert reassigned in {item["approval_id"] for item in visible_b.json()}

        escalated_snapshot = snapshot(services, escalated)
        escalation = await client.post(
            f"/v1/approvals/{escalated}/escalate",
            headers={**actor_a, "Idempotency-Key": str(uuid4())},
            json={"assignee": principals["b"], "revision": 1},
        )
        assert escalation.status_code == 200
        assert escalation.json()["revision"] == 2
        changed, assignment = services.approvals.protected_store.inspect(escalated)
        assert assignment[1] == principals["b"]
        escalated_state = snapshot(services, escalated)
        audit = json.loads(escalated_state["audit"][0][3])
        assert audit["request"]["action"] == "escalate"
        assert escalated_state != escalated_snapshot
        services.verify_protected_approvals(handle)


@pytest.mark.asyncio
async def test_independent_policy_or_index_damage_cannot_be_bypassed_by_request_event(
    approval_runtime,
):
    services, _, _ = approval_runtime
    subject, handle, origin = await seed(approval_runtime)
    key = replace(origin, operation=REQUEST_POLICY).key
    connection = services.effect_journal.connection
    for table in ("workflow_effects", "workflow_effect_invocations"):
        saved = connection.execute(f"SELECT * FROM {table} WHERE identity=?", (key,)).fetchone()
        connection.execute(f"DELETE FROM {table} WHERE identity=?", (key,))
        connection.commit()
        before = snapshot(services, subject)
        with pytest.raises(OwaError):
            services.verify_protected_approvals(handle)
        assert snapshot(services, subject) == before
        connection.execute(
            f"INSERT INTO {table} VALUES ({','.join('?' for _ in saved)})", tuple(saved)
        )
        connection.commit()


@pytest.mark.asyncio
async def test_expiry_inbox_backpressure_rolls_back_terminal_audit_and_policy(
    approval_runtime, monkeypatch
):
    services, _, _ = approval_runtime
    subject, _, _ = await seed(approval_runtime, expired=True)
    before = snapshot(services, subject)
    ingest = services.event_store.ingest_in_transaction

    def fail(*args, **kwargs):
        ingest(*args, **kwargs)
        raise RuntimeError("controlled post-ingestion failure")

    with monkeypatch.context() as patch:
        patch.setattr(services.event_store, "ingest_in_transaction", fail)
        with pytest.raises(OwaError):
            await mutate_protected(
                services.approvals,
                subject,
                action="expired",
                actor=Principal("system:approval-expiry"),
                revision=1,
                operation_key="expiry:" + subject,
                original_expiry=True,
            )
    assert snapshot(services, subject) == before
    assert services.approvals.store.get(subject).status == "pending"


@pytest.mark.asyncio
async def test_escalation_keeps_original_expiry_and_immutable_assignment_audit(approval_runtime):
    services, _, principals = approval_runtime
    subject, handle, _ = await seed(approval_runtime)
    original, _ = services.approvals.protected_store.inspect(subject)
    key = str(uuid4())
    changed = await services.approvals.mutate_protected(
        subject,
        action="escalate",
        actor=actor(services, "a"),
        revision=1,
        operation_key=key,
        assignee=principals["b"],
    )
    before = snapshot(services, subject)
    replay = await services.approvals.mutate_protected(
        subject,
        action="escalate",
        actor=actor(services, "a"),
        revision=1,
        operation_key=key,
        assignee=principals["b"],
    )
    assert replay.as_dict() == changed.as_dict() and snapshot(services, subject) == before
    assert changed.expires_at == original["expires_at"] and changed.assignment_revision == 2
    assert changed.audit[0]["request"]["action"] == "escalate"
    with pytest.raises(ApprovalValidationError):
        await services.approvals.mutate_protected(
            subject,
            action="escalate",
            actor=actor(services, "b"),
            revision=2,
            operation_key=str(uuid4()),
            assignee=principals["outsider"],
        )
    assert snapshot(services, subject) == before
    services.verify_protected_approvals(handle)


@pytest.mark.asyncio
async def test_two_actual_database_workers_preserve_only_first_terminal_decision(approval_runtime):
    services, config, principals = approval_runtime
    subject, handle, _ = await seed(approval_runtime)
    contenders = [
        RuntimeServices(config, model=FakeModel(), database_root=services.database_root)
        for _ in range(2)
    ]
    barrier = threading.Barrier(2, timeout=5)

    def compete(contender, decision):
        principal = actor(contender, "a")
        barrier.wait()
        try:
            return asyncio.run(
                contender.approvals.decide(
                    subject,
                    decision=decision,
                    operator_id=principals["a"],
                    value=None,
                    operation_key=str(uuid4()),
                    revision=1,
                    actor=principal,
                )
            )
        except ApprovalConflict as error:
            return error

    try:
        async with asyncio.timeout(10):
            results = await asyncio.gather(
                *(
                    asyncio.to_thread(compete, contender, decision)
                    for contender, decision in zip(
                        contenders, ("approved", "rejected"), strict=True
                    )
                )
            )
        assert sum(isinstance(result, ApprovalConflict) for result in results) == 1
        winner = next(result for result in results if not isinstance(result, ApprovalConflict))
        retained = services.approvals.store.get(subject)
        assert retained.as_dict() == winner.as_dict() and retained.assignment_revision == 2
        assert len(retained.audit) == 1
        events = services.event_store.connection.execute(
            "SELECT envelope FROM event_inbox WHERE scope=? AND event_id=?",
            (principals["requester"], retained.decision_operation_key),
        ).fetchall()
        assert len(events) == 1 and json.loads(events[0][0]) == retained.decision_event
        services.verify_protected_approvals(handle)
        assert services.model.calls == [] and all(
            contender.model.calls == [] for contender in contenders
        )
    finally:
        for contender in contenders:
            contender.close()


@pytest.mark.asyncio
async def test_disabled_or_unowned_protected_request_does_not_commit_projection_or_inbox(
    approval_runtime, monkeypatch
):
    services, _, principals = approval_runtime
    subject, handle, origin = await seed(approval_runtime)
    event = copy.deepcopy(services.approvals.store.get(subject).request_event)
    event["id"] = event["subject"] = str(uuid4())
    before = snapshot(services, subject)
    with pytest.raises(ApprovalValidationError):
        await services.event_bus.publish_scoped(
            event, default_source="urn:core:approval", scope=principals["requester"]
        )
    with monkeypatch.context() as patch:
        patch.setattr(services.approvals.store, "protected", None)
        identity = replace(
            origin,
            occurrence=((origin.task_reference, 2), (origin.task_reference + "/@attempt", 1)),
        )
        with pytest.raises(ApprovalValidationError):
            await services.event_bus.publish_workflow_event(
                identity,
                effect_request_digest("emit", event),
                event,
                default_source="urn:core:approval",
                scope=principals["requester"],
                execution_lease=handle._execution_lease,
            )
    assert snapshot(services, subject) == before
    assert services.approvals.store.get(event["id"]) is None
    assert services.model.calls == []


def test_protected_policy_rejects_unsupported_schema_actors_and_non_utc_expiry(approval_runtime):
    _, config, principals = approval_runtime
    policy = {
        "profile": PROTECTED_APPROVAL_PROFILE,
        "assignee": principals["a"],
        "reassignmentTargets": [principals["b"]],
        "escalationTargets": [],
    }
    for updates in (
        {"responseSchema": None},
        {"responseSchema": {"resource": "https://untrusted.test/schema"}},
        {"assignee": "system:approval-expiry"},
        {"reassignmentTargets": [principals["b"], principals["b"]]},
        {"undeclared": True},
    ):
        with pytest.raises(ApprovalValidationError):
            ProtectedApprovalPolicy.from_value({**policy, **updates})
    for invalid in (
        "2030-01-01",
        "2030-01-01 00:00:00Z",
        "2030-01-01T00:00:00+00:00",
        "2030-01-01T00:00:00+01:00",
    ):
        with pytest.raises(ApprovalValidationError):
            original_expiry(invalid)
    source = config.model_dump(mode="json", exclude_unset=True)
    for mutate in ("missing-principal", "same-principal", "missing-actors"):
        changed = copy.deepcopy(source)
        if mutate == "missing-principal":
            del changed["security"]["profiles"]["a"]["principal"]
        elif mutate == "same-principal":
            changed["security"]["profiles"]["b"]["principal"] = principals["a"]
        else:
            changed["approvals"]["actor_security_profiles"] = []
        with pytest.raises(ValueError):
            RuntimeConfig.model_validate(changed)


@pytest.mark.asyncio
async def test_deleted_protected_request_never_downgrades_get_or_expiry_to_legacy(approval_runtime):
    services, _, _ = approval_runtime
    subject, handle, _ = await seed(approval_runtime, expired=True)
    original, _ = services.approvals.protected_store.inspect(subject)
    protected = services.approvals.protected_store
    saved = protected.connection.execute(
        f"SELECT * FROM {protected.requests} WHERE approval_id=?", (subject,)
    ).fetchone()
    protected.connection.execute(
        f"DELETE FROM {protected.requests} WHERE approval_id=?", (subject,)
    )
    protected.connection.commit()
    before = snapshot(services, subject, original=original)
    try:
        with pytest.raises(OwaError):
            services.approvals.store.get(subject)
        with pytest.raises(OwaError):
            services.verify_protected_approvals(handle)
        services.approvals.store._expire_due()
        assert snapshot(services, subject, original=original) == before
        status = services.approvals.store._execute(
            "SELECT status FROM approvals WHERE approval_id=?", (subject,)
        ).fetchone()[0]
        assert status == "pending"
    finally:
        protected.connection.execute(
            f"INSERT INTO {protected.requests} VALUES({','.join('?' for _ in saved)})", tuple(saved)
        )
        protected.connection.commit()


@pytest.mark.asyncio
async def test_request_routing_and_terminal_actor_projections_are_independently_bound(
    approval_runtime,
):
    services, _, principals = approval_runtime
    subject, handle, _ = await seed(approval_runtime)
    await services.approvals.decide(
        subject,
        decision="approved",
        operator_id=principals["a"],
        value=None,
        revision=1,
        operation_key=str(uuid4()),
        actor=actor(services, "a"),
    )
    protected = services.approvals.protected_store
    connection = protected.connection
    for table, column, bad in (
        (protected.requests, "invocation_id", str(uuid4())),
        (protected.requests, "fingerprint", "0" * 64),
        (services.approvals.store._table, "operator_id", principals["b"]),
        (services.approvals.store._table, "decision_operation_key", str(uuid4())),
        (services.approvals.store._table, "decided_at", 1.0),
        (protected.audit, "operation_key", str(uuid4())),
    ):
        original = connection.execute(
            f"SELECT {column} FROM {table} WHERE approval_id=?", (subject,)
        ).fetchone()[0]
        connection.execute(f"UPDATE {table} SET {column}=? WHERE approval_id=?", (bad, subject))
        connection.commit()
        before = snapshot(services, subject)
        try:
            with pytest.raises(OwaError):
                services.approvals.store.get(subject)
            with pytest.raises(OwaError):
                services.verify_protected_approvals(handle)
            assert snapshot(services, subject) == before
        finally:
            connection.execute(
                f"UPDATE {table} SET {column}=? WHERE approval_id=?", (original, subject)
            )
            connection.commit()
    services.verify_protected_approvals(handle)


@pytest.mark.asyncio
async def test_poisoned_due_page_isolated_without_starving_later_original_deadlines(
    approval_runtime,
):
    services, _, _ = approval_runtime
    poisoned, _, origin = await seed(approval_runtime, expired=True)
    good, handle, _ = await seed(approval_runtime, expired=True)
    key = replace(origin, operation=REQUEST_POLICY).key
    connection = services.effect_journal.connection
    saved = connection.execute("SELECT * FROM workflow_effects WHERE identity=?", (key,)).fetchone()
    connection.execute("DELETE FROM workflow_effects WHERE identity=?", (key,))
    connection.commit()
    before = snapshot(services, poisoned)
    try:
        for _ in range(
            1000
        ):  # Each observation retains scan_limit=1, including shared PG neighbors.
            await services.approvals.expire_due(limit=1)
            if services.approvals.store.get(good).status == "expired":
                break
        else:
            pytest.fail("bounded fair expiry cycle did not reach the valid original deadline")
        assert "workflow_execution_error" in services.approvals._expiry_errors
        assert snapshot(services, poisoned) == before
        assert (
            services.approvals.store._execute(
                "SELECT status FROM approvals WHERE approval_id=?", (poisoned,)
            ).fetchone()[0]
            == "pending"
        )
        services.verify_protected_approvals(handle)
    finally:
        connection.execute(
            f"INSERT INTO workflow_effects VALUES ({','.join('?' for _ in saved)})", tuple(saved)
        )
        connection.commit()


@pytest.mark.asyncio
async def test_private_history_limits_reject_before_proof_traversal_or_state_writes(
    approval_runtime, monkeypatch
):
    from open_workflow_agent import protected_approval_store as proof

    services, _, _ = approval_runtime
    subject, handle, _ = await seed(approval_runtime)
    before = snapshot(services, subject)
    for name, value in (
        ("MAX_INVOCATION_ROWS", 1),
        ("MAX_INVOCATION_INDEX_BYTES", 1),
        ("MAX_INDEX_PAYLOAD_BYTES", 1),
    ):
        with monkeypatch.context() as patch:
            patch.setattr(proof, name, value)
            with pytest.raises(OwaError):
                services.verify_protected_approvals(handle)
            assert snapshot(services, subject) == before
    original, _ = services.approvals.protected_store.inspect(subject)
    assert original["history_limits"] == {
        "rows": 10_000,
        "indexBytes": 8_388_608,
        "indexPayloadBytes": 65_536,
    }
    services.verify_protected_approvals(handle)


@pytest.mark.asyncio
async def test_protected_business_receipt_is_verified_before_reuse(approval_runtime):
    services, _, _ = approval_runtime
    subject, handle, identity = await seed(approval_runtime)
    receipt = services.effect_journal.get(identity)
    assert receipt is not None and receipt.output["subject"] == subject

    verify_protected_approval_business_receipt(
        services.effect_journal.connection, services.invocations, identity, receipt
    )

    # A source rewrite keeps the shape of an event but breaks the independent
    # protected request's origin proof. It must fail closed before reuse.
    tampered = copy.deepcopy(receipt.output)
    tampered["source"] = "urn:attacker:forged"
    with pytest.raises(WorkflowExecutionError, match="protected approval business evidence"):
        verify_protected_approval_business_receipt(
            services.effect_journal.connection,
            services.invocations,
            identity,
            replace(receipt, output=tampered),
        )

    # The stored invocation binding also prevents replay under another workflow.
    with pytest.raises(WorkflowExecutionError, match="protected approval business evidence"):
        verify_protected_approval_business_receipt(
            services.effect_journal.connection,
            services.invocations,
            replace(identity, workflow_fingerprint="0" * 64),
            receipt,
        )
    assert services.invocations.get(handle.invocation_id) is not None


@pytest.mark.asyncio
async def test_actor_decision_uses_atomic_protected_path_without_preflight_read(
    approval_runtime, monkeypatch
):
    services, _, _ = approval_runtime
    subject, _, _ = await seed(approval_runtime)
    principal = services.approvals.authorize_actor("Bearer a-controlled-token")
    original_get = services.approvals.store.get
    observed_statuses: list[str] = []

    def track_status(approval_id: str, *, commit: bool = True):
        record = original_get(approval_id, commit=commit)
        if record is not None:
            observed_statuses.append(record.status)
        return record

    monkeypatch.setattr(services.approvals.store, "get", track_status)
    await services.approvals.decide(
        subject,
        decision="approved",
        operator_id=principal.identity,
        actor=principal,
        revision=1,
        operation_key="atomic-protected-decision",
        value={"allowed": True},
    )

    # The only common projection read occurs after the proof-checked transaction
    # commits; there is no multi-query preflight that can observe a mixed race.
    assert observed_statuses == ["approved"]


@pytest.mark.asyncio
async def test_actor_decision_rejects_legacy_and_unknown_approval_ids(approval_runtime):
    services, _, _ = approval_runtime
    legacy_id = "legacy-actor-denied"
    services.approvals.store.create_request(
        EventEnvelope(
            id=legacy_id,
            source="urn:test",
            type="io.openworkflow.agent.approval.requested",
            time=datetime.now(UTC).isoformat(),
            subject=legacy_id,
        )
    )
    actor = Principal("reviewer-a")

    for approval_id in (legacy_id, "unknown-actor-denied"):
        with pytest.raises(ApprovalAuthorizationError):
            await services.approvals.decide(
                approval_id,
                decision="approved",
                operator_id=actor.identity,
                actor=actor,
                revision=1,
                operation_key="actor-denied-" + approval_id,
                value={"approved": True},
            )


@pytest.mark.asyncio
async def test_approval_event_retention_keeps_independent_request_evidence(approval_runtime):
    services, _, _ = approval_runtime
    subject, handle, identity = await seed(approval_runtime)
    receipt = services.effect_journal.get(identity)
    assert receipt is not None
    original = receipt.output
    protected = EventEnvelope(
        id=original["id"],
        source=original["source"],
        type=original["type"],
        time=original["time"],
        subject=subject,
        data=original["data"],
        data_present=True,
    )
    connection = services.effect_journal.connection
    assert protected_approval_event_retained(connection, protected)

    # Even if an event loses the embedded policy hint, cleanup must preserve it
    # while the private request proof remains. Otherwise corruption could make
    # a control event appear to be ordinary, prunable history.
    stripped = replace(protected, data={"question": "continue?"})
    assert protected_approval_event_retained(connection, stripped)
    assert protected_approval_invocation_retained(connection, handle.invocation_id)
    assert not protected_approval_event_retained(
        connection, replace(protected, type="io.example.message")
    )


def test_approval_retention_is_false_when_private_schema_is_absent():
    connection = StorageConnection(sqlite3.connect(":memory:"), "sqlite")
    try:
        event = EventEnvelope(
            id="orphan",
            source="urn:test",
            type="io.openworkflow.agent.approval.requested",
            time="2026-01-01T00:00:00Z",
            subject="missing",
        )
        assert not protected_approval_event_retained(connection, event)
        assert not protected_approval_invocation_retained(connection, "missing")
    finally:
        connection.close()
