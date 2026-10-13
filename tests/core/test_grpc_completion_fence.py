"""Known unary replies cannot cross the original execution generation fence."""

import os
from dataclasses import replace
from uuid import uuid4

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.effects import EffectIdentity, effect_request_digest
from open_workflow_agent.errors import InvocationStateConflict
from open_workflow_agent.services import RuntimeServices


@pytest.fixture(params=["sqlite", "postgresql"])
def pending_call(tmp_path, request):
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if request.param == "postgresql" else None
    if request.param == "postgresql" and datasource is None:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    services = RuntimeServices(
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}}),
        model=FakeModel(),
        database_root=None if datasource else tmp_path,
    )
    handle = services.invocations.create(
        engine="langgraph",
        session_id=None,
        user_id=None,
        workflow_name=f"grpc-fence-{uuid4().hex}",
        workflow_version="1.0.0",
        workflow_fingerprint=uuid4().hex,
    )
    lease = services.execution_ownership.acquire(handle, seconds=60)
    identity = EffectIdentity(
        handle.invocation_id,
        handle.workflow_fingerprint,
        "/do/0/call/peer:1.0.0@trusted",
        (("/do/0/remote", 1),),
        "call:grpc",
    )
    receipt = services.effect_journal.begin(
        identity,
        effect_request_digest("grpc", {"arguments": {"value": None}}),
        "original-intent-owner",
        execution_lease=lease,
        operation_id="original-key@trusted",
    )
    try:
        yield services, handle, lease, identity, receipt
    finally:
        try:
            services.execution_ownership.release(lease)
        except InvocationStateConflict:
            pass  # Expiry/takeover are this fixture's intentional evidence.
        finally:
            services.close()


def snapshot(services, handle):
    return {
        table: services.effect_journal.connection.execute(
            f"SELECT * FROM {table} WHERE "
            + (
                "invocation_id=?"
                if table != "workflow_effects"
                else "identity IN "
                "(SELECT identity FROM workflow_effect_invocations WHERE invocation_id=?)"
            ),
            (handle.invocation_id,),
        ).fetchall()
        for table in ("workflow_effects", "workflow_effect_invocations", "invocation_ownership")
    }


@pytest.mark.parametrize("outcome", ["value", "null", "fault"])
def test_current_unary_owner_retains_one_immutable_known_outcome(pending_call, outcome):
    services, _handle, lease, identity, pending = pending_call
    error = (
        {"code": "tool_error", "message": "known remote fault", "details": {}}
        if outcome == "fault"
        else None
    )
    output = {"value": 7} if outcome == "value" else None
    completed = services.effect_journal.finish_grpc(
        identity,
        pending.owner,
        execution_lease=lease,
        output=output,
        error=error,
        error_status_code=404 if error else None,
    )
    assert completed.status == ("faulted" if error else "completed")
    assert completed.output == output and completed.error == error
    assert completed.operation_id == pending.operation_id
    assert (
        services.effect_journal.finish_grpc(
            identity,
            pending.owner,
            execution_lease=lease,
            output={"late": True},
        )
        == completed
    )


@pytest.mark.parametrize("loss", ["takeover", "expired", "missing-lease", "other-invocation"])
def test_late_unary_reply_cannot_complete_or_mutate_common_evidence(pending_call, loss):
    services, handle, lease, identity, pending = pending_call
    connection = services.effect_journal.connection
    if loss in {"takeover", "expired"}:
        connection.execute(
            "UPDATE invocation_ownership SET expires_at=1 WHERE invocation_id=?",
            (handle.invocation_id,),
        )
        connection.commit()
    if loss == "takeover":
        successor = services.execution_ownership.acquire(handle, seconds=60)
        assert successor.generation == lease.generation + 1
    supplied = None if loss == "missing-lease" else lease
    if loss == "other-invocation":
        supplied = replace(lease, invocation_id=uuid4().hex)
    before = snapshot(services, handle)
    with pytest.raises(InvocationStateConflict):
        services.effect_journal.finish_grpc(
            identity,
            pending.owner,
            execution_lease=supplied,
            output={"late": True},
        )
    assert snapshot(services, handle) == before
    assert services.effect_journal.get(identity) == pending
    assert services.model.calls == []


def test_expiry_during_outcome_verification_rolls_back_the_reply(pending_call, monkeypatch):
    services, handle, lease, identity, pending = pending_call
    original = services.effect_journal.get
    calls = 0

    def expire_before_commit(selected):
        nonlocal calls
        recorded = original(selected)
        calls += 1
        if calls == 2:
            services.effect_journal.connection.execute(
                "UPDATE invocation_ownership SET expires_at=1 WHERE invocation_id=?",
                (handle.invocation_id,),
            )
        return recorded

    before = snapshot(services, handle)
    monkeypatch.setattr(services.effect_journal, "get", expire_before_commit)
    with pytest.raises(InvocationStateConflict):
        services.effect_journal.finish_grpc(
            identity, pending.owner, execution_lease=lease, output=7
        )
    assert snapshot(services, handle) == before
    assert original(identity) == pending
