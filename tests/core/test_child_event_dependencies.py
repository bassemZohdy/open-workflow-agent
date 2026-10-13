"""Repeated terminal child proofs form a DAG; corrupted ancestry cycles fail."""

from __future__ import annotations

import json
import os

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.child_suspension import ChildSuspension
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.effects import (
    ChildInvocationDependency,
    EffectIdentity,
    effect_request_digest,
)
from open_workflow_agent.errors import WorkflowExecutionError
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import ExecutionState, WorkflowExecutor, compile_workflow


def _source(name):
    return {
        "document": {
            "dsl": "1.0.3",
            "namespace": "dependency-dag",
            "name": name,
            "version": "1.0.0",
        },
        "do": [{"finish": {"set": {"done": True}}}],
    }


def _child_outcome(tmp_path, backend, *, archive_definitions):
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and datasource is None:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    services = RuntimeServices(
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}}),
        model=FakeModel(),
        database_root=None if datasource else tmp_path,
    )
    assert services.invocations.connection.kind == backend
    assert services.effect_journal.connection.kind == backend
    parent_plan, child_plan = (
        compile_workflow(_source("parent")),
        compile_workflow(_source("child")),
    )
    services.workflow_catalog.register(child_plan.source)
    parent = services.invocations.create(
        engine="adk",
        session_id=None,
        user_id="correlation",
        owner_principal="owner",
        workflow_name=parent_plan.name,
        workflow_version=parent_plan.version,
        workflow_fingerprint=parent_plan.fingerprint,
    )
    original = {"original": True}
    services.invocations.bind_input(parent, original)
    parent._execution_lease = services.execution_ownership.acquire(parent, seconds=300)
    if archive_definitions:
        services.recovery_definitions.admit(
            parent_plan, parent, original, execution_lease=parent._execution_lease
        )
    reference = "/do/0/start"
    identity = EffectIdentity(
        parent.invocation_id,
        parent.workflow_fingerprint,
        reference,
        ((reference, 1), (reference + "/@attempt", 1)),
        "run:workflow",
    )
    child = services.children.reserve(identity, parent, child_plan, original)
    if archive_definitions:
        child._execution_lease = services.execution_ownership.acquire(child, seconds=300)
        try:
            services.recovery_definitions.admit(
                child_plan, child, original, execution_lease=child._execution_lease
            )
        finally:
            services.execution_ownership.release(child._execution_lease)
            child._execution_lease = None
    digest = effect_request_digest(
        "run:workflow", {"workflow_fingerprint": child_plan.fingerprint, "input": original}
    )
    services.effect_journal.begin(
        identity, digest, "original-action", execution_lease=parent._execution_lease
    )
    registration = services.child_waits.arm(
        identity, parent, child, workflow_source=parent_plan.source, deadline_at=None
    )
    services.invocations.update(child, status="completed", output={"actual": "child-outcome"})
    services.child_waits.wake(registration, parent, now=500.0)
    receipt = services.effect_journal.finish_child(
        identity,
        digest,
        child.invocation_id,
        execution_lease=parent._execution_lease,
        output=child.output,
    )
    services.child_waits.consume(registration, parent)
    try:
        yield services, parent, child, identity, receipt, child_plan, original
    finally:
        # The deliberate cycle case corrupts terminal root ancestry; release
        # its original lease without attempting a second identity mutation.
        if parent.status in {"running", "waiting"}:
            services.invocations.update(parent, status="cancelled")
        services.execution_ownership.release(parent._execution_lease)
        services.close()


@pytest.fixture(params=["sqlite", "postgresql"])
def child_outcome(tmp_path, request):
    # Common terminal-DAG evidence remains readable without legacy archives.
    # The deliberate ancestry corruption must reach the cycle guard, rather
    # than fail an earlier archived parent-identity comparison.
    yield from _child_outcome(tmp_path, request.param, archive_definitions=False)


@pytest.fixture(params=["sqlite", "postgresql"])
def archived_child_outcome(tmp_path, request):
    # Nonterminal continuation cannot adopt missing original definition proof.
    yield from _child_outcome(tmp_path, request.param, archive_definitions=True)


def test_event_recovery_accepts_same_child_in_wake_and_action_outcomes(child_outcome):
    services, parent, child, identity, receipt, _, _ = child_outcome
    assert receipt.child_invocation.invocation_id == child.invocation_id
    services.effect_journal.verify_invocation(parent.invocation_id, parent.workflow_fingerprint)
    services.event_store.verify_invocation(parent.invocation_id, parent.workflow_fingerprint)
    services.child_waits.verify_invocation(parent.invocation_id, parent.workflow_fingerprint)
    assert services.effect_journal.get(identity) == receipt
    assert services.model.calls == []


def test_event_recovery_rejects_real_back_edge_despite_duplicate_child_proofs(child_outcome):
    services, parent, child, _, _, _, original = child_outcome
    services.invocations.update(parent, status="completed", output={"actual": "parent-outcome"})
    # Controlled stored-record damage creates a back edge that the ordinary
    # admission API cannot produce. Both immutable terminal dependencies are
    # otherwise real and individually readable, so traversal must catch it.
    payload = json.loads(parent._persisted_payload)
    payload["parent_invocation_id"] = child.invocation_id
    connection = services.effect_journal.connection
    connection.execute(
        "UPDATE invocations SET payload=? WHERE invocation_id=?",
        (json.dumps(payload), parent.invocation_id),
    )
    connection.commit()
    back_edge = EffectIdentity(
        child.invocation_id,
        child.workflow_fingerprint,
        "/do/0/back",
        (("/do/0/back", 1),),
        "run:workflow",
    )
    digest = effect_request_digest(
        "run:workflow", {"workflow_fingerprint": parent.workflow_fingerprint, "input": original}
    )
    services.effect_journal.begin(back_edge, digest, "corrupted-back-edge")
    services.effect_journal.finish(
        back_edge,
        "corrupted-back-edge",
        output=parent.output,
        child_invocation=ChildInvocationDependency.capture(
            services.invocations, parent.invocation_id
        ),
    )
    assert (
        services.effect_journal.get(back_edge).child_invocation.invocation_id
        == parent.invocation_id
    )
    with pytest.raises(WorkflowExecutionError, match="dependencies could not be reconciled"):
        services.effect_journal.verify_invocation(parent.invocation_id, parent.workflow_fingerprint)
    with pytest.raises(WorkflowExecutionError, match="event invocation evidence"):
        services.event_store.verify_invocation(parent.invocation_id, parent.workflow_fingerprint)
    assert services.model.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["missing", "policy", "count"])
async def test_terminal_child_replay_rejects_damaged_root_budget_without_new_dispatch(
    child_outcome, damage
):
    services, parent, child, identity, receipt, child_plan, original = child_outcome
    executor = WorkflowExecutor(services.catalog, services=services)
    state = ExecutionState(
        data=original,
        execution_path=identity.occurrence,
        metadata={
            "invocation_id": parent.invocation_id,
            "_workflow_fingerprint": parent.workflow_fingerprint,
            "_invocation_handle": parent,
        },
        variables={
            "_task_reference": identity.task_reference,
            "_execution_reference": identity.task_reference,
        },
    )
    run = {
        "workflow": {
            "namespace": child_plan.namespace,
            "name": child_plan.name,
            "version": child_plan.version,
        }
    }

    async def forbidden(*args, **kwargs):
        raise AssertionError("known terminal outcome must not dispatch another child")

    services.workflow_runner = forbidden
    assert await executor._run_subworkflow(run, state) == child.output
    connection = services.children.connection
    if damage == "missing":
        connection.execute(
            "DELETE FROM child_workflow_budgets WHERE root_id=?", (parent.invocation_id,)
        )
    elif damage == "policy":
        connection.execute(
            "UPDATE child_workflow_budgets SET max_depth=64 WHERE root_id=?",
            (parent.invocation_id,),
        )
    else:
        connection.execute(
            "UPDATE child_workflow_budgets SET used=0 WHERE root_id=?", (parent.invocation_id,)
        )
    connection.commit()
    parent_before = services.invocations.get(parent.invocation_id)._persisted_payload
    child_before = services.invocations.get(child.invocation_id)._persisted_payload
    with pytest.raises(WorkflowExecutionError):
        await executor._run_subworkflow(run, state)
    assert services.invocations.get(parent.invocation_id)._persisted_payload == parent_before
    assert services.invocations.get(child.invocation_id)._persisted_payload == child_before
    row = connection.execute(
        "SELECT payload FROM workflow_effects WHERE identity=?", (identity.key,)
    ).fetchone()
    assert (
        json.loads(row[0])["child_invocation"]["record_digest"]
        == receipt.child_invocation.record_digest
    )
    assert services.model.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", [False, True])
async def test_live_child_can_finish_its_action_but_terminal_tree_cannot_bless_uncertainty(
    archived_child_outcome, terminal
):
    services, parent, _, _, _, child_plan, original = archived_child_outcome
    reference = "/do/1/live"
    identity = EffectIdentity(
        parent.invocation_id,
        parent.workflow_fingerprint,
        reference,
        ((reference, 1), (reference + "/@attempt", 1)),
        "run:workflow",
    )
    child = services.children.reserve(identity, parent, child_plan, original)
    child._execution_lease = services.execution_ownership.acquire(child, seconds=300)
    services.recovery_definitions.admit(
        child_plan, child, original, execution_lease=child._execution_lease
    )
    nested_lease = None
    digest = effect_request_digest(
        "run:workflow", {"workflow_fingerprint": child_plan.fingerprint, "input": original}
    )
    services.effect_journal.begin(
        identity, digest, "original-parent-action", execution_lease=parent._execution_lease
    )
    services.child_waits.arm(
        identity, parent, child, workflow_source=_source("parent"), deadline_at=None
    )
    leaf = child
    if terminal:
        # A terminal internal child with a cancelled nested child still owns
        # the uncertainty of an external action dispatched in that leaf.
        nested = EffectIdentity(
            child.invocation_id,
            child.workflow_fingerprint,
            "/do/0/nested",
            (("/do/0/nested", 1),),
            "run:workflow",
        )
        leaf = services.children.reserve(nested, child, child_plan, original)
        nested_lease = services.execution_ownership.acquire(leaf, seconds=300)
        leaf._execution_lease = nested_lease
        services.recovery_definitions.admit(
            child_plan, leaf, original, execution_lease=nested_lease
        )
        services.effect_journal.begin(
            nested, digest, "nested-action", execution_lease=child._execution_lease
        )
    external = EffectIdentity(
        leaf.invocation_id,
        leaf.workflow_fingerprint,
        "/do/0/http",
        (("/do/0/http", 1),),
        "call:http",
    )
    services.effect_journal.begin(
        external,
        effect_request_digest("call:http", {"original": True}),
        "still-dispatched",
        execution_lease=leaf._execution_lease,
    )
    if terminal:
        services.invocations.update(leaf, status="cancelled")
        services.invocations.update(child, status="cancelled")
    executor = WorkflowExecutor(services.catalog, services=services)
    state = ExecutionState(
        data=original,
        execution_path=identity.occurrence,
        metadata={
            "invocation_id": parent.invocation_id,
            "_workflow_fingerprint": parent.workflow_fingerprint,
            "_workflow_source": _source("parent"),
            "_invocation_handle": parent,
        },
        variables={"_task_reference": reference, "_execution_reference": reference},
    )

    async def forbidden(*args, **kwargs):
        raise AssertionError("pending child action cannot be redispatched")

    services.workflow_runner = forbidden
    try:
        if terminal:
            with pytest.raises(WorkflowExecutionError, match="unresolved execution intent"):
                await executor._run_subworkflow(
                    {
                        "workflow": {
                            "namespace": child_plan.namespace,
                            "name": child_plan.name,
                            "version": child_plan.version,
                        }
                    },
                    state,
                )
        else:
            with pytest.raises(ChildSuspension) as pending:
                await executor._run_subworkflow(
                    {
                        "workflow": {
                            "namespace": child_plan.namespace,
                            "name": child_plan.name,
                            "version": child_plan.version,
                        }
                    },
                    state,
                )
            assert pending.value.child_id == child.invocation_id
            assert child.status == "running" and parent.status == "waiting"
        assert services.effect_journal.get(identity).status == "pending"
        assert services.effect_journal.get(external).status == "pending"
        assert services.model.calls == []
    finally:
        if nested_lease is not None:
            services.execution_ownership.release(nested_lease)
            leaf._execution_lease = None
        services.execution_ownership.release(child._execution_lease)
        child._execution_lease = None
