"""Workflow action ownership and receipts survive common-store contention/reopening."""

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from uuid import uuid4

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.effects import (
    EffectIdentity,
    EffectJournal,
    EffectReceipt,
    ReceiptDependency,
    effect_request_digest,
)
from open_workflow_agent.errors import ExpressionError, WorkflowExecutionError
from open_workflow_agent.services import RuntimeServices


def _identity():
    return EffectIdentity(
        "invocation", "fingerprint", "/do/0/action", (("/do/0/action", 1),), "http"
    )


def _digest():
    return effect_request_digest("http", {"method": "POST", "body": {"amount": 3}})


def test_pending_and_completed_null_are_distinct_after_reopening(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    first = EffectJournal(path)
    other = EffectJournal(path)
    try:
        assert first.get(_identity()) is None
        pending = first.begin(
            _identity(), _digest(), "first-owner", operation_id="public-operation"
        )
        assert pending.status == "pending" and pending.output is None
        assert other.begin(_identity(), _digest(), "another-owner") == pending
        with pytest.raises(WorkflowExecutionError, match="does not own"):
            other.finish(_identity(), "another-owner", output={"incorrect": True})
        completed = first.finish(_identity(), "first-owner", output=None)
        assert completed.status == "completed" and completed.output is None
        assert completed.operation_id == "public-operation"
        assert other.finish(_identity(), "first-owner", output={"late": True}) == completed
    finally:
        first.close()
        other.close()
    reopened = EffectJournal(path)
    try:
        assert reopened.begin(_identity(), _digest(), "new-runtime") == completed
    finally:
        reopened.close()


def test_simultaneous_connections_claim_one_owner_and_preserve_one_outcome(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    initial = EffectJournal(path)
    initial.close()
    ready = Barrier(2)

    def claim(owner):
        journal = EffectJournal(path)
        try:
            ready.wait(timeout=10)
            return journal.begin(_identity(), _digest(), owner)
        finally:
            journal.close()

    with ThreadPoolExecutor(max_workers=2) as workers:
        claims = list(workers.map(claim, ("owner-a", "owner-b")))
    assert claims[0] == claims[1]
    owner = claims[0].owner
    ready = Barrier(2)

    def complete(value):
        journal = EffectJournal(path)
        try:
            ready.wait(timeout=10)
            return journal.finish(_identity(), owner, output={"value": value})
        finally:
            journal.close()

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(complete, (1, 2)))
    assert results[0] == results[1]
    assert results[0].output in ({"value": 1}, {"value": 2})


@pytest.mark.parametrize(
    "changes",
    [
        {"invocation_id": "another"},
        {"workflow_fingerprint": "another"},
        {"task_reference": "/do/1/action"},
        {"occurrence": (("/do/0/action", 2),)},
        {"occurrence": (("/do/0/loop", 2), ("/do/0/action", 1))},
        {"occurrence": (("/do/0/action", 1), ("/do/0/action/@attempt", 2))},
        {"operation": "run.script"},
    ],
)
def test_invocations_definitions_scopes_visits_attempts_and_operation_kinds_are_separate(changes):
    journal = EffectJournal()
    try:
        original = journal.begin(_identity(), _digest(), "owner")
        distinct = replace(_identity(), **changes)
        assert distinct.key != _identity().key and journal.get(distinct) is None
        assert journal.begin(distinct, _digest(), "different-owner").owner == "different-owner"
        assert journal.get(_identity()) == original
    finally:
        journal.close()


def test_changed_request_cannot_replace_or_reuse_the_original_intent():
    journal = EffectJournal()
    try:
        pending = journal.begin(_identity(), _digest(), "owner")
        changed = effect_request_digest("http", {"method": "POST", "body": {"amount": 99}})
        with pytest.raises(WorkflowExecutionError, match="request differs"):
            journal.begin(_identity(), changed, "owner")
        assert journal.get(_identity()) == pending
    finally:
        journal.close()


@pytest.mark.parametrize(
    "outcome",
    [
        {"error": {"code": "invalid", "message": "bad", "details": []}},
        {"error_status_code": 400},
        {
            "error": {"code": "invalid", "message": "bad", "details": {}},
            "error_status_code": 600,
        },
        {"operation_id": 3},
    ],
)
def test_invalid_common_error_or_private_identity_does_not_complete_an_intent(outcome):
    journal = EffectJournal()
    try:
        pending = journal.begin(_identity(), _digest(), "owner")
        with pytest.raises(WorkflowExecutionError, match="outcome could not be stored"):
            journal.finish(_identity(), "owner", **outcome)
        assert journal.get(_identity()) == pending
    finally:
        journal.close()


def test_common_fault_and_private_error_identity_remain_distinct_from_null():
    journal = EffectJournal()
    error = {
        "code": "expression_error",
        "message": "invalid input",
        "details": {"instance": "/do/0/action", "status": 400},
    }
    try:
        journal.begin(_identity(), _digest(), "owner")
        receipt = journal.finish(
            _identity(), "owner", error=error, error_status_code=400, operation_id="error-operation"
        )
        assert receipt.status == "faulted" and receipt.output is None
        assert receipt.error == error and receipt.error_status_code == 400
        assert receipt.operation_id == "error-operation"
        assert journal.finish(_identity(), "owner", output={"late": "success"}) == receipt
        restored = journal.get(_identity())
        restored.error["details"]["status"] = 500
        assert journal.get(_identity()).error == error
    finally:
        journal.close()


@pytest.mark.parametrize("output", [float("nan"), float("inf"), {1: "invalid"}, object()])
def test_invalid_outcome_cannot_complete_or_overwrite_an_intent(output):
    journal = EffectJournal()
    try:
        pending = journal.begin(_identity(), _digest(), "owner")
        with pytest.raises(WorkflowExecutionError, match="outcome could not be stored"):
            journal.finish(_identity(), "owner", output=output)
        assert journal.get(_identity()) == pending
        completed = journal.finish(_identity(), "owner", output={"recorded": True})
        with pytest.raises(WorkflowExecutionError, match="outcome could not be stored"):
            journal.finish(_identity(), "owner", output=output)
        assert journal.get(_identity()) == completed
    finally:
        journal.close()


@pytest.mark.parametrize("payload", ['{"secret":"never-include-in-errors"', "NaN", "{}"])
def test_corrupt_receipt_fails_closed_with_a_static_common_error(payload):
    journal = EffectJournal()
    try:
        journal.begin(_identity(), _digest(), "owner")
        journal.connection.execute("UPDATE workflow_effects SET payload = ?", (payload,))
        journal.connection.commit()
        with pytest.raises(WorkflowExecutionError) as failure:
            journal.get(_identity())
        assert failure.value.message == "workflow effect receipt could not be read"
        assert failure.value.details == {}
    finally:
        journal.close()


def test_missing_intent_cannot_be_completed_and_closed_storage_is_a_common_error():
    journal = EffectJournal()
    with pytest.raises(WorkflowExecutionError, match="does not own"):
        journal.finish(_identity(), "owner", output={})
    journal.close()
    with pytest.raises(WorkflowExecutionError, match="could not be read"):
        journal.get(_identity())
    with pytest.raises(WorkflowExecutionError, match="intent could not be stored"):
        journal.begin(_identity(), _digest(), "owner")


def test_receipt_owner_and_status_columns_cannot_disagree_with_the_payload():
    journal = EffectJournal()
    try:
        journal.begin(_identity(), _digest(), "owner")
        journal.connection.execute("UPDATE workflow_effects SET owner = 'changed'")
        journal.connection.commit()
        with pytest.raises(WorkflowExecutionError, match="could not be read"):
            journal.get(_identity())
    finally:
        journal.close()


@pytest.mark.parametrize("changes", [{"owner": ""}, {"request_digest": "invalid"}])
def test_invalid_intent_cannot_be_written(changes):
    journal = EffectJournal()
    try:
        arguments = {"owner": "owner", "request_digest": _digest(), **changes}
        with pytest.raises(WorkflowExecutionError, match="intent could not be stored"):
            journal.begin(_identity(), **arguments)
        assert journal.get(_identity()) is None
    finally:
        journal.close()


@pytest.mark.parametrize(
    "changes",
    [
        {"task_reference": ""},
        {"operation": ""},
        {"occurrence": ()},
        {"occurrence": (("task", False),)},
        {"occurrence": (("task", 0),)},
    ],
)
def test_invalid_identity_cannot_name_an_action(changes):
    with pytest.raises(ValueError):
        replace(_identity(), **changes)


def test_failed_sqlite_outcome_commit_retains_pending_intent(tmp_path, monkeypatch):
    journal = EffectJournal(tmp_path / "runtime.sqlite3")
    try:
        pending = journal.begin(_identity(), _digest(), "owner")

        def fail_commit(_connection):
            raise RuntimeError("private-database-credential")

        with monkeypatch.context() as patch:
            patch.setattr(type(journal.connection), "commit", fail_commit)
            with pytest.raises(WorkflowExecutionError) as failure:
                journal.finish(_identity(), "owner", output={"completed": True})
            assert failure.value.message == "workflow effect outcome could not be stored"
        assert journal.get(_identity()) == pending
    finally:
        journal.close()


def test_runtime_composes_closes_and_reopens_the_common_journal(tmp_path):
    services = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
    services.effect_journal.begin(_identity(), _digest(), "owner")
    completed = services.effect_journal.finish(_identity(), "owner", output=[None, False, 3])
    services.close()
    with pytest.raises(WorkflowExecutionError, match="could not be read"):
        services.effect_journal.get(_identity())
    reopened = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
    try:
        assert reopened.effect_journal.get(_identity()) == completed
    finally:
        reopened.close()


def test_request_digest_has_stable_object_order_and_rejects_non_json():
    assert effect_request_digest("http", {"a": 1, "b": 2}) == effect_request_digest(
        "http", {"b": 2, "a": 1}
    )
    assert effect_request_digest("http", None) != effect_request_digest("http", {})
    assert effect_request_digest("http", {}) != effect_request_digest("mcp", {})
    with pytest.raises(ExpressionError):
        effect_request_digest("http", float("nan"))


def test_existing_non_child_native_digest_retains_original_receipt_format():
    legacy = {
        "request_digest": _digest(),
        "owner": "owner",
        "status": "completed",
        "output": {"known": True},
        "error": None,
        "error_status_code": None,
        "operation_id": "operation",
    }
    original = hashlib.sha256(
        json.dumps(
            legacy, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    assert (
        ReceiptDependency.capture(_identity(), EffectReceipt(**legacy)).receipt_digest == original
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_effect_index_and_intent_admission_rollback_together(tmp_path, monkeypatch, backend):
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    journal = EffectJournal(datasource or tmp_path / "runtime.sqlite3")
    identity = replace(_identity(), invocation_id=str(uuid4()))
    execute = journal.connection.execute

    def fail_intent(_connection, sql, *args, **kwargs):
        if "INSERT INTO workflow_effects(" in sql:
            raise RuntimeError("injected intent write failure")
        return execute(sql, *args, **kwargs)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(type(journal.connection), "execute", fail_intent)
            with pytest.raises(WorkflowExecutionError, match="intent could not be stored"):
                journal.begin(identity, _digest(), "owner")
        assert journal.get(identity) is None
        assert (
            journal.connection.execute(
                "SELECT identity FROM workflow_effect_invocations WHERE identity = ?",
                (identity.key,),
            ).fetchall()
            == []
        )
    finally:
        journal.close()


def test_invocation_reconciliation_is_scoped_and_rejects_missing_admitted_receipt():
    journal = EffectJournal()
    other = replace(_identity(), invocation_id="other")
    try:
        for identity in (_identity(), other):
            journal.begin(identity, _digest(), "owner")
            journal.finish(identity, "owner", output=None)
        journal.connection.execute("DELETE FROM workflow_effects WHERE identity = ?", (other.key,))
        journal.connection.commit()
        journal.verify_invocation("invocation", "fingerprint")
        with pytest.raises(WorkflowExecutionError, match="could not be reconciled"):
            journal.verify_invocation("other", "fingerprint")
    finally:
        journal.close()
