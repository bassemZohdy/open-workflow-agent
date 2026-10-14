"""Original executable artifact decisions on actual common SQLite/PostgreSQL stores."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import time
import uuid
from dataclasses import asdict, replace

import pytest
from open_workflow_agent import executable_source_admission as admission_module
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.effects import (
    EffectIdentity,
    EffectReceipt,
    ReceiptDependency,
    effect_request_digest,
)
from open_workflow_agent.errors import (
    ConfigurationError,
    InvocationStateConflict,
    OwaError,
    WorkflowExecutionError,
)
from open_workflow_agent.executable_source_admission import (
    SOURCE_POLICY,
    ExecutableSourceAdmission,
    verify_executable_source_business_receipt,
    verify_executable_source_receipt,
)
from open_workflow_agent.executable_sources import (
    ExecutableScriptBinding,
    ExecutableSource,
    ExecutableSourceRegistry,
)
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import normalize_workflow

URI = "https://approved.test/original.py"
CODE = b"print('original executable bytes: \\u03b1')\n"


def artifact(code=CODE):
    return ExecutableSource.capture(
        id="approved-script",
        version="1.0.0",
        uri=URI,
        code=code,
        digest=hashlib.sha256(code).hexdigest(),
    )


@pytest.fixture(params=["sqlite", "postgresql"])
def original(tmp_path, request):
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if request.param == "postgresql" else None
    if request.param == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    source = artifact()
    config = RuntimeConfig.model_validate(
        {
            "persistence": {"datasource": datasource},
            "sandbox": {"enabled": True, "script_sources": [source.as_value()]},
        }
    )
    services = RuntimeServices(
        config, model=FakeModel(), database_root=None if datasource else tmp_path
    )
    assert services.effect_journal.connection.kind == request.param
    # Independent store evidence seam. Native/profile qualification remains
    # in the shared actual SDK and process fixtures, not this portable handle.
    services.recovery_definitions.profile_provider = lambda engine: {
        "profile": "executable-source-store-test",
        "engine": engine,
        "backend": request.param,
        "policy": {"sandbox": {"max_output_bytes": config.sandbox.max_output_bytes}},
    }
    public = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "scripts",
            "name": "original-" + uuid.uuid4().hex,
            "version": "1.0.0",
        },
        "do": [
            {"action": {"run": {"script": {"language": "python", "source": {"endpoint": URI}}}}}
        ],
    }
    plan = normalize_workflow(
        public,
        script_resources=(ExecutableScriptBinding("/do/0/action", source),),
    )
    handle = services.invocations.create(
        engine="portable",
        session_id="original-context",
        user_id="original-user",
        owner_principal="original-principal",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    lease = services.execution_ownership.acquire(handle, seconds=300)
    handle._execution_lease = lease
    services.recovery_definitions.admit(plan, handle, {"raw": None}, execution_lease=lease)
    identity = EffectIdentity(
        handle.invocation_id, plan.fingerprint, "/do/0/action", (("/do/0/action", 1),), "run:script"
    )
    helper = services.executable_source_admissions
    assert isinstance(helper, ExecutableSourceAdmission)
    payload = {"script": {"language": "python", "source": {"endpoint": URI}}, "input": None}
    tree = services, helper, handle, identity, source, payload, time.time() + 200
    try:
        yield tree
    finally:
        if handle._execution_lease is not None:
            try:
                services.execution_ownership.release(handle._execution_lease)
            except InvocationStateConflict:
                pass
        services.close()


def prepare(tree, **changes):
    _, helper, handle, identity, source, payload, deadline = tree
    return helper.prepare(
        changes.pop("identity", identity),
        changes.pop("payload", payload),
        changes.pop("source", source),
        changes.pop("handle", handle),
        execution_lease=changes.pop("execution_lease", handle._execution_lease),
        deadline_at=changes.pop("deadline_at", deadline),
        **changes,
    )


def snapshot(tree):
    services, _, handle, identity, _, _, _ = tree
    connection = services.effect_journal.connection
    # Scope every read to this invocation; shared PostgreSQL fixture state is
    # neither an oracle nor eligible for deletion/repair by these tests.
    return tuple(
        connection.execute(statement, parameters).fetchall()
        for statement, parameters in (
            (
                "SELECT * FROM executable_source_admissions WHERE invocation_id=? "
                "ORDER BY business_key",
                (handle.invocation_id,),
            ),
            (
                "SELECT * FROM workflow_effect_invocations WHERE invocation_id=? ORDER BY identity",
                (handle.invocation_id,),
            ),
            (
                "SELECT * FROM workflow_effects WHERE identity IN "
                "(SELECT identity FROM workflow_effect_invocations WHERE invocation_id=?) "
                "ORDER BY identity",
                (handle.invocation_id,),
            ),
            (
                "SELECT * FROM workflow_effects WHERE identity IN (?,?) ORDER BY identity",
                (identity.key, replace(identity, operation=SOURCE_POLICY).key),
            ),
            ("SELECT * FROM invocation_ownership WHERE invocation_id=?", (handle.invocation_id,)),
            ("SELECT * FROM invocations WHERE invocation_id=?", (handle.invocation_id,)),
            ("SELECT * FROM invocation_inputs WHERE invocation_id=?", (handle.invocation_id,)),
            (
                "SELECT * FROM recovery_invocation_definitions WHERE invocation_id=?",
                (handle.invocation_id,),
            ),
            (
                "SELECT * FROM recovery_definitions WHERE fingerprint=?",
                (handle.workflow_fingerprint,),
            ),
        )
    )


def test_original_source_receipt_is_atomic_code_free_and_occurrence_bound(original):
    services, _, _, identity, source, _, deadline = original
    decision = prepare(original)
    assert decision.identity == replace(identity, operation=SOURCE_POLICY)
    assert decision.source.verified_bytes() == CODE and decision.receipt.status == "completed"
    assert decision.receipt.output["deadline_at"] == deadline
    assert decision.receipt.output["source_binding"] == {
        "profile": "owa/executable-source/v1",
        "id": source.id,
        "version": source.version,
        "uri": URI,
        "language": "python",
        "digest": hashlib.sha256(CODE).hexdigest(),
    }
    assert "code" not in decision.receipt.output["source_binding"]
    assert source.code not in json.dumps(decision.receipt.output)
    assert services.effect_journal.get(identity) is None
    assert services.recovery_definitions.resolve(original[2]).script_resources[0].source == source
    assert (
        ReceiptDependency.capture(decision.identity, decision.receipt).identity == decision.identity
    )
    before = snapshot(original)
    assert prepare(original, allow_new=False) == decision
    assert snapshot(original) == before and services.model.calls == []


def test_saved_source_proof_is_readonly_after_execution_lease_release(original):
    services, _, handle, _, _, _, _ = original
    decision = prepare(original)
    services.execution_ownership.release(handle._execution_lease)
    handle._execution_lease = None
    before = snapshot(original)
    verify_executable_source_receipt(
        services.effect_journal.connection,
        services.invocations,
        decision.identity,
        decision.receipt,
    )
    assert snapshot(original) == before
    with pytest.raises(InvocationStateConflict):
        prepare(original)
    assert snapshot(original) == before


@pytest.mark.parametrize(
    "damage",
    [
        "source-fingerprint",
        "index-identity",
        "receipt-missing",
        "source-payload-bound",
        "index-payload-bound",
    ],
    ids=["fingerprint", "index", "receipt", "source-bound", "index-bound"],
)
@pytest.mark.asyncio
async def test_startup_source_history_rejects_fixture_damage_without_repair_or_dispatch(
    original, damage
):
    services, _, handle, identity, _, _, _ = original
    decision = prepare(original)
    connection = services.effect_journal.connection
    if damage == "source-fingerprint":
        connection.execute(
            "UPDATE executable_source_admissions SET fingerprint=? WHERE business_key=?",
            ("forged", identity.key),
        )
    elif damage == "index-identity":
        forged = asdict(identity)
        forged["workflow_fingerprint"] = "forged"
        connection.execute(
            "UPDATE workflow_effect_invocations SET payload=? WHERE identity=?",
            (json.dumps(forged), decision.identity.key),
        )
    elif damage == "receipt-missing":
        connection.execute(
            "DELETE FROM workflow_effects WHERE identity=?", (decision.identity.key,)
        )
    elif damage == "source-payload-bound":
        connection.execute(
            "UPDATE executable_source_admissions SET payload=? WHERE business_key=?",
            ("x" * 524_289, identity.key),
        )
    else:
        connection.execute(
            "UPDATE workflow_effect_invocations SET payload=? WHERE identity=?",
            ("x" * 65_537, decision.identity.key),
        )
    connection.commit()
    before = snapshot(original)
    expected_error = (
        WorkflowExecutionError if damage == "index-payload-bound" else ConfigurationError
    )
    with pytest.raises(expected_error):
        await services.verify_executable_source_history(handle)
    assert snapshot(original) == before
    assert services.model.calls == []


@pytest.mark.asyncio
async def test_startup_source_history_accepts_intact_prepared_source_without_mutation(original):
    services, _, handle, _, _, _, _ = original
    prepare(original)
    before = snapshot(original)
    await services.verify_executable_source_history(handle)
    assert snapshot(original) == before and services.model.calls == []


def test_same_original_source_has_distinct_policy_per_loop_visit(original):
    first = prepare(original)
    identity = replace(original[3], occurrence=(("/do/0/action", 2),))
    second = prepare(original, identity=identity)
    assert first.identity.key != second.identity.key and first.source == second.source
    assert first.receipt.output["business_identity"]["occurrence"] == [["/do/0/action", 1]]
    assert second.receipt.output["business_identity"]["occurrence"] == [["/do/0/action", 2]]


def test_reusable_calls_retain_public_source_pointer_and_distinct_execution_identity(original):
    services, helper, _, _, source, payload, deadline = original
    public = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "scripts",
            "name": "reusable-" + uuid.uuid4().hex,
            "version": "1.0.0",
        },
        "use": {
            "functions": {
                "shared": {
                    "run": {
                        "script": {
                            "language": "python",
                            "source": {"endpoint": URI},
                        }
                    }
                }
            }
        },
        "do": [{"first": {"call": "shared"}}, {"second": {"call": "shared"}}],
    }
    plan = normalize_workflow(
        public,
        script_resources=(ExecutableScriptBinding("/use/functions/shared", source),),
    )
    handle = services.invocations.create(
        engine="portable",
        session_id="original-context",
        user_id="original-user",
        owner_principal="original-principal",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    lease = services.execution_ownership.acquire(handle, seconds=300)
    handle._execution_lease = lease
    try:
        services.recovery_definitions.admit(plan, handle, {"raw": None}, execution_lease=lease)
        leaves = [task for scope in plan.scopes for task in scope.tasks if task.kind == "run"]
        assert len(leaves) == 2 and {task.reference for task in leaves} == {"/use/functions/shared"}
        decisions = []
        for leaf in leaves:
            execution = leaf.execution_reference
            assert execution is not None and execution != leaf.reference
            identity = EffectIdentity(
                handle.invocation_id, plan.fingerprint, execution, ((execution, 1),), "run:script"
            )
            decision = helper.prepare(
                identity, payload, source, handle, execution_lease=lease, deadline_at=deadline
            )
            verify_executable_source_receipt(
                services.effect_journal.connection,
                services.invocations,
                decision.identity,
                decision.receipt,
            )
            decisions.append(decision)
        assert decisions[0].identity != decisions[1].identity
        assert decisions[0].source == decisions[1].source == source
    finally:
        services.execution_ownership.release(lease)


def test_coherently_forged_source_digest_still_requires_original_archived_artifact(original):
    services, _, _, identity, _, _, _ = original
    decision = prepare(original)
    value = copy.deepcopy(decision.receipt.output)
    value["source_binding"]["digest"] = "0" * 64
    forged = replace(
        decision.receipt, output=value, request_digest=effect_request_digest(SOURCE_POLICY, value)
    )
    connection = services.effect_journal.connection
    connection.execute(
        "UPDATE executable_source_admissions SET payload=?,digest=? WHERE business_key=?",
        (json.dumps(value), forged.request_digest, identity.key),
    )
    connection.execute(
        "UPDATE workflow_effects SET payload=? WHERE identity=?",
        (json.dumps(asdict(forged)), decision.identity.key),
    )
    connection.commit()
    before = snapshot(original)
    with pytest.raises(OwaError):
        verify_executable_source_receipt(
            connection, services.invocations, decision.identity, forged
        )
    assert snapshot(original) == before


@pytest.mark.parametrize("changed", ["request", "deadline", "source", "registry", "task", "lease"])
def test_changed_live_binding_rejects_without_promoting_an_existing_source(original, changed):
    prepare(original)
    services, helper, _, identity, _, payload, deadline = original
    changes = {}
    if changed == "request":
        changes["payload"] = {**payload, "input": {"changed": True}}
    elif changed == "deadline":
        changes["deadline_at"] = deadline + 1
    elif changed == "source":
        changes["source"] = artifact(b"print('different approved bytes')\n")
    elif changed == "registry":
        services.executable_sources = ExecutableSourceRegistry(
            (artifact(b"print('replacement')\n"),)
        )
    elif changed == "task":
        changes["identity"] = replace(identity, task_reference="/do/0/forged")
    else:
        changes["execution_lease"] = replace(original[2]._execution_lease, generation=999)
    before = snapshot(original)
    with pytest.raises(OwaError):
        prepare(original, **changes)
    assert snapshot(original) == before and helper.registry.sources[0].verified_bytes() == CODE


@pytest.mark.parametrize(
    "damage",
    [
        "row_missing",
        "row_digest",
        "policy_missing",
        "index_missing",
        "index_changed",
        "definition_missing",
        "archive_missing",
        "archive_changed",
        "input_changed",
        "principal_changed",
    ],
)
def test_pure_native_policy_verifier_rejects_missing_or_corrupt_independent_evidence(
    original, damage
):
    services, _, handle, identity, _, _, _ = original
    decision = prepare(original)
    connection = services.effect_journal.connection
    if damage == "row_missing":
        connection.execute(
            "DELETE FROM executable_source_admissions WHERE business_key=?", (identity.key,)
        )
    elif damage == "row_digest":
        connection.execute(
            "UPDATE executable_source_admissions SET digest=? WHERE business_key=?",
            ("0" * 64, identity.key),
        )
    elif damage == "policy_missing":
        connection.execute(
            "DELETE FROM workflow_effects WHERE identity=?", (decision.identity.key,)
        )
    elif damage == "index_missing":
        connection.execute(
            "DELETE FROM workflow_effect_invocations WHERE identity=?", (decision.identity.key,)
        )
    elif damage == "index_changed":
        connection.execute(
            "UPDATE workflow_effect_invocations SET fingerprint=? WHERE identity=?",
            ("forged", decision.identity.key),
        )
    elif damage == "definition_missing":
        dependency = ReceiptDependency.from_value(decision.receipt.output["definition_receipt"])
        connection.execute(
            "DELETE FROM workflow_effects WHERE identity=?", (dependency.identity.key,)
        )
    elif damage in {"archive_missing", "archive_changed"}:
        statement = (
            "DELETE FROM recovery_definitions WHERE fingerprint=?"
            if damage == "archive_missing"
            else "UPDATE recovery_definitions SET digest='forged' WHERE fingerprint=?"
        )
        connection.execute(statement, (handle.workflow_fingerprint,))
    elif damage == "input_changed":
        connection.execute(
            "UPDATE invocation_inputs SET payload=? WHERE invocation_id=?",
            ('{"raw":"changed"}', handle.invocation_id),
        )
    else:
        row = connection.execute(
            "SELECT payload FROM invocations WHERE invocation_id=?", (handle.invocation_id,)
        ).fetchone()
        value = json.loads(row[0])
        value["owner_principal"] = "foreign"
        connection.execute(
            "UPDATE invocations SET payload=? WHERE invocation_id=?",
            (json.dumps(value), handle.invocation_id),
        )
    connection.commit()
    before = snapshot(original)
    with pytest.raises(OwaError):
        verify_executable_source_receipt(
            connection, services.invocations, decision.identity, decision.receipt
        )
    assert snapshot(original) == before
    with pytest.raises(OwaError):
        prepare(original)
    assert snapshot(original) == before


@pytest.mark.parametrize("status", ["pending", "value", "null", "fault", "orphan-index"])
def test_legacy_business_intent_is_never_promoted_to_source_authority(original, status):
    services, _, _, identity, _, payload, _ = original
    journal = services.effect_journal
    # Arrange prior/corrupt evidence directly. Production journal reads now
    # deliberately reject business-only source evidence before returning it.
    receipt = EffectReceipt(
        effect_request_digest("run:script", payload),
        "original-business-owner",
        status="faulted"
        if status == "fault"
        else "pending"
        if status in {"pending", "orphan-index"}
        else "completed",
        output={"known": True} if status == "value" else None,
        error={"code": "known", "message": "known failure", "details": {}}
        if status == "fault"
        else None,
    )
    journal.connection.execute(
        "INSERT INTO workflow_effect_invocations(identity,invocation_id,fingerprint,payload) "
        "VALUES (?,?,?,?)",
        (
            identity.key,
            identity.invocation_id,
            identity.workflow_fingerprint,
            json.dumps(asdict(identity)),
        ),
    )
    if status != "orphan-index":
        journal.connection.execute(
            "INSERT INTO workflow_effects(identity,owner,status,payload) VALUES (?,?,?,?)",
            (identity.key, receipt.owner, receipt.status, json.dumps(asdict(receipt))),
        )
    journal.connection.commit()
    before = snapshot(original)
    with pytest.raises(OwaError):
        prepare(original)
    assert snapshot(original) == before


def test_failed_receipt_commit_rolls_back_source_row_policy_and_index(original, monkeypatch):
    real_insert = admission_module.insert_completed_receipt
    before = snapshot(original)

    def fail_after_insert(*args):
        real_insert(*args)
        raise WorkflowExecutionError("injected commit participant failure")

    monkeypatch.setattr(admission_module, "insert_completed_receipt", fail_after_insert)
    with pytest.raises(WorkflowExecutionError, match="injected"):
        prepare(original)
    assert snapshot(original) == before
    monkeypatch.setattr(admission_module, "insert_completed_receipt", real_insert)
    assert prepare(original).source.verified_bytes() == CODE


@pytest.mark.parametrize("deadline", [0, -1, True, float("inf"), float("nan"), 1.0])
def test_invalid_or_expired_original_deadline_never_creates_policy(original, deadline):
    before = snapshot(original)
    with pytest.raises(OwaError):
        prepare(original, deadline_at=deadline)
    assert snapshot(original) == before


@pytest.mark.parametrize(
    "payload",
    [("not-json",), {"untrusted": object()}, float("nan")],
    ids=["tuple", "object", "nonfinite"],
)
def test_untrusted_run_input_is_not_coerced_into_source_authority(original, payload):
    before = snapshot(original)
    with pytest.raises((OwaError, ValueError, TypeError)):
        prepare(original, payload=payload)
    assert snapshot(original) == before


def test_policy_from_other_visit_cannot_verify_current_identity(original):
    decision = prepare(original)
    identity = replace(decision.identity, occurrence=(("/do/0/action", 2),))
    before = snapshot(original)
    with pytest.raises(OwaError):
        verify_executable_source_receipt(
            original[0].effect_journal.connection,
            original[0].invocations,
            identity,
            decision.receipt,
        )
    assert snapshot(original) == before


@pytest.mark.parametrize("outcome", ["value", "null", "fault"])
@pytest.mark.parametrize("damage", ["policy", "policy-index", "all-source-proofs", "archive"])
def test_business_only_native_dependency_requires_independent_original_source_policy(
    original, outcome, damage
):
    services, _, handle, identity, _, payload, _ = original
    decision = prepare(original)
    journal = services.effect_journal
    journal.begin(
        identity,
        effect_request_digest("run:script", payload),
        "original-business-owner",
        execution_lease=handle._execution_lease,
    )
    receipt = journal.finish(
        identity,
        "original-business-owner",
        output={"known": "original"} if outcome == "value" else None,
        error={"code": "known", "message": "known fault", "details": {}}
        if outcome == "fault"
        else None,
    )
    # The native checkpoint deliberately retains only the business dependency,
    # not a separately notified source-policy dependency.
    dependency = ReceiptDependency.capture(identity, receipt)
    dependency.verify(journal)
    connection = journal.connection
    if damage == "archive":
        connection.execute(
            "DELETE FROM recovery_definitions WHERE fingerprint=?", (handle.workflow_fingerprint,)
        )
    else:
        connection.execute(
            "DELETE FROM workflow_effects WHERE identity=?", (decision.identity.key,)
        )
        if damage in {"policy-index", "all-source-proofs"}:
            connection.execute(
                "DELETE FROM workflow_effect_invocations WHERE identity=?", (decision.identity.key,)
            )
        if damage == "all-source-proofs":
            connection.execute(
                "DELETE FROM executable_source_admissions WHERE business_key=?", (identity.key,)
            )
    connection.commit()
    before = snapshot(original)
    with pytest.raises(OwaError):
        verify_executable_source_business_receipt(
            connection, services.invocations, identity, receipt
        )
    with pytest.raises(WorkflowExecutionError):
        dependency.verify(journal)
    assert snapshot(original) == before and services.model.calls == []


def test_source_business_request_digest_must_match_retained_policy(original):
    services, _, handle, identity, _, payload, _ = original
    prepare(original)
    original_receipt = services.effect_journal.begin(
        identity,
        effect_request_digest("run:script", payload),
        "original-business-owner",
        execution_lease=handle._execution_lease,
    )
    # Reading a mismatched intent is already unsafe; it need not reach an
    # outcome or business execution to fail closed.
    connection = services.effect_journal.connection
    changed = replace(
        original_receipt,
        request_digest=effect_request_digest("run:script", {**payload, "input": "forged"}),
    )
    connection.execute(
        "UPDATE workflow_effects SET payload=? WHERE identity=?",
        (json.dumps(asdict(changed)), identity.key),
    )
    connection.commit()
    row = connection.execute(
        "SELECT payload FROM workflow_effects WHERE identity=?", (identity.key,)
    ).fetchone()
    receipt = EffectReceipt(**json.loads(row[0]))
    before = snapshot(original)
    with pytest.raises(OwaError):
        verify_executable_source_business_receipt(
            connection, services.invocations, identity, receipt
        )
    assert snapshot(original) == before


@pytest.mark.parametrize("archived", [False, True])
def test_generic_inline_business_receipt_retains_legacy_journal_contract(original, archived):
    services, _, _, _, _, _, _ = original
    public = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "scripts",
            "name": "inline-" + uuid.uuid4().hex,
            "version": "1.0.0",
        },
        "do": [{"inline": {"run": {"script": {"language": "python", "code": "pass"}}}}],
    }
    plan = normalize_workflow(public)
    handle = services.invocations.create(
        engine="portable",
        session_id="legacy-context",
        user_id="original-user",
        owner_principal="original-principal",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    lease = services.execution_ownership.acquire(handle, seconds=300)
    handle._execution_lease = lease
    identity = EffectIdentity(
        handle.invocation_id, plan.fingerprint, "/do/0/inline", (("/do/0/inline", 1),), "run:script"
    )
    try:
        if archived:
            services.recovery_definitions.admit(plan, handle, {"raw": None}, execution_lease=lease)
        # Check both the predecessor journal with no archive/source evidence
        # and a genuinely archived inline task with no captured source.
        journal = services.effect_journal
        journal.begin(
            identity,
            effect_request_digest("run:script", {"code": "pass"}),
            "inline-owner",
            execution_lease=lease,
        )
        receipt = journal.finish(identity, "inline-owner", output=None)

        def own_snapshot():
            return tuple(
                journal.connection.execute(sql, args).fetchall()
                for sql, args in (
                    ("SELECT * FROM invocations WHERE invocation_id=?", (handle.invocation_id,)),
                    (
                        "SELECT * FROM invocation_ownership WHERE invocation_id=?",
                        (handle.invocation_id,),
                    ),
                    ("SELECT * FROM workflow_effects WHERE identity=?", (identity.key,)),
                    (
                        "SELECT * FROM executable_source_admissions WHERE invocation_id=?",
                        (handle.invocation_id,),
                    ),
                )
            )

        before = snapshot(original), own_snapshot()
        verify_executable_source_business_receipt(
            journal.connection, services.invocations, identity, receipt
        )
        ReceiptDependency.capture(identity, receipt).verify(journal)
        assert (snapshot(original), own_snapshot()) == before
    finally:
        services.execution_ownership.release(lease)


@pytest.mark.parametrize("stdout", ["x" * 300_000, "\x01" * 300_000], ids=["ascii", "escaped"])
def test_valid_business_output_exceeds_policy_size_budget_without_losing_source_proof(
    original, stdout
):
    services, _, handle, identity, _, payload, _ = original
    prepare(original)
    journal = services.effect_journal
    journal.begin(
        identity,
        effect_request_digest("run:script", payload),
        "original-business-owner",
        execution_lease=handle._execution_lease,
    )
    receipt = journal.finish(
        identity,
        "original-business-owner",
        output={
            "execution_id": "original",
            "exit_code": 0,
            "stdout": stdout,
            "stderr": "",
            "duration": 0.1,
        },
    )
    assert len(stdout.encode("utf-8")) < services.config.sandbox.max_output_bytes
    saved = journal.connection.execute(
        "SELECT payload FROM workflow_effects WHERE identity=?", (identity.key,)
    ).fetchone()[0]
    assert len(saved.encode("utf-8")) > admission_module._MAX_RECORD_BYTES
    before = snapshot(original)
    verify_executable_source_business_receipt(
        journal.connection, services.invocations, identity, receipt
    )
    ReceiptDependency.capture(identity, receipt).verify(journal)
    assert snapshot(original) == before and journal.get(identity).output["stdout"] == stdout


def test_business_receipt_byte_bound_rejects_oversize_before_loading_utf8_payload(
    original, monkeypatch
):
    services, _, handle, identity, _, payload, _ = original
    prepare(original)
    journal = services.effect_journal
    pending = journal.begin(
        identity,
        effect_request_digest("run:script", payload),
        "original-business-owner",
        execution_lease=handle._execution_lease,
    )
    receipt = replace(pending, status="completed", output={"stdout": "\U0001f4a5" * 160})
    raw = json.dumps(asdict(receipt), ensure_ascii=False)
    finite_limit = 800
    assert len(raw) < finite_limit < len(raw.encode("utf-8"))
    journal.connection.execute(
        "UPDATE workflow_effects SET status=?,payload=? WHERE identity=?",
        ("completed", raw, identity.key),
    )
    journal.connection.commit()
    before = snapshot(original)
    original_parse = admission_module._parsed
    seen = []

    def bounded_parse(value, **kwargs):
        seen.append(value)
        assert value != raw, "oversize business bytes reached JSON decoding"
        return original_parse(value, **kwargs)

    # A small private read allowance makes the UTF8-vs-character boundary
    # observable without a many-megabyte corruption fixture.
    monkeypatch.setattr(admission_module, "_business_limit", lambda binding: finite_limit)
    monkeypatch.setattr(admission_module, "_parsed", bounded_parse)
    with pytest.raises(OwaError):
        verify_executable_source_business_receipt(
            journal.connection, services.invocations, identity, receipt
        )
    assert raw not in seen and snapshot(original) == before


@pytest.mark.parametrize(
    "budget", [10 * 1_048_576, 11 * 1_048_576], ids=["documented", "unsupported"]
)
def test_source_output_profile_is_qualified_before_any_business_dispatch(
    original, tmp_path, budget
):
    original_services, _, _, _, source, _, _ = original
    config = RuntimeConfig.model_validate(
        {
            "persistence": original_services.config.persistence.model_dump(mode="json"),
            "sandbox": {
                "enabled": True,
                "script_sources": [source.as_value()],
                "max_output_bytes": budget,
            },
        }
    )
    services = RuntimeServices(
        config,
        model=FakeModel(),
        database_root=None
        if original_services.effect_journal.connection.is_postgresql
        else tmp_path / "output-profile",
    )
    services.recovery_definitions.profile_provider = lambda engine: {
        "profile": "source-output-profile-test",
        "engine": engine,
        "policy": {"sandbox": {"max_output_bytes": budget}},
    }
    public = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "scripts",
            "name": "output-" + uuid.uuid4().hex,
            "version": "1.0.0",
        },
        "do": [
            {"action": {"run": {"script": {"language": "python", "source": {"endpoint": URI}}}}}
        ],
    }
    plan = normalize_workflow(
        public, script_resources=(ExecutableScriptBinding("/do/0/action", source),)
    )
    handle = services.invocations.create(
        engine="portable",
        session_id="output-context",
        user_id="original-user",
        owner_principal="original-principal",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    lease = services.execution_ownership.acquire(handle, seconds=300)
    handle._execution_lease = lease
    identity = EffectIdentity(
        handle.invocation_id, plan.fingerprint, "/do/0/action", (("/do/0/action", 1),), "run:script"
    )
    try:
        args = identity, {"run": "original"}, source, handle
        if budget == 10 * 1_048_576:
            services.recovery_definitions.admit(plan, handle, {}, execution_lease=lease)
            decision = services.executable_source_admissions.prepare(
                *args,
                execution_lease=lease,
                deadline_at=None,
            )
            assert decision.source == source
        else:
            with pytest.raises(OwaError) as rejected:
                services.recovery_definitions.admit(plan, handle, {}, execution_lease=lease)
            assert (
                rejected.value.details["reason"] == "executable_source_output_profile_unsupported"
            )
            assert (
                services.effect_journal.connection.execute(
                    "SELECT 1 FROM executable_source_admissions WHERE invocation_id=?",
                    (handle.invocation_id,),
                ).fetchone()
                is None
            )
        assert services.effect_journal.get(identity) is None and services.model.calls == []
    finally:
        services.execution_ownership.release(lease)
        services.close()
