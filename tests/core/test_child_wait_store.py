"""Real parent leases fence durable child wait admissions and wakes."""

from __future__ import annotations

import json
import os
from dataclasses import replace

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.effects import EffectIdentity
from open_workflow_agent.errors import InvocationStateConflict, WorkflowExecutionError
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow


def _plan(name):
    return compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "child-await",
                "name": name,
                "version": "1.0.0",
            },
            "do": [{"finish": {"set": {"done": True}}}],
        }
    )


@pytest.fixture(params=["sqlite", "postgresql"])
def waiting_tree(tmp_path, request):
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if request.param == "postgresql" else None
    if request.param == "postgresql" and datasource is None:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    services = RuntimeServices(
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}}),
        model=FakeModel(),
        database_root=None if datasource else tmp_path,
    )
    parent_plan = _plan("parent")
    parent = services.invocations.create(
        engine="adk",
        session_id=None,
        user_id="correlation",
        owner_principal="owner",
        workflow_name=parent_plan.name,
        workflow_version=parent_plan.version,
        workflow_fingerprint=parent_plan.fingerprint,
    )
    parent._execution_lease = services.execution_ownership.acquire(parent, seconds=60)
    identity = EffectIdentity(
        parent.invocation_id,
        parent.workflow_fingerprint,
        "/do/0/child",
        (("/do", 1),),
        "run:workflow",
    )
    child = services.children.reserve(identity, parent, _plan("child"), {"original": "input"})
    services.invocations.update(child, status="waiting")
    services.invocations.update(parent, status="waiting")
    try:
        yield services, parent, child, identity, parent_plan.source
    finally:
        services.invocations.update(parent, status="cancelled")
        services.execution_ownership.release(parent._execution_lease)
        services.close()


def _arm(tree, deadline=1000.0):
    services, parent, child, identity, source = tree
    return services.child_waits.arm(
        identity, parent, child, workflow_source=source, deadline_at=deadline
    )


def test_wait_admission_and_original_deadline_survive_reopen(waiting_tree):
    services, parent, child, identity, source = waiting_tree
    registration = _arm(waiting_tree)
    reopened = RuntimeServices(
        services.config, model=FakeModel(), database_root=services.database_root
    )
    try:
        saved = reopened.child_waits.registration(identity)
        assert saved.suspension.as_value() == registration.suspension.as_value()
        assert saved.receipt == registration.receipt
        assert saved.workflow_source_json == registration.workflow_source_json
        assert (
            reopened.child_waits.arm(
                identity, parent, child, workflow_source=source, deadline_at=1000.0
            ).receipt
            == registration.receipt
        )
        with pytest.raises(WorkflowExecutionError, match="deadline changed"):
            reopened.child_waits.arm(
                identity, parent, child, workflow_source=source, deadline_at=1001.0
            )
        assert reopened.child_waits.wake(saved, parent, now=999.0) is None
        assert reopened.child_waits.pending_keys("adk", now=999.0) == []
        value = reopened.child_waits.wake(saved, parent, now=1000.0)
        assert value == {
            "child_key": identity.key,
            "child_id": child.invocation_id,
            "request_digest": registration.suspension.request_digest,
            "deadline_at": 1000.0,
            "reason": "deadline",
        }
        assert reopened.child_waits.wake(saved, parent, now=2000.0) == value
        assert reopened.child_waits.known_wakes(
            parent.invocation_id, parent.workflow_fingerprint
        ) == {identity.key: value}
        reopened.child_waits.consume(saved, parent)
        assert reopened.child_waits.pending_keys("adk", now=2000.0) == []
        assert (
            reopened.child_waits.for_invocation(parent.invocation_id, parent.workflow_fingerprint)
            == []
        )
        assert reopened.child_waits.registration(identity).receipt == registration.receipt
    finally:
        reopened.close()


@pytest.mark.parametrize("status", ["completed", "faulted", "cancelled"])
def test_terminal_wake_captures_child_outcome_and_rejects_changed_result(waiting_tree, status):
    services, parent, child, identity, _ = waiting_tree
    registration = _arm(waiting_tree, deadline=None)
    if status == "completed":
        services.invocations.update(child, status="running")
    services.invocations.update(
        child,
        status=status,
        output={"child": "result"} if status == "completed" else None,
        error={"code": "test", "message": "expected", "details": {}}
        if status == "faulted"
        else None,
    )
    assert services.child_waits.pending_keys("adk", now=500.0) == [identity.key]
    wake = services.child_waits.wake(registration, parent, now=500.0)
    assert wake["reason"] == "child_terminal"
    receipt = services.effect_journal.get(replace(identity, operation="policy:child-wake:v1"))
    assert receipt.child_invocation.invocation_id == child.invocation_id
    assert (
        services.effect_journal.get(replace(identity, operation="policy:child-await:v1"))
        == registration.receipt
    )
    services.child_waits.consume(registration, parent)
    payload = json.loads(child._persisted_payload)
    payload["output"] = {"forged": True}
    connection = services.child_waits.connection
    connection.execute(
        "UPDATE invocations SET payload=? WHERE invocation_id=?",
        (json.dumps(payload), child.invocation_id),
    )
    connection.commit()
    with pytest.raises(WorkflowExecutionError):
        services.child_waits.known_wakes(parent.invocation_id, parent.workflow_fingerprint)
    with pytest.raises(WorkflowExecutionError):
        services.effect_journal.get(replace(identity, operation="policy:child-wake:v1"))


@pytest.mark.parametrize(
    "damage",
    [
        "deadline",
        "source",
        "binding",
        "budget",
        "input",
        "owner",
        "state",
        "receipt",
        "missing-row",
    ],
)
def test_changed_wait_evidence_fails_closed_before_resume(waiting_tree, damage):
    services, parent, child, identity, _ = waiting_tree
    registration = _arm(waiting_tree)
    connection = services.child_waits.connection
    if damage == "deadline":
        connection.execute(
            "UPDATE child_workflow_waits SET deadline_at=2000 WHERE run_key=?", (identity.key,)
        )
    elif damage == "source":
        row = connection.execute(
            "SELECT payload FROM child_workflow_waits WHERE run_key=?", (identity.key,)
        ).fetchone()
        value = json.loads(row[0])
        value["source"]["do"] = []
        connection.execute(
            "UPDATE child_workflow_waits SET payload=? WHERE run_key=?",
            (json.dumps(value), identity.key),
        )
    elif damage == "binding":
        connection.execute(
            "DELETE FROM child_workflow_bindings WHERE binding_key=?", (identity.key,)
        )
    elif damage == "budget":
        connection.execute(
            "UPDATE child_workflow_budgets SET max_depth=64 WHERE root_id=?",
            (parent.invocation_id,),
        )
    elif damage == "input":
        connection.execute(
            "DELETE FROM invocation_inputs WHERE invocation_id=?", (child.invocation_id,)
        )
    elif damage == "owner":
        value = json.loads(child._persisted_payload)
        value["owner_principal"] = "forged"
        connection.execute(
            "UPDATE invocations SET payload=? WHERE invocation_id=?",
            (json.dumps(value), child.invocation_id),
        )
    elif damage == "state":
        connection.execute(
            "UPDATE child_workflow_waits SET state='woken' WHERE run_key=?", (identity.key,)
        )
    elif damage == "receipt":
        connection.execute(
            "DELETE FROM workflow_effects WHERE identity=?",
            (replace(identity, operation="policy:child-await:v1").key,),
        )
    else:
        connection.execute("DELETE FROM child_workflow_waits WHERE run_key=?", (identity.key,))
    connection.commit()
    with pytest.raises(WorkflowExecutionError):
        services.child_waits.wake(registration, parent, now=1000.0)
    with pytest.raises(WorkflowExecutionError):
        services.child_waits.verify_invocation(parent.invocation_id, parent.workflow_fingerprint)
    assert services.model.calls == []


def test_wait_writes_require_live_parent_execution_ownership(waiting_tree):
    services, parent, child, identity, source = waiting_tree
    lease = parent._execution_lease
    parent._execution_lease = None
    try:
        with pytest.raises(InvocationStateConflict):
            services.child_waits.arm(
                identity, parent, child, workflow_source=source, deadline_at=None
            )
    finally:
        parent._execution_lease = lease
    registration = _arm(waiting_tree)
    # Keep a well-formed but wrong generation, so the database guard decides authority.
    parent._execution_lease = replace(lease, generation=lease.generation + 1)
    try:
        with pytest.raises(InvocationStateConflict):
            services.child_waits.wake(registration, parent, now=1000.0)
        with pytest.raises(InvocationStateConflict):
            services.child_waits.consume(registration, parent)
        assert (
            services.child_waits.known_wakes(parent.invocation_id, parent.workflow_fingerprint)
            == {}
        )
    finally:
        parent._execution_lease = lease


def test_failed_admission_receipt_rolls_back_wait_row(waiting_tree, monkeypatch):
    import open_workflow_agent.child_waits as module

    services, _, _, identity, _ = waiting_tree

    def fail(*args, **kwargs):
        raise WorkflowExecutionError("injected admission failure")

    monkeypatch.setattr(module, "insert_completed_receipt", fail)
    with pytest.raises(WorkflowExecutionError, match="injected"):
        _arm(waiting_tree)
    assert services.child_waits.registration(identity) is None
    assert services.effect_journal.get(replace(identity, operation="policy:child-await:v1")) is None


def test_raw_bounded_scan_can_advance_past_damaged_eligible_work(waiting_tree):
    services, parent, child, identity, source = waiting_tree
    registrations = [_arm(waiting_tree)]
    for index in (2, 3):
        other = replace(identity, occurrence=(("/do", index),))
        child = services.children.reserve(other, parent, _plan("child"), {"n": index})
        registrations.append(
            services.child_waits.arm(
                other, parent, child, workflow_source=source, deadline_at=1000.0
            )
        )
    keys = sorted(item.identity.key for item in registrations)
    connection = services.child_waits.connection
    connection.execute(
        "UPDATE child_workflow_waits SET digest='invalid' WHERE run_key=?", (keys[0],)
    )
    connection.commit()
    assert services.child_waits.pending_keys("adk", now=1000.0, limit=1) == keys[:1]
    with pytest.raises(WorkflowExecutionError):
        services.child_waits.registration_for_key(keys[0])
    assert (
        services.child_waits.pending_keys("adk", now=1000.0, after_key=keys[0], limit=1)
        == keys[1:2]
    )
    assert services.child_waits.registration_for_key(keys[1]) is not None


def test_failed_wake_rolls_back_both_receipt_and_delivery_state(waiting_tree, monkeypatch):
    import open_workflow_agent.child_waits as module

    services, parent, _, identity, _ = waiting_tree
    registration = _arm(waiting_tree)
    insert = module.insert_completed_receipt

    def fail_after_insert(*args, **kwargs):
        insert(*args, **kwargs)
        raise WorkflowExecutionError("injected wake failure")

    monkeypatch.setattr(module, "insert_completed_receipt", fail_after_insert)
    with pytest.raises(WorkflowExecutionError, match="injected"):
        services.child_waits.wake(registration, parent, now=1000.0)
    assert services.effect_journal.get(replace(identity, operation="policy:child-wake:v1")) is None
    assert services.child_waits.registration(identity).receipt == registration.receipt
    assert services.child_waits.connection.execute(
        "SELECT state FROM child_workflow_waits WHERE run_key=?", (identity.key,)
    ).fetchone() == ("pending",)
