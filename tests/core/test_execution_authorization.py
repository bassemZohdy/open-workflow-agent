"""Independent original authority and finite action audits on real common stores."""

from __future__ import annotations

import json
import os
import time
from dataclasses import replace
from uuid import uuid4

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.effects import EffectIdentity, ReceiptDependency, effect_request_digest
from open_workflow_agent.errors import InvocationStateConflict, OwaError
from open_workflow_agent.execution_authority import ExecutionAuthority, authority_scope
from open_workflow_agent.execution_authorization import (
    AuthorizationOutcomeUnavailable,
    ExecutionAuthorizationStore,
    authority_identity,
    verify_authorized_business_snapshot,
    verify_execution_authorization_business_receipt,
)
from open_workflow_agent.recovery_definitions import PROFILE, RecoveryDefinitionArchive
from open_workflow_agent.security import ExecutionAuthorizationConfig, Principal
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow


@pytest.fixture(params=["sqlite", "postgresql"])
def authority_tree(tmp_path, request):
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if request.param == "postgresql" else None
    if request.param == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    config = RuntimeConfig.model_validate(
        {
            "persistence": {"datasource": datasource},
            "security": {
                "profiles": {
                    "ingress": {
                        "type": "bearer",
                        "token": {"from_env": "OWA_AUTHORITY_UNUSED"},
                        "principal": "alice",
                        "roles": ["operator"],
                        "scopes": ["execute"],
                        "audience": ["runtime"],
                    }
                }
            },
            "execution_authorization": {
                "enabled": True,
                "id": "fixture",
                "version": "1",
                "policy": {
                    "rules": [
                        {
                            "actions": ["protocol.call", "tool.invoke"],
                            "resources": ["http:https://peer.test/action", "safe-tool"],
                            "roles": ["operator"],
                            "scopes": ["execute"],
                            "audience": ["runtime"],
                        }
                    ]
                },
            },
        }
    )
    services = RuntimeServices(
        config, model=FakeModel(), database_root=None if datasource else tmp_path
    )
    assert services.effect_journal.connection.kind == request.param
    archive = RecoveryDefinitionArchive(
        services.effect_journal,
        services.invocations,
        profile_provider=lambda engine: {
            "profile": PROFILE,
            "engine": engine,
            "backend": request.param,
            "continuation_supported": True,
        },
    )
    store = ExecutionAuthorizationStore(
        services.effect_journal,
        services.invocations,
        archive,
        config_provider=lambda: config.execution_authorization,
        security_provider=lambda: config.security,
    )
    archive.admission_participant = store.admission_participant
    archive.authority_expectation = store.expected_authority_digest
    archive.authority_verifier = store.verify_invocation
    services.effect_journal._execution_authorization_seal = store.seal_business
    services.effect_journal._execution_authorization_intent = store.seal_intent
    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "test",
                "name": "authority-" + uuid4().hex,
                "version": "1.0.0",
            },
            "do": [
                {
                    "action": {
                        "call": "http",
                        "with": {"method": "POST", "endpoint": "https://peer.test/action"},
                    }
                }
            ],
        }
    )
    handle = services.invocations.create(
        engine="reference",
        session_id=None,
        user_id="correlation-only",
        owner_principal="alice",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    authority = ExecutionAuthority(
        Principal("alice", frozenset({"operator"}), frozenset({"execute"}), frozenset({"runtime"})),
        "ingress",
    )
    lease = services.execution_ownership.acquire(handle, seconds=300)
    handle._execution_lease = lease
    identity = EffectIdentity(
        handle.invocation_id, plan.fingerprint, "/do/0/action", (("/do/0/action", 1),), "call:http"
    )
    services._authority_fixture_invocations = [handle.invocation_id]
    services._authority_fixture_schedules = []
    try:
        yield services, archive, store, plan, handle, authority, identity
    finally:
        try:
            services.execution_ownership.release(lease)
        except InvocationStateConflict:
            pass  # The expiry/takeover cases already deliberately released this generation.
        # Keep corrupt proof evidence, but settle only this fixture's queued
        # work so a later PostgreSQL dispatcher cannot claim its schedule.
        for invocation_id in services._authority_fixture_invocations:
            current = services.invocations.get(invocation_id)
            if current is not None and current.status not in {"completed", "faulted", "cancelled"}:
                services.invocations.cancel_authoritatively(
                    current,
                    error={
                        "code": "invocation_cancelled",
                        "message": "fixture cleanup",
                        "details": {},
                    },
                )
        for schedule_id in services._authority_fixture_schedules:
            connection = services.schedules.connection
            row = connection.execute(
                "SELECT payload FROM schedules WHERE schedule_id=?", (schedule_id,)
            ).fetchone()
            if row is not None:
                value = json.loads(row[0])
                value["status"] = "cancelled"
                connection.execute(
                    "UPDATE schedules SET payload=?,status='cancelled',lease_until=NULL "
                    "WHERE schedule_id=?",
                    (json.dumps(value), schedule_id),
                )
                connection.execute(
                    "UPDATE schedule_slots SET state='cancelled',owner='',lease_until=NULL "
                    "WHERE schedule_id=?",
                    (schedule_id,),
                )
                connection.commit()
        services.close()


def _admit(tree, value=None, *, reserved=False):
    _, archive, store, plan, handle, authority, _ = tree
    if reserved:
        store.reserve_invocation(handle, authority, input_data=value)
    with authority_scope(authority):
        archive.admit(plan, handle, value, execution_lease=handle._execution_lease)


def _decision(tree, *, action="protocol.call", resource="http:https://peer.test/action"):
    _, _, store, _, handle, _, identity = tree
    return store.authorize(
        handle,
        identity,
        action,
        resource,
        effect_request_digest("permission", [action, resource]),
        execution_lease=handle._execution_lease,
    )


def _finish(tree, output):
    services, _, _, _, handle, _, identity = tree
    services.effect_journal.begin(
        identity,
        effect_request_digest("http", {"original": True}),
        "first",
        execution_lease=handle._execution_lease,
    )
    return services.effect_journal.finish_owned(
        identity, "first", output=output, execution_lease=handle._execution_lease
    )


def _snapshot(tree):
    services, _, _, _, handle, _, _ = tree
    connection = services.effect_journal.connection
    rows = tuple(
        (
            table,
            tuple(
                connection.execute(
                    f"SELECT * FROM {table} WHERE {column}=? ORDER BY 1", (handle.invocation_id,)
                ).fetchall()
            ),
        )
        for table, column in [
            ("execution_authorities", "invocation_id"),
            ("recovery_invocation_definitions", "invocation_id"),
            ("invocation_inputs", "invocation_id"),
            ("invocation_ownership", "invocation_id"),
            ("execution_authorization_decisions", "invocation_id"),
            ("execution_authorization_outcomes", "invocation_id"),
            ("execution_authorization_intents", "invocation_id"),
            ("workflow_effect_invocations", "invocation_id"),
        ]
    )
    receipts = tuple(
        connection.execute(
            "SELECT e.* FROM workflow_effects e JOIN workflow_effect_invocations i "
            "ON i.identity=e.identity WHERE i.invocation_id=? ORDER BY e.identity",
            (handle.invocation_id,),
        ).fetchall()
    )
    candidates = tuple(
        connection.execute(
            "SELECT * FROM execution_authority_candidates WHERE candidate_key=?",
            ("invocation:" + handle.invocation_id,),
        ).fetchall()
    )
    return rows, receipts, candidates


@pytest.mark.parametrize("original", [None, {"original": [1, False]}], ids=["null", "value"])
def test_actual_claims_and_original_input_remain_immutable_after_ingress_scope_ends(
    authority_tree, original
):
    _, archive, store, _, handle, authority, _ = authority_tree
    _admit(authority_tree, original, reserved=True)
    before = _snapshot(authority_tree)
    with authority_scope(ExecutionAuthority(Principal("bob"), "untrusted")):
        assert store.verify_invocation(handle) == authority
        assert archive.resolve(handle).fingerprint == handle.workflow_fingerprint
    assert store.authority_receipt(handle).receipt.status == "completed"
    assert _snapshot(authority_tree) == before


@pytest.mark.parametrize(
    "output", [None, {"value": "done"}, "x" * 300_000], ids=["null", "value", "large-valid"]
)
def test_known_business_outcome_is_sealed_with_exact_permission_dependencies(
    authority_tree, output
):
    services, _, store, _, handle, _, identity = authority_tree
    _admit(authority_tree)
    first = _decision(authority_tree)
    second = _decision(authority_tree, action="tool.invoke", resource="safe-tool")
    receipt = _finish(authority_tree, output)
    assert receipt.status == "completed" and receipt.output == output
    assert first.identity != second.identity and store.has_protected_business(identity)
    before = _snapshot(authority_tree)
    ReceiptDependency.capture(identity, receipt).verify(services.effect_journal)
    assert store.verify_invocation(handle).principal.identity == "alice"
    assert _snapshot(authority_tree) == before and services.model.calls == []


@pytest.mark.parametrize(
    "corruption",
    [
        "authority",
        "authority-receipt",
        "authority-index",
        "candidate",
        "binding-marker",
        "all-authority",
    ],
    ids=["authority", "receipt", "index", "candidate", "marker", "all"],
)
def test_missing_original_authority_never_adopts_current_ingress(authority_tree, corruption):
    services, archive, store, _, handle, authority, _ = authority_tree
    _admit(authority_tree, reserved=True)
    connection = services.effect_journal.connection
    key = authority_identity(handle).key
    if corruption in {"authority", "all-authority"}:
        connection.execute(
            "DELETE FROM execution_authorities WHERE invocation_id=?", (handle.invocation_id,)
        )
    if corruption in {"candidate", "all-authority"}:
        connection.execute(
            "DELETE FROM execution_authority_candidates WHERE candidate_key=?",
            ("invocation:" + handle.invocation_id,),
        )
    if corruption in {"authority-receipt", "all-authority"}:
        connection.execute("DELETE FROM workflow_effects WHERE identity=?", (key,))
    if corruption in {"authority-index", "all-authority"}:
        connection.execute("DELETE FROM workflow_effect_invocations WHERE identity=?", (key,))
    if corruption == "binding-marker":
        row = connection.execute(
            "SELECT payload FROM recovery_invocation_definitions WHERE invocation_id=?",
            (handle.invocation_id,),
        ).fetchone()
        value = json.loads(row[0])
        value.pop("execution_authority_digest")
        connection.execute(
            "UPDATE recovery_invocation_definitions SET payload=? WHERE invocation_id=?",
            (json.dumps(value), handle.invocation_id),
        )
    connection.commit()
    before = _snapshot(authority_tree)
    with authority_scope(authority), pytest.raises(OwaError):
        archive.resolve(handle)
    with pytest.raises(OwaError):
        store.verify_invocation(handle)
    assert _snapshot(authority_tree) == before and services.model.calls == []


@pytest.mark.parametrize(
    "corruption",
    [
        "decision",
        "decision-receipt",
        "decision-index",
        "seal",
        "seal-receipt",
        "business",
        "request",
        "business-and-index",
    ],
    ids=[
        "decision",
        "receipt",
        "index",
        "seal",
        "seal-receipt",
        "business",
        "request",
        "business-and-index",
    ],
)
def test_known_native_business_dependency_rejects_omitted_or_changed_audit_evidence(
    authority_tree, corruption
):
    services, _, store, _, handle, _, identity = authority_tree
    _admit(authority_tree)
    decision = _decision(authority_tree)
    receipt = _finish(authority_tree, {"original": True})
    connection = services.effect_journal.connection
    if corruption == "decision":
        connection.execute(
            "DELETE FROM execution_authorization_decisions WHERE decision_key=?",
            (decision.identity.key,),
        )
    elif corruption in {"decision-receipt", "decision-index"}:
        table = (
            "workflow_effects"
            if corruption == "decision-receipt"
            else "workflow_effect_invocations"
        )
        connection.execute(f"DELETE FROM {table} WHERE identity=?", (decision.identity.key,))
    elif corruption == "seal":
        connection.execute(
            "DELETE FROM execution_authorization_outcomes WHERE business_key=?", (identity.key,)
        )
    elif corruption == "seal-receipt":
        connection.execute(
            "DELETE FROM workflow_effects WHERE identity=?",
            (replace(identity, operation="policy:execution-authorization-seal:v1").key,),
        )
    elif corruption == "business":
        connection.execute(
            "UPDATE workflow_effects SET payload=? WHERE identity=?", ("{}", identity.key)
        )
    elif corruption == "business-and-index":
        connection.execute("DELETE FROM workflow_effects WHERE identity=?", (identity.key,))
        connection.execute(
            "DELETE FROM workflow_effect_invocations WHERE identity=?", (identity.key,)
        )
    else:
        # Change a still-well-formed decision and its completed policy receipt.
        # The independently retained business seal must detect the changed
        # permission dependency even when the native lane has only business.
        import open_workflow_agent.execution_authorization as module
        from open_workflow_agent.effects import _receipt_payload

        row = connection.execute(
            "SELECT payload FROM execution_authorization_decisions WHERE decision_key=?",
            (decision.identity.key,),
        ).fetchone()
        value = json.loads(row[0])
        value["request_digest"] = "f" * 64
        connection.execute(
            "UPDATE execution_authorization_decisions SET payload=?,digest=? WHERE decision_key=?",
            (module._encoded(value), module._digest(value), decision.identity.key),
        )
        connection.execute(
            "UPDATE workflow_effects SET payload=? WHERE identity=?",
            (json.dumps(_receipt_payload(module._decision_receipt(value))), decision.identity.key),
        )
    connection.commit()
    before = _snapshot(authority_tree)
    with pytest.raises(OwaError):
        ReceiptDependency.capture(identity, receipt).verify(services.effect_journal)
    with pytest.raises(OwaError):
        store.verify_invocation(handle)
    assert _snapshot(authority_tree) == before


@pytest.mark.parametrize(
    "change", ["profile-claims", "policy-version", "policy-disabled", "policy-rule"]
)
def test_current_trust_revocation_rejects_before_lease_or_action_mutation(authority_tree, change):
    services, archive, store, _, handle, _, _ = authority_tree
    _admit(authority_tree)
    if change == "profile-claims":
        services.config.security.profiles["ingress"].roles = ["other"]
    else:
        value = services.config.execution_authorization.model_dump(mode="json")
        if change == "policy-version":
            value["version"] = "2"
        elif change == "policy-disabled":
            value["enabled"] = False
        else:
            value["policy"]["rules"] = []
        services.config.execution_authorization = ExecutionAuthorizationConfig.model_validate(value)
    before = _snapshot(authority_tree)
    with pytest.raises(OwaError):
        archive.resolve(handle)
    with pytest.raises(OwaError):
        _decision(authority_tree)
    assert _snapshot(authority_tree) == before and services.model.calls == []


def test_policyoff_authenticated_identity_keeps_legacy_late_known_reply_contract(authority_tree):
    services, _, store, _, handle, authority, identity = authority_tree
    services.config.execution_authorization.enabled = False
    _admit(authority_tree)
    assert store.verify_invocation(handle) == authority
    assert not store.has_protected_business(identity) and _decision(authority_tree) is None
    services.effect_journal.begin(
        identity, "a" * 64, "first", execution_lease=handle._execution_lease
    )
    services.execution_ownership.release(handle._execution_lease)
    receipt = services.effect_journal.finish(identity, "first", output=None)
    assert receipt.status == "completed"
    verify_execution_authorization_business_receipt(
        services.effect_journal.connection, services.invocations, identity, receipt
    )
    assert store.verify_invocation(handle) == authority


def test_action_decision_and_outcome_reject_stale_generation_without_adopting_intent(
    authority_tree,
):
    services, _, store, _, handle, _, identity = authority_tree
    _admit(authority_tree)
    _decision(authority_tree)
    services.effect_journal.begin(
        identity, "a" * 64, "first", execution_lease=handle._execution_lease
    )
    old = handle._execution_lease
    services.execution_ownership.release(old)
    takeover = services.execution_ownership.acquire(handle, seconds=300)
    before = _snapshot(authority_tree)
    try:
        with pytest.raises(InvocationStateConflict):
            _decision(authority_tree)
        with pytest.raises(InvocationStateConflict):
            services.effect_journal.finish_owned(
                identity, "first", output="late", execution_lease=old
            )
        assert services.effect_journal.get(identity).status == "pending"
        assert _snapshot(authority_tree) == before and takeover.generation > old.generation
    finally:
        services.execution_ownership.release(takeover)


def test_first_admission_failure_rolls_back_input_archive_authority_and_policy_receipts(
    authority_tree, monkeypatch
):
    services, archive, store, _, handle, _, _ = authority_tree
    before = _snapshot(authority_tree)
    original = store.admission_participant

    def fail_after_authority(*args):
        original(*args)
        raise RuntimeError("owned admission rollback")

    archive.admission_participant = fail_after_authority
    with pytest.raises((OwaError, RuntimeError)):
        _admit(authority_tree, {"original": True})
    assert _snapshot(authority_tree) == before
    assert not archive.has_binding(handle)
    assert services.model.calls == []


def test_oversize_postdispatch_outcome_keeps_original_pending_intent_and_no_audit_seal(
    authority_tree,
):
    services, _, _, _, handle, _, identity = authority_tree
    services.config.execution_authorization.max_business_receipt_bytes = 1024
    _admit(authority_tree)
    _decision(authority_tree)
    services.effect_journal.begin(
        identity, "a" * 64, "first", execution_lease=handle._execution_lease
    )
    before = _snapshot(authority_tree)
    with pytest.raises(AuthorizationOutcomeUnavailable):
        services.effect_journal.finish_owned(
            identity, "first", output="x" * 1024, execution_lease=handle._execution_lease
        )
    assert services.effect_journal.get(identity).status == "pending"
    assert _snapshot(authority_tree) == before


def test_exact_resource_denial_and_forged_principal_do_not_create_policy_evidence(authority_tree):
    _, _, store, _, handle, _, _ = authority_tree
    _admit(authority_tree)
    before = _snapshot(authority_tree)
    with pytest.raises(OwaError):
        _decision(authority_tree, resource="http:https://attacker.test/action")
    with pytest.raises(OwaError):
        store.authorize_request(
            ExecutionAuthority(Principal("alice", frozenset({"admin"})), "ingress"),
            "protocol.call",
            "http:https://peer.test/action",
        )
    assert _snapshot(authority_tree) == before


@pytest.mark.parametrize(
    "damage",
    [
        None,
        "candidate-before-admission",
        "candidate-after-admission",
        "parent-proof",
        "child-input",
    ],
    ids=["valid", "candidate-initial", "candidate-history", "parent", "input"],
)
def test_real_child_reservation_retains_parent_authority_and_rejects_origin_deletion(
    authority_tree, damage
):
    services, archive, store, _, parent, authority, identity = authority_tree
    _admit(authority_tree)
    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "test",
                "name": "child-" + uuid4().hex,
                "version": "1.0.0",
            },
            "do": [{"done": {"set": {"ok": True}}}],
        }
    )
    services.children.authority_participant = lambda child, root, lease: store.reserve_child(
        child, root, execution_lease=lease
    )
    child = services.children.reserve(
        replace(identity, operation="run:workflow"), parent, plan, None
    )
    services._authority_fixture_invocations.append(child.invocation_id)
    assert store.verify_invocation(child) == authority
    connection = services.effect_journal.connection
    if damage == "candidate-before-admission":
        connection.execute(
            "DELETE FROM execution_authority_candidates WHERE candidate_key=?",
            ("invocation:" + child.invocation_id,),
        )
        connection.commit()
        before = child._persisted_payload
        with authority_scope(authority), pytest.raises(OwaError):
            store.verify_invocation(child)
        assert services.invocations.get(child.invocation_id)._persisted_payload == before
        return
    lease = services.execution_ownership.acquire(child, seconds=300)
    try:
        archive.admit(plan, child, None, execution_lease=lease)
        if damage == "candidate-after-admission":
            connection.execute(
                "DELETE FROM execution_authority_candidates WHERE candidate_key=?",
                ("invocation:" + child.invocation_id,),
            )
        elif damage == "parent-proof":
            connection.execute(
                "DELETE FROM execution_authorities WHERE invocation_id=?", (parent.invocation_id,)
            )
        elif damage == "child-input":
            connection.execute(
                "DELETE FROM invocation_inputs WHERE invocation_id=?", (child.invocation_id,)
            )
        connection.commit()
        before = services.invocations.get(child.invocation_id)._persisted_payload
        if damage is not None:
            with authority_scope(authority), pytest.raises(OwaError):
                archive.resolve(child)
        else:
            assert store.verify_invocation(child) == authority
            assert archive.resolve(child).fingerprint == plan.fingerprint
        assert services.invocations.get(child.invocation_id)._persisted_payload == before
    finally:
        services.execution_ownership.release(lease)


@pytest.mark.parametrize(
    "damage",
    [None, "schedule-candidate", "slot-candidate", "original-input"],
    ids=["valid", "schedule-proof", "slot-proof", "input"],
)
def test_actual_schedule_transaction_preserves_authenticated_origin_without_ingress_context(
    authority_tree, damage
):
    services, archive, store, _, _, authority, _ = authority_tree
    schedule = services.schedules
    schedule.authority_expectation = store.expected_schedule_authority_digest
    schedule.authority_participant = lambda connection, record: store.reserve_schedule(
        record, commit=False, connection=connection
    )
    schedule.authority_verifier = lambda connection, record: store.reserved_schedule(
        record, connection=connection
    )
    schedule.invocation_authority_participant = lambda connection, record, handle, claim: (
        store.reserve_scheduled_invocation(handle, record, claim, connection=connection)
    )
    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "test",
                "name": "scheduled-" + uuid4().hex,
                "version": "1.0.0",
            },
            "schedule": {"after": {"seconds": 1}},
            "do": [{"done": {"set": {"ok": True}}}],
        }
    )
    now = time.time()
    with authority_scope(authority):
        record = schedule.create(plan, None, now=now - 2, owner_principal="alice")
    services._authority_fixture_schedules.append(record.schedule_id)
    claimed = schedule.claim_due(now=now, schedule_id=record.schedule_id, lease_seconds=300)
    assert claimed is not None
    child = schedule.bind_invocation(claimed, engine="reference", now=now)
    services._authority_fixture_invocations.append(child.invocation_id)
    assert store.verify_invocation(child) == authority
    lease = services.execution_ownership.acquire(child, seconds=300)
    try:
        archive.admit(plan, child, None, execution_lease=lease)
        connection = services.effect_journal.connection
        if damage == "schedule-candidate":
            connection.execute(
                "DELETE FROM execution_authority_candidates WHERE candidate_key=?",
                ("schedule:" + record.schedule_id,),
            )
        elif damage == "slot-candidate":
            connection.execute(
                "DELETE FROM execution_authority_candidates WHERE candidate_key=?",
                ("invocation:" + child.invocation_id,),
            )
        elif damage == "original-input":
            connection.execute(
                "DELETE FROM invocation_inputs WHERE invocation_id=?", (child.invocation_id,)
            )
        connection.commit()
        before = services.invocations.get(child.invocation_id)._persisted_payload
        if damage is None:
            assert store.verify_invocation(child) == authority
            assert archive.resolve(child).fingerprint == plan.fingerprint
        else:
            with pytest.raises(OwaError):
                archive.resolve(child)
        assert services.invocations.get(child.invocation_id)._persisted_payload == before
    finally:
        services.execution_ownership.release(lease)


def test_schedule_marker_rejects_candidate_deletion_before_claim_even_policyoff(authority_tree):
    services, _, store, _, _, authority, _ = authority_tree
    services.config.execution_authorization.enabled = False
    schedule = services.schedules
    schedule.authority_expectation = store.expected_schedule_authority_digest
    schedule.authority_participant = lambda connection, record: store.reserve_schedule(
        record, commit=False, connection=connection
    )
    schedule.authority_verifier = lambda connection, record: store.reserved_schedule(
        record, connection=connection
    )
    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "test",
                "name": "queued-" + uuid4().hex,
                "version": "1.0.0",
            },
            "schedule": {"after": {"seconds": 1}},
            "do": [{"done": {"set": {"ok": True}}}],
        }
    )
    now = time.time()
    with authority_scope(authority):
        record = schedule.create(plan, {}, now=now - 2, owner_principal="alice")
    services._authority_fixture_schedules.append(record.schedule_id)
    connection = schedule.connection
    connection.execute(
        "DELETE FROM execution_authority_candidates WHERE candidate_key=?",
        ("schedule:" + record.schedule_id,),
    )
    connection.commit()
    before = tuple(
        connection.execute(
            "SELECT * FROM schedule_slots WHERE schedule_id=?", (record.schedule_id,)
        ).fetchall()
    )
    with authority_scope(authority), pytest.raises(OwaError):
        schedule.claim_due(now=now, schedule_id=record.schedule_id)
    assert (
        tuple(
            connection.execute(
                "SELECT * FROM schedule_slots WHERE schedule_id=?", (record.schedule_id,)
            ).fetchall()
        )
        == before
    )


def test_outcome_seal_insertion_failure_rolls_back_seal_and_retains_pending_business(
    authority_tree, monkeypatch
):
    import open_workflow_agent.execution_authorization as module

    services, _, _, _, handle, _, identity = authority_tree
    _admit(authority_tree)
    _decision(authority_tree)
    services.effect_journal.begin(
        identity, "a" * 64, "first", execution_lease=handle._execution_lease
    )
    before = _snapshot(authority_tree)
    insert = module.insert_completed_receipt

    def fail_seal(connection, selected, receipt):
        insert(connection, selected, receipt)
        if selected.operation == module.SEAL_OPERATION:
            raise RuntimeError("seal insertion interrupted")

    monkeypatch.setattr(module, "insert_completed_receipt", fail_seal)
    with pytest.raises(OwaError):
        services.effect_journal.finish_owned(
            identity, "first", output={"known": True}, execution_lease=handle._execution_lease
        )
    assert (
        _snapshot(authority_tree) == before
        and services.effect_journal.get(identity).status == "pending"
    )


@pytest.mark.parametrize(
    "damage", [None, "authority", "decision"], ids=["valid", "authority", "decision"]
)
def test_independent_supervisor_snapshot_verification_is_select_only(
    authority_tree, monkeypatch, damage
):
    from open_workflow_agent.persistence import InvocationStore

    services, _, _, _, handle, _, identity = authority_tree
    _admit(authority_tree)
    decision = _decision(authority_tree)
    receipt = _finish(authority_tree, None)
    connection = services.effect_journal.connection
    if damage == "authority":
        connection.execute(
            "DELETE FROM execution_authorities WHERE invocation_id=?", (handle.invocation_id,)
        )
    elif damage == "decision":
        connection.execute(
            "DELETE FROM execution_authorization_decisions WHERE decision_key=?",
            (decision.identity.key,),
        )
    connection.commit()
    before = _snapshot(authority_tree)
    execute = type(connection).execute
    statements = []

    def select_only(selected_connection, statement, parameters=()):
        assert statement.lstrip().upper().startswith("SELECT"), statement
        statements.append(statement)
        return execute(selected_connection, statement, parameters)

    def forbidden(*args, **kwargs):
        raise AssertionError("supervisor must not initialize invocation storage")

    monkeypatch.setattr(type(connection), "execute", select_only)
    monkeypatch.setattr(InvocationStore, "__init__", forbidden)
    if damage is None:
        verify_authorized_business_snapshot(connection, identity, receipt)
    else:
        with pytest.raises(OwaError):
            verify_authorized_business_snapshot(connection, identity, receipt)
    assert statements and _snapshot(authority_tree) == before and services.model.calls == []


@pytest.mark.parametrize(
    "table,column,key",
    [
        ("execution_authorities", "invocation_id", "invocation"),
        ("workflow_effect_invocations", "identity", "authority"),
    ],
    ids=["authority-row", "receipt-index"],
)
def test_oversize_independent_proof_is_rejected_without_mutation(
    authority_tree, table, column, key
):
    services, _, store, _, handle, _, _ = authority_tree
    _admit(authority_tree)
    connection = services.effect_journal.connection
    selected = handle.invocation_id if key == "invocation" else authority_identity(handle).key
    connection.execute(
        f"UPDATE {table} SET payload=? WHERE {column}=?", ('"' + "x" * 262_145 + '"', selected)
    )
    connection.commit()
    before = _snapshot(authority_tree)
    with pytest.raises(OwaError):
        store.verify_invocation(handle)
    assert _snapshot(authority_tree) == before


@pytest.mark.parametrize(
    "damage",
    ["marker", "marker-receipt", "marker-index", "business-and-index"],
    ids=["marker", "receipt", "index", "business"],
)
def test_pending_business_original_intent_tombstone_prevents_reissue_after_deletion(
    authority_tree, damage
):
    import open_workflow_agent.execution_authorization as module

    services, archive, store, _, handle, _, identity = authority_tree
    _admit(authority_tree)
    _decision(authority_tree)
    original = services.effect_journal.begin(
        identity, "a" * 64, "first", execution_lease=handle._execution_lease
    )
    connection = services.effect_journal.connection
    marker = module.intent_identity(identity)
    if damage == "marker":
        connection.execute(
            "DELETE FROM execution_authorization_intents WHERE business_key=?", (identity.key,)
        )
    elif damage == "marker-receipt":
        connection.execute("DELETE FROM workflow_effects WHERE identity=?", (marker.key,))
    elif damage == "marker-index":
        connection.execute(
            "DELETE FROM workflow_effect_invocations WHERE identity=?", (marker.key,)
        )
    else:
        connection.execute("DELETE FROM workflow_effects WHERE identity=?", (identity.key,))
        connection.execute(
            "DELETE FROM workflow_effect_invocations WHERE identity=?", (identity.key,)
        )
    connection.commit()
    before = _snapshot(authority_tree)
    with pytest.raises(OwaError):
        archive.resolve(handle)
    with pytest.raises(OwaError):
        store.verify_invocation(handle)
    with pytest.raises(OwaError):
        services.effect_journal.begin(
            identity, original.request_digest, "second", execution_lease=handle._execution_lease
        )
    assert _snapshot(authority_tree) == before and services.model.calls == []


def test_intent_and_companion_failure_roll_back_before_business_dispatch(
    authority_tree, monkeypatch
):
    import open_workflow_agent.execution_authorization as module

    services, _, _, _, handle, _, identity = authority_tree
    _admit(authority_tree)
    _decision(authority_tree)
    before = _snapshot(authority_tree)
    insert = module.insert_completed_receipt

    def fail_intent(connection, selected, receipt):
        insert(connection, selected, receipt)
        if selected.operation.startswith(module.INTENT_OPERATION + ":"):
            raise RuntimeError("intent companion interrupted")

    monkeypatch.setattr(module, "insert_completed_receipt", fail_intent)
    with pytest.raises(OwaError):
        services.effect_journal.begin(
            identity, "a" * 64, "first", execution_lease=handle._execution_lease
        )
    assert _snapshot(authority_tree) == before and services.effect_journal.get(identity) is None


def test_permission_before_intent_is_valid_but_missing_existing_marker_is_not_retrofitted(
    authority_tree,
):

    services, archive, store, _, handle, authority, identity = authority_tree
    _admit(authority_tree)
    _decision(authority_tree)
    assert services.effect_journal.get(identity) is None
    assert store.verify_invocation(handle) == authority
    archive.resolve(handle)
    original = services.effect_journal.begin(
        identity, "a" * 64, "first", execution_lease=handle._execution_lease
    )
    connection = services.effect_journal.connection
    connection.execute(
        "DELETE FROM execution_authorization_intents WHERE business_key=?", (identity.key,)
    )
    connection.commit()
    before = _snapshot(authority_tree)
    with pytest.raises(OwaError):
        store.seal_intent(
            identity, original, execution_lease=handle._execution_lease, first_intent=False
        )
    assert _snapshot(authority_tree) == before


@pytest.mark.parametrize(
    "remaining",
    ["decision", "intent", "outcome", "authority-receipt", "intent-receipt", "seal-receipt"],
)
def test_removed_original_authority_cannot_be_downgraded_by_disabling_policy(
    authority_tree, remaining
):
    import open_workflow_agent.execution_authorization as module

    services, _, store, _, handle, _, identity = authority_tree
    _admit(authority_tree)
    _decision(authority_tree)
    completed = _finish(authority_tree, {"known": True})
    connection = services.effect_journal.connection
    selected = {
        "authority-receipt": authority_identity(handle),
        "intent-receipt": module.intent_identity(identity),
        "seal-receipt": replace(identity, operation=module.SEAL_OPERATION),
    }.get(remaining)
    own_keys = [
        row[0]
        for row in connection.execute(
            "SELECT identity FROM workflow_effect_invocations WHERE invocation_id=?",
            (handle.invocation_id,),
        ).fetchall()
    ]
    for key in own_keys:
        if selected is None or key != selected.key:
            connection.execute("DELETE FROM workflow_effects WHERE identity=?", (key,))
    for table in (
        "execution_authorities",
        "execution_authorization_decisions",
        "execution_authorization_intents",
        "execution_authorization_outcomes",
        "recovery_invocation_definitions",
        "workflow_effect_invocations",
    ):
        if table == "execution_authorization_" + remaining + "s":
            continue
        connection.execute(f"DELETE FROM {table} WHERE invocation_id=?", (handle.invocation_id,))
    connection.execute(
        "DELETE FROM execution_authority_candidates WHERE candidate_key=?",
        ("invocation:" + handle.invocation_id,),
    )
    connection.commit()
    services.config.execution_authorization.enabled = False
    before = _snapshot(authority_tree)
    marker_before = (
        None
        if selected is None
        else connection.execute(
            "SELECT * FROM workflow_effects WHERE identity=?",
            (selected.key,),
        ).fetchall()
    )
    with pytest.raises(OwaError):
        store.seal_intent(identity, completed, execution_lease=handle._execution_lease)
    with pytest.raises(OwaError):
        store.seal_business(identity, completed, execution_lease=handle._execution_lease)
    if remaining in {"decision", "intent", "outcome", "authority-receipt"}:
        with pytest.raises(OwaError):
            store.verify_invocation(handle)
    if remaining == "authority-receipt":
        connection.execute("DELETE FROM invocations WHERE invocation_id=?", (handle.invocation_id,))
        connection.commit()
        with pytest.raises(OwaError):
            store.seal_intent(identity, completed, execution_lease=handle._execution_lease)
    assert _snapshot(authority_tree) == before and services.model.calls == []
    if selected is not None:
        assert (
            connection.execute(
                "SELECT * FROM workflow_effects WHERE identity=?", (selected.key,)
            ).fetchall()
            == marker_before
        )
    # Only fixture-owned orphan markers are removed so real PostgreSQL profiles
    # retain unrelated fixtures and cannot mistake this fault injection for work.
    for key in own_keys:
        connection.execute("DELETE FROM workflow_effects WHERE identity=?", (key,))
    connection.commit()
