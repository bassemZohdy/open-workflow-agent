"""Common job ledger controls, not supervisor/native lifetime qualification."""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, replace

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.effects import EffectIdentity, effect_request_digest
from open_workflow_agent.errors import InvocationStateConflict, OwaError, WorkflowExecutionError
from open_workflow_agent.sandbox import compile_sandbox_workflow
from open_workflow_agent.sandbox.contract import (
    SandboxExecutionRequest,
    SandboxExecutionResult,
    SandboxSecretReference,
)
from open_workflow_agent.sandbox.jobs import (
    SandboxJobBackendBinding,
    SandboxJobLease,
    SandboxJobProfile,
    SandboxJobStore,
    job_id,
    policy_identity,
    verify_job_admission_receipt,
)
from open_workflow_agent.services import RuntimeServices


@pytest.fixture(params=["sqlite", "postgresql"])
def jobs(tmp_path, request):
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if request.param == "postgresql" else None
    if request.param == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    config = RuntimeConfig.model_validate(
        {
            "persistence": {"datasource": datasource},
            "sandbox": {
                "enabled": True,
                "allow_shell": True,
                "timeout_seconds": 300,
                "workspace_root": str(tmp_path / "private-workspace"),
                "secret_environment": ["TOKEN_REFERENCE"],
            },
        }
    )
    services = RuntimeServices(
        config, model=FakeModel(), database_root=None if datasource else tmp_path
    )
    assert services.effect_journal.connection.kind == request.param
    services.recovery_definitions.profile_provider = lambda engine: {
        "profile": "sandbox-job-store-test",
        "engine": engine,
        "backend": request.param,
        "continuation_supported": True,
    }
    # The pure ledger uses an admitted synchronous executable declaration.
    # Runtime await:false/supervisor activation is an independent later gate.
    plan = compile_sandbox_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "jobs",
                "name": "store-" + uuid.uuid4().hex,
                "version": "1.0.0",
            },
            "do": [{"job": {"run": {"await": True, "shell": {"command": "approved-command"}}}}],
        },
        sandbox=config.sandbox,
    )
    handle = services.invocations.create(
        engine="portable",
        session_id="context",
        user_id="user",
        owner_principal="original-owner",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    lease = services.execution_ownership.acquire(handle, seconds=300)
    handle._execution_lease = lease
    services.recovery_definitions.admit(plan, handle, {"raw": "original"}, execution_lease=lease)
    identity = EffectIdentity(
        handle.invocation_id,
        plan.fingerprint,
        plan.tasks[0].reference,
        ((plan.tasks[0].reference, 1),),
        "run:shell",
    )
    state = {
        "profile": SandboxJobProfile("internal", "supervisor-original", "1.0.0", "a" * 64),
        "deadline": time.time() + 120,
    }
    store = SandboxJobStore(services, profile_provider=lambda: state["profile"])
    execution = SandboxExecutionRequest(
        job_id(identity),
        "shell",
        command="approved-command",
        stdin="transformed input",
        arguments=("--finite",),
        environment=(("TOKEN", SandboxSecretReference("TOKEN_REFERENCE")),),
        invocation_id=handle.invocation_id,
        task_reference=identity.task_reference,
    )
    try:
        yield services, store, handle, identity, execution, state
    finally:
        if datasource:
            # Capacity is global; remove only this fixture's private job rows.
            connection = services.effect_journal.connection
            # Some corruption cases deliberately delete the admission row while
            # retaining its delivery row. Track the fixture's deterministic job
            # IDs as well as rows still discoverable through the admission table.
            second_identity = replace(identity, occurrence=((identity.task_reference, 2),))
            identifiers = {
                job_id(identity),
                job_id(second_identity),
                *(
                    row[0]
                    for row in connection.execute(
                        "SELECT job_id FROM sandbox_jobs WHERE invocation_id=?",
                        (handle.invocation_id,),
                    ).fetchall()
                ),
            }
            for identifier in identifiers:
                for table in ("sandbox_job_delivery", "sandbox_job_ownership", "sandbox_jobs"):
                    connection.execute(f"DELETE FROM {table} WHERE job_id=?", (identifier,))
            connection.commit()
        if handle._execution_lease is not None:
            try:
                services.execution_ownership.release(handle._execution_lease)
            except InvocationStateConflict:
                pass
        services.close()


def admit(tree, **overrides):
    _, store, handle, identity, execution, state = tree
    return store.admit(
        overrides.pop("identity", identity),
        handle,
        overrides.pop("request", execution),
        overrides.pop("input", {"transformed": None}),
        deadline_at=overrides.pop("deadline_at", state["deadline"]),
        execution_lease=overrides.pop("execution_lease", handle._execution_lease),
        **overrides,
    )


def binding(job):
    value = json.loads(job.admission_json)
    return SandboxJobBackendBinding(
        job.job_id,
        value["request_digest"],
        value["backend_profile"]["deployment_id"],
        "actual-controller-instance",
    )


def outcome(job, **values):
    return SandboxExecutionResult(
        job.job_id,
        values.get("exit_code", 0),
        values.get("stdout", "bounded output"),
        values.get("stderr", ""),
        values.get("duration", 1.0),
    )


def snapshot(tree):
    connection = tree[0].effect_journal.connection
    return tuple(
        (
            table,
            connection.execute(
                f"SELECT * FROM {table} ORDER BY "
                + ("identity" if table.startswith("workflow_") else "job_id")
            ).fetchall(),
        )
        for table in (
            "sandbox_jobs",
            "sandbox_job_delivery",
            "sandbox_job_ownership",
            "workflow_effects",
            "workflow_effect_invocations",
        )
    )


def test_admission_is_atomic_frozen_acceptance_not_process_completion(jobs, monkeypatch):
    monkeypatch.setenv("TOKEN_REFERENCE", "resolved-secret-must-not-be-saved")
    services, store, handle, identity, execution, state = jobs
    job = admit(jobs)
    assert job.status == "ready" and job.submit_json is None and job.terminal_json is None
    assert store.pending() == (job.job_id,)
    assert job.transformed_input == {"transformed": None}
    changed = job.transformed_input
    changed["transformed"] = "changed"
    assert job.transformed_input == {"transformed": None}
    raw = json.loads(job.admission_json)
    assert raw["deadline_at"] == state["deadline"]
    assert raw["request"]["environment"] == {"TOKEN": {"secret_reference": "TOKEN_REFERENCE"}}
    assert "resolved-secret-must-not-be-saved" not in job.admission_json
    assert job.receipt.status == "completed" and job.receipt.output == raw
    verify_job_admission_receipt(store, policy_identity(identity), job.receipt)
    assert services.effect_journal.get(identity) is None and services.model.calls == []
    assert admit(jobs) == job


@pytest.mark.parametrize(
    "untrusted",
    [{"transformed": (1, 2)}, {"transformed": object()}, {"transformed": float("inf")}],
    ids=["application-tuple", "application-object", "nonfinite-number"],
)
def test_private_identity_serialization_never_coerces_invalid_application_input(jobs, untrusted):
    services, _, handle = jobs[:3]
    before = snapshot(jobs)
    original = services.invocations.read_input(handle)
    with pytest.raises(OwaError):
        admit(jobs, input=untrusted)
    assert snapshot(jobs) == before
    assert services.invocations.read_input(handle) == original and services.model.calls == []


@pytest.mark.parametrize("change", ["request", "input", "deadline", "profile", "lifetime"])
def test_original_acceptance_cannot_be_rebound(jobs, change):
    job = admit(jobs)
    _, store, _, _, execution, state = jobs
    before = snapshot(jobs)
    options = {}
    if change == "request":
        options["request"] = replace(execution, command="different-command")
    elif change == "input":
        options["input"] = {"transformed": "different"}
    elif change == "deadline":
        options["deadline_at"] = state["deadline"] + 1
    elif change == "profile":
        state["profile"] = replace(state["profile"], revision="2.0.0")
    else:
        options["lifetime"] = "parent-linked"
    with pytest.raises(OwaError):
        admit(jobs, **options)
    assert snapshot(jobs) == before and store.get(job.job_id).status == "ready"


@pytest.mark.parametrize("boundary", ["before-receipt", "after-receipt"])
def test_admission_row_ready_and_receipt_index_rollback_as_one_transaction(
    jobs, monkeypatch, boundary
):
    from open_workflow_agent.sandbox import jobs as module

    original = module.insert_completed_receipt
    before = snapshot(jobs)

    class Stopped(BaseException):
        pass

    def stop(connection, identity, receipt):
        if boundary == "after-receipt":
            original(connection, identity, receipt)
        raise Stopped

    monkeypatch.setattr(module, "insert_completed_receipt", stop)
    with pytest.raises(Stopped):
        admit(jobs)
    assert snapshot(jobs) == before


@pytest.mark.parametrize(
    "damage",
    [
        "admission",
        "delivery",
        "digest",
        "receipt",
        "index",
        "input",
        "archive",
        "owner",
        "state",
        "oversize",
        "duplicate",
    ],
)
def test_corrupted_original_evidence_rejects_before_claim_or_generation_mutation(jobs, damage):
    services, store, handle, identity, _, _ = jobs
    job = admit(jobs)
    connection = services.effect_journal.connection
    if damage in {"admission", "delivery"}:
        table = "sandbox_jobs" if damage == "admission" else "sandbox_job_delivery"
        connection.execute(f"DELETE FROM {table} WHERE job_id=?", (job.job_id,))
    elif damage == "digest":
        connection.execute(
            "UPDATE sandbox_jobs SET digest=? WHERE job_id=?", ("0" * 64, job.job_id)
        )
    elif damage in {"receipt", "index"}:
        table = "workflow_effects" if damage == "receipt" else "workflow_effect_invocations"
        connection.execute(
            f"DELETE FROM {table} WHERE identity=?", (policy_identity(identity).key,)
        )
    elif damage == "input":
        connection.execute(
            "DELETE FROM invocation_inputs WHERE invocation_id=?", (handle.invocation_id,)
        )
    elif damage == "archive":
        connection.execute(
            "DELETE FROM recovery_definitions WHERE fingerprint=?", (handle.workflow_fingerprint,)
        )
    elif damage == "owner":
        row = json.loads(
            connection.execute(
                "SELECT payload FROM invocations WHERE invocation_id=?", (handle.invocation_id,)
            ).fetchone()[0]
        )
        row["owner_principal"] = "foreign"
        connection.execute(
            "UPDATE invocations SET payload=? WHERE invocation_id=?",
            (json.dumps(row), handle.invocation_id),
        )
    elif damage == "state":
        connection.execute(
            "UPDATE sandbox_job_delivery SET status='accepted' WHERE job_id=?", (job.job_id,)
        )
    elif damage == "oversize":
        connection.execute(
            "UPDATE sandbox_jobs SET payload=? WHERE job_id=?", ("x" * 1_048_577, job.job_id)
        )
    else:
        connection.execute(
            "UPDATE sandbox_jobs SET payload=? WHERE job_id=?",
            ('{"profile":"owa/sandbox-job/v1","profile":"foreign"}', job.job_id),
        )
    connection.commit()
    before = snapshot(jobs)
    with pytest.raises(OwaError):
        store.claim(job.job_id)
    assert snapshot(jobs) == before


@pytest.mark.parametrize("damage", ["definition-receipt", "definition-index"])
def test_lost_original_definition_authority_rejects_before_job_claim(jobs, damage):
    from open_workflow_agent.recovery_definitions import admission_identity

    services, store, handle, _, _, _ = jobs
    job = admit(jobs)
    definition = admission_identity(handle)
    table = "workflow_effects" if damage == "definition-receipt" else "workflow_effect_invocations"
    services.effect_journal.connection.execute(
        f"DELETE FROM {table} WHERE identity=?", (definition.key,)
    )
    services.effect_journal.connection.commit()
    before = snapshot(jobs)

    with pytest.raises(OwaError):
        store.claim(job.job_id)

    assert snapshot(jobs) == before


@pytest.mark.parametrize(
    "authority", ["missing-lease", "stale-parent", "foreign-job", "foreign-invocation"]
)
def test_admission_requires_real_original_invocation_ownership(jobs, authority):
    services, _, handle, identity, execution, _ = jobs
    options = {}
    if authority == "missing-lease":
        options["execution_lease"] = None
    elif authority == "stale-parent":
        services.execution_ownership.release(handle._execution_lease)
        renewed = services.execution_ownership.acquire(handle, seconds=300)
        options["execution_lease"] = handle._execution_lease
        handle._execution_lease = renewed
    elif authority == "foreign-job":
        options["request"] = replace(execution, execution_id="job_" + "0" * 64)
    else:
        options["identity"] = replace(identity, invocation_id="foreign")
    before = snapshot(jobs)
    with pytest.raises(OwaError):
        admit(jobs, **options)
    assert snapshot(jobs) == before


@pytest.mark.parametrize(
    "environment",
    [
        (("TOKEN", "literal"),),
        (("TOKEN", "resolved-secret"),),
        (
            ("TOKEN", SandboxSecretReference("REFERENCE")),
            ("TOKEN", SandboxSecretReference("OTHER")),
        ),
    ],
)
def test_durable_environment_profile_rejects_literals_resolved_values_and_duplicate_names(
    jobs, environment
):
    before = snapshot(jobs)
    with pytest.raises(OwaError):
        admit(jobs, request=replace(jobs[4], environment=environment))
    assert snapshot(jobs) == before


@pytest.mark.parametrize("parent_state", ["completed", "cancelled"])
def test_independent_job_owner_remains_valid_after_parent_terminal_without_parent_lease(
    jobs, parent_state
):
    services, store, handle, _, _, _ = jobs
    job = admit(jobs)
    services.invocations.update(
        handle,
        status=parent_state,
        completed_at=time.time(),
        output={"parent": "known"} if parent_state == "completed" else None,
    )
    services.execution_ownership.release(handle._execution_lease)
    handle._execution_lease = None
    lease = store.claim(job.job_id)
    intent = store.begin_submit(lease)
    assert intent["job_id"] == job.job_id and store.get(job.job_id).status == "submitting"
    store.release(lease)
    assert services.invocations.get(handle.invocation_id).status == parent_state


def test_two_real_services_compete_and_stale_generation_cannot_submit_or_commit(jobs):
    services, store, _, _, _, state = jobs
    job = admit(jobs)
    other = RuntimeServices(
        services.config, model=FakeModel(), database_root=services.database_root
    )
    other.recovery_definitions.profile_provider = services.recovery_definitions.profile_provider
    assert other.effect_journal.connection.kind == services.effect_journal.connection.kind
    contender = SandboxJobStore(other, profile_provider=lambda: state["profile"])
    try:
        old = store.claim(job.job_id)
        before = snapshot(jobs)
        with pytest.raises(InvocationStateConflict):
            contender.claim(job.job_id)
        assert snapshot(jobs) == before
        store.release(old)
        current = contender.claim(job.job_id)
        assert current.generation == old.generation + 1 and current.owner != old.owner
        for operation in (
            lambda: store.begin_submit(old),
            lambda: store.renew(old),
            lambda: store.mark_uncertain(old, reason="controller_lost"),
        ):
            with pytest.raises(InvocationStateConflict):
                operation()
        contender.begin_submit(current)
        contender.record_accepted(current, binding(job))
        with pytest.raises(InvocationStateConflict):
            store.record_terminal(old, binding(job), outcome(job))
        contender.record_terminal(current, binding(job), outcome(job))
        assert store.get(job.job_id).status == "terminal"
        contender.release(current)
    finally:
        other.close()


@pytest.mark.parametrize(
    "reason", ["submit_response_lost", "outcome_unavailable", "controller_lost"]
)
def test_ambiguous_submit_is_retained_and_cannot_return_to_ready_or_launch_again(jobs, reason):
    store = jobs[1]
    job = admit(jobs)
    lease = store.claim(job.job_id)
    intent = store.begin_submit(lease)
    store.mark_uncertain(lease, reason=reason)
    store.release(lease)
    recovered = store.claim(job.job_id)
    current = store.get(job.job_id)
    assert current.status == "uncertain" and current.uncertainty == reason
    assert json.loads(current.submit_json) == intent
    before = snapshot(jobs)
    with pytest.raises(WorkflowExecutionError):
        store.begin_submit(recovered)
    assert snapshot(jobs) == before
    # A trusted exact backend inspection can establish the original launch.
    store.record_accepted(recovered, binding(job))
    store.record_terminal(recovered, binding(job), outcome(job))
    assert store.get(job.job_id).status == "terminal"
    store.release(recovered)


def test_first_terminal_and_backend_binding_are_immutable_even_after_takeover(jobs):
    store = jobs[1]
    job = admit(jobs)
    lease = store.claim(job.job_id)
    store.begin_submit(lease)
    store.record_accepted(lease, binding(job))
    result = store.record_terminal(lease, binding(job), outcome(job, exit_code=7))
    before = snapshot(jobs)
    assert store.record_terminal(lease, binding(job), outcome(job, exit_code=7)) == result
    with pytest.raises(InvocationStateConflict):
        store.record_terminal(lease, binding(job), outcome(job, exit_code=0))
    with pytest.raises(InvocationStateConflict):
        store.record_accepted(lease, binding(job))
    assert snapshot(jobs) == before
    store.release(lease)
    recovered = store.claim(job.job_id)
    assert (
        store.get(job.job_id).terminal_json == result.terminal_json
        and job.job_id not in store.pending()
    )
    store.release(recovered)


def test_cancel_intent_acknowledgment_and_truthful_exit_are_independent_records(jobs):
    store = jobs[1]
    job = admit(jobs)
    lease = store.claim(job.job_id)
    store.begin_submit(lease)
    store.record_accepted(lease, binding(job))
    before = snapshot(jobs)
    with pytest.raises(InvocationStateConflict):
        store.request_control(lease, reason="cancel", principal="foreign")
    assert snapshot(jobs) == before
    control = store.request_control(lease, reason="cancel", principal="original-owner")
    assert (
        store.get(job.job_id).status == "accepted" and store.get(job.job_id).terminal_json is None
    )
    with pytest.raises(OwaError):
        store.record_terminal(lease, binding(job), outcome(job), reason="cancel")
    store.acknowledge_control(lease, binding(job), generation=control["generation"])
    assert store.get(job.job_id).terminal_json is None
    terminal = store.record_terminal(
        lease, binding(job), outcome(job, exit_code=137), reason="cancel"
    )
    assert json.loads(terminal.terminal_json)["outcome"]["exit_code"] == 137
    assert terminal.receipt == job.receipt and terminal.transformed_input == job.transformed_input
    store.release(lease)


def test_unstarted_cancel_is_explicitly_closed_without_fabricated_backend_or_exit(jobs):
    store = jobs[1]
    job = admit(jobs)
    lease = store.claim(job.job_id)
    control = store.request_control(lease, reason="cancel", principal="original-owner")
    assert store.request_control(lease, reason="cancel", principal="original-owner") == control
    with pytest.raises(WorkflowExecutionError):
        store.begin_submit(lease)
    terminal = store.settle_unstarted_control(lease)
    value = json.loads(terminal.terminal_json)
    assert (
        value["reason"] == "not_launched" and value["binding"] is None and value["outcome"] is None
    )
    assert terminal.submit_json is None and terminal.backend_json is None
    store.release(lease)


def test_original_deadline_never_rebases_and_requires_due_control(jobs, monkeypatch):
    store, state = jobs[1], jobs[5]
    job = admit(jobs)
    lease = store.claim(job.job_id)
    with pytest.raises(InvocationStateConflict):
        store.request_control(lease, reason="deadline")
    monkeypatch.setattr(store, "_now", lambda: state["deadline"] + 1)
    assert admit(jobs).admission_json == job.admission_json
    with pytest.raises(WorkflowExecutionError):
        store.begin_submit(lease)
    store.request_control(lease, reason="deadline")
    terminal = store.settle_unstarted_control(lease)
    assert json.loads(terminal.admission_json)["deadline_at"] == state["deadline"]
    store.release(lease)


def test_deadline_terminal_cause_requires_original_deadline_proof_or_control_ack(jobs):
    store = jobs[1]
    job = admit(jobs)
    lease = store.claim(job.job_id)
    store.begin_submit(lease)
    peer = binding(job)
    store.record_accepted(lease, peer)
    before = snapshot(jobs)

    with pytest.raises(OwaError):
        store.record_terminal(lease, peer, outcome(job), reason="deadline")

    assert snapshot(jobs) == before
    assert store.get(job.job_id).status == "accepted"
    store.release(lease)


@pytest.mark.parametrize("bad", ["stdout", "exit_code", "duration", "foreign-binding"])
def test_terminal_result_limits_and_original_controller_binding_fail_before_outcome_write(
    jobs, bad
):
    store = jobs[1]
    job = admit(jobs)
    lease = store.claim(job.job_id)
    store.begin_submit(lease)
    selected_binding = binding(job)
    selected_outcome = outcome(job)
    if bad == "stdout":
        selected_outcome = outcome(job, stdout="x" * 262_145)
    elif bad == "exit_code":
        selected_outcome = outcome(job, exit_code=True)
    elif bad == "duration":
        selected_outcome = outcome(job, duration=float("nan"))
    else:
        selected_binding = replace(selected_binding, controller_id="foreign-controller")
    before = snapshot(jobs)
    with pytest.raises(OwaError):
        store.record_terminal(lease, selected_binding, selected_outcome)
    assert snapshot(jobs) == before and store.get(job.job_id).terminal_json is None
    store.release(lease)


def test_job_capacity_and_cursor_scan_are_bounded_and_cannot_adopt_foreign_pending_action(jobs):
    services, store, handle, identity, execution, state = jobs
    store.max_pending = 1
    job = admit(jobs)
    second = replace(identity, occurrence=((identity.task_reference, 2),))
    second_request = replace(execution, execution_id=job_id(second))
    before = snapshot(jobs)
    with pytest.raises(WorkflowExecutionError):
        admit(jobs, identity=second, request=second_request)
    assert snapshot(jobs) == before and store.pending(limit=1) == (job.job_id,)
    assert store.pending(limit=1, after=job.job_id) == ()
    with pytest.raises(ValueError):
        store.pending(limit=0)
    services.effect_journal.begin(
        second,
        effect_request_digest("run:shell", {}),
        "foreign-owner",
        execution_lease=handle._execution_lease,
    )
    store.max_pending = 1000
    before = snapshot(jobs)
    with pytest.raises(OwaError):
        admit(jobs, identity=second, request=second_request)
    assert snapshot(jobs) == before


def test_current_deployment_revocation_rejects_before_job_claim_mutation(jobs):
    _, store, _, _, _, state = jobs
    job = admit(jobs)
    state["profile"] = replace(state["profile"], policy_digest="b" * 64)
    before = snapshot(jobs)
    with pytest.raises(InvocationStateConflict):
        store.claim(job.job_id)
    assert snapshot(jobs) == before


def test_live_pre_ipc_verification_is_read_only_and_rejects_stale_owner(jobs):
    store = jobs[1]
    job = admit(jobs)
    lease = store.claim(job.job_id)
    before = snapshot(jobs)
    assert store.verify(lease).admission_json == job.admission_json
    assert snapshot(jobs) == before
    store.release(lease)
    renewed = store.claim(job.job_id)
    before = snapshot(jobs)
    with pytest.raises(InvocationStateConflict):
        store.verify(lease)
    assert snapshot(jobs) == before
    assert store.verify(renewed).job_id == job.job_id
    store.release(renewed)


def test_exact_backend_terminal_inspection_can_resolve_lost_acceptance_without_relaunch(jobs):
    store = jobs[1]
    job = admit(jobs)
    lease = store.claim(job.job_id)
    intent = store.begin_submit(lease)
    store.mark_uncertain(lease, reason="submit_response_lost")
    store.release(lease)
    recovered = store.claim(job.job_id)
    terminal = store.record_terminal(recovered, binding(job), outcome(job))
    assert terminal.backend_json is None and terminal.status == "terminal"
    assert json.loads(terminal.submit_json) == intent
    assert json.loads(terminal.terminal_json)["binding"] == asdict(binding(job))
    assert terminal.receipt == job.receipt
    store.release(recovered)


@pytest.mark.parametrize("damage", ["submit", "backend", "control", "control_ack", "terminal"])
def test_mutable_stage_cannot_disagree_with_its_independent_immutable_receipt(jobs, damage):
    services, store = jobs[:2]
    job = admit(jobs)
    lease = store.claim(job.job_id)
    store.begin_submit(lease)
    store.record_accepted(lease, binding(job))
    if damage == "terminal":
        store.record_terminal(lease, binding(job), outcome(job))
    elif damage in {"control", "control_ack"}:
        control = store.request_control(lease, reason="cancel", principal="original-owner")
        if damage == "control_ack":
            store.acknowledge_control(lease, binding(job), generation=control["generation"])
    store.release(lease)
    column = damage + "_json"
    services.effect_journal.connection.execute(
        f"UPDATE sandbox_job_delivery SET {column}=? WHERE job_id=?",
        ('{"job_id":"foreign"}', job.job_id),
    )
    services.effect_journal.connection.commit()
    before = snapshot(jobs)
    with pytest.raises(OwaError):
        store.claim(job.job_id)
    assert snapshot(jobs) == before


def test_damaged_candidate_can_be_skipped_with_cursor_without_hiding_later_job(jobs):
    services, store, _, identity, execution, _ = jobs
    first = admit(jobs)
    next_identity = replace(identity, occurrence=((identity.task_reference, 2),))
    second = admit(
        jobs, identity=next_identity, request=replace(execution, execution_id=job_id(next_identity))
    )
    ordered = sorted([first.job_id, second.job_id])
    services.effect_journal.connection.execute(
        "UPDATE sandbox_job_delivery SET status='foreign-state' WHERE job_id=?", (ordered[0],)
    )
    services.effect_journal.connection.commit()
    assert store.pending(limit=1) == (ordered[0],)
    with pytest.raises(OwaError):
        store.claim(ordered[0])
    assert store.pending(limit=1, after=ordered[0]) == (ordered[1],)
    lease = store.claim(ordered[1])
    assert store.verify(lease).status == "ready"
    store.release(lease)


def test_supervisor_verification_is_select_only_without_services_or_plan_compilation(
    jobs, monkeypatch
):
    from open_workflow_agent import recovery_definitions
    from open_workflow_agent.sandbox.job_backend import SandboxJobLaunch
    from open_workflow_agent.sandbox.job_validation import verify_backend_launch
    from open_workflow_agent.storage import StorageConnection

    services, store = jobs[:2]
    job = admit(jobs)
    lease = store.claim(job.job_id)
    store.begin_submit(lease)
    launch = SandboxJobLaunch.from_verified_job(store.get(job.job_id), lease)
    statements = []
    original = StorageConnection.execute

    def record(connection, sql, parameters=()):
        if connection is services.effect_journal.connection:
            statements.append(sql)
        return original(connection, sql, parameters)

    def forbidden(*args, **kwargs):
        raise AssertionError("supervisor must not bootstrap or compile a plan")

    before = snapshot(jobs)
    with monkeypatch.context() as guard:
        guard.setattr(StorageConnection, "execute", record)
        guard.setattr(RuntimeServices, "__init__", forbidden)
        guard.setattr(recovery_definitions, "_plan", forbidden)
        verify_backend_launch(
            services.effect_journal.connection, launch, expected_profile=launch.profile
        )
    assert statements and all(statement.lstrip().startswith("SELECT ") for statement in statements)
    assert snapshot(jobs) == before and services.model.calls == []
    store.release(lease)


@pytest.mark.parametrize(
    "damage",
    [
        "lease",
        "admission",
        "request",
        "profile",
        "nonce",
        "index",
        "source",
        "input",
        "creator",
        "clock",
    ],
)
def test_supervisor_repeats_original_readonly_authority_before_process_mutation(jobs, damage):
    from open_workflow_agent.sandbox.job_backend import SandboxJobLaunch
    from open_workflow_agent.sandbox.job_validation import verify_backend_launch
    from open_workflow_agent.timing import root_clock_identity

    services, store, handle, identity = jobs[:4]
    clock_identity = root_clock_identity(handle.invocation_id, handle.workflow_fingerprint)
    services.effect_journal.record_value(
        clock_identity,
        effect_request_digest("policy:clock:v1", {"profile": "owa/timing/v1"}),
        lambda: {"profile": "owa/timing/v1", "started_at": time.time()},
        execution_lease=handle._execution_lease,
    )
    job = admit(jobs)
    lease = store.claim(job.job_id)
    store.begin_submit(lease)
    launch = SandboxJobLaunch.from_verified_job(store.get(job.job_id), lease)
    connection = services.effect_journal.connection
    if damage == "lease":
        store.release(lease)
    elif damage == "admission":
        connection.execute(
            "UPDATE sandbox_jobs SET digest=? WHERE job_id=?", ("0" * 64, job.job_id)
        )
    elif damage == "request":
        launch = replace(launch, request=replace(launch.request, command="foreign"))
    elif damage == "profile":
        launch = replace(launch, profile=replace(launch.profile, revision="foreign"))
    elif damage == "nonce":
        launch = replace(launch, submit_id="foreign")
    elif damage == "index":
        connection.execute(
            "DELETE FROM workflow_effect_invocations WHERE identity=?",
            (policy_identity(identity).key,),
        )
    elif damage == "source":
        connection.execute(
            "UPDATE recovery_definitions SET payload=? WHERE fingerprint=?",
            ('{"source":"foreign"}', handle.workflow_fingerprint),
        )
    elif damage == "input":
        connection.execute(
            "UPDATE invocation_inputs SET payload=? WHERE invocation_id=?",
            ('{"changed":true}', handle.invocation_id),
        )
    elif damage == "creator":
        value = json.loads(
            connection.execute(
                "SELECT payload FROM invocations WHERE invocation_id=?", (handle.invocation_id,)
            ).fetchone()[0]
        )
        value["owner_principal"] = "foreign"
        connection.execute(
            "UPDATE invocations SET payload=? WHERE invocation_id=?",
            (json.dumps(value), handle.invocation_id),
        )
    elif damage == "clock":
        connection.execute("DELETE FROM workflow_effects WHERE identity=?", (clock_identity.key,))
    connection.commit()
    before = snapshot(jobs)
    with pytest.raises(OwaError):
        verify_backend_launch(connection, launch, expected_profile=jobs[5]["profile"])
    assert snapshot(jobs) == before


def test_supervisor_control_requires_exact_persisted_generation_and_never_submits_controlled_job(
    jobs,
):
    from open_workflow_agent.sandbox.job_backend import SandboxJobLaunch
    from open_workflow_agent.sandbox.job_validation import verify_backend_launch

    services, store = jobs[:2]
    job = admit(jobs)
    lease = store.claim(job.job_id)
    store.begin_submit(lease)
    store.record_accepted(lease, binding(job))
    launch = SandboxJobLaunch.from_verified_job(store.get(job.job_id), lease)
    store.request_control(lease, reason="cancel", principal="original-owner")
    before = snapshot(jobs)
    with pytest.raises(OwaError):
        verify_backend_launch(
            services.effect_journal.connection,
            launch,
            expected_profile=launch.profile,
            purpose="submit",
        )
    with pytest.raises(OwaError):
        verify_backend_launch(
            services.effect_journal.connection,
            launch,
            expected_profile=launch.profile,
            purpose="control",
            control_generation=2,
            reason="cancel",
        )
    verify_backend_launch(
        services.effect_journal.connection,
        launch,
        expected_profile=launch.profile,
        purpose="control",
        control_generation=1,
        reason="cancel",
    )
    verify_backend_launch(
        services.effect_journal.connection,
        launch,
        expected_profile=launch.profile,
        purpose="inspect",
    )
    assert snapshot(jobs) == before
    store.release(lease)


@pytest.mark.parametrize("control", [None, "cancel"])
def test_original_deadline_proof_keeps_first_control_and_truthful_terminal_cause(jobs, control):
    from open_workflow_agent.sandbox.job_backend import SandboxJobOriginalDeadlineProof

    services, store, _, _, _, state = jobs
    state["deadline"] = time.time() + 0.5
    job = admit(jobs)
    lease = store.claim(job.job_id)
    store.begin_submit(lease)
    peer = binding(job)
    store.record_accepted(lease, peer)
    original_control = None
    if control is not None:
        original_control = store.request_control(lease, reason=control, principal="original-owner")
    time.sleep(max(0, state["deadline"] - time.time()) + 0.02)
    proof = SandboxJobOriginalDeadlineProof(job.admission_digest, state["deadline"], "e" * 64)
    result = outcome(job, exit_code=-15)
    store.record_original_deadline(lease, peer, result, proof=proof)
    terminal = store.record_terminal(lease, peer, result, reason="deadline")
    assert json.loads(terminal.terminal_json)["reason"] == "deadline"
    assert terminal.control_ack_json is None
    assert (
        json.loads(terminal.control_json) if terminal.control_json else None
    ) == original_control
    services.effect_journal.get(
        policy_identity(job.identity, "policy:sandbox-job-original-deadline:v1")
    )
    assert store.get(job.job_id) == terminal
    store.release(lease)


@pytest.mark.parametrize("damage", ["missing", "admission", "deadline", "stale", "early"])
def test_original_deadline_proof_rejects_missing_or_unbound_authority(jobs, damage):
    from open_workflow_agent.sandbox.job_backend import SandboxJobOriginalDeadlineProof

    _, store, _, _, _, state = jobs
    state["deadline"] = time.time() + 0.5
    job = admit(jobs)
    lease = store.claim(job.job_id)
    store.begin_submit(lease)
    peer = binding(job)
    store.record_accepted(lease, peer)
    if damage != "early":
        time.sleep(max(0, state["deadline"] - time.time()) + 0.02)
    proof = (
        None
        if damage == "missing"
        else SandboxJobOriginalDeadlineProof(
            "f" * 64 if damage == "admission" else job.admission_digest,
            state["deadline"] + (1 if damage == "deadline" else 0),
            "d" * 64,
        )
    )
    if damage == "stale":
        store.release(lease)
        replacement = store.claim(job.job_id)
    before = snapshot(jobs)
    with pytest.raises(OwaError):
        store.record_original_deadline(lease, peer, outcome(job, exit_code=-15), proof=proof)
    assert snapshot(jobs) == before
    if damage == "stale":
        store.release(replacement)
    else:
        store.release(lease)


@pytest.mark.parametrize(
    "value",
    [
        {"payload": object()},
        {"payload": float("nan")},
        {"payload": "x" * 1_048_577},
    ],
    ids=["non-json-value", "non-finite-number", "oversized-json"],
)
def test_private_job_encoding_rejects_values_without_a_bounded_canonical_form(jobs, value):
    from open_workflow_agent.sandbox.jobs import _encoded

    with pytest.raises(OwaError):
        _encoded(value)


@pytest.mark.parametrize(
    "encoded",
    [
        '{"state":"ready","state":"terminal"}',
        "x" * 1_048_577,
        "{not-json",
    ],
    ids=["duplicate-field", "oversized-input", "malformed-json"],
)
def test_private_job_decoding_rejects_ambiguous_or_unbounded_evidence(jobs, encoded):
    from open_workflow_agent.sandbox.jobs import _decode

    with pytest.raises(WorkflowExecutionError, match="sandbox job evidence"):
        _decode(encoded)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: SandboxJobProfile("external", "deployment", "1", "a" * 64),
        lambda: SandboxJobProfile("internal", "bad deployment", "1", "a" * 64),
        lambda: SandboxJobProfile("internal", "deployment", "bad revision", "a" * 64),
        lambda: SandboxJobProfile("internal", "deployment", "1", "not-a-digest"),
        lambda: SandboxJobProfile("internal", "deployment", "1", "a" * 64, True),
        lambda: SandboxJobProfile("internal", "deployment", "1", "a" * 64, float("inf")),
        lambda: SandboxJobProfile("internal", "deployment", "1", "a" * 64, 0),
    ],
    ids=[
        "backend",
        "deployment-id",
        "revision",
        "policy-digest",
        "boolean-timeout",
        "infinite-timeout",
        "zero-timeout",
    ],
)
def test_job_profile_rejects_unbounded_or_malformed_deployment_authority(jobs, factory):
    with pytest.raises(WorkflowExecutionError):
        factory()


@pytest.mark.parametrize(
    "factory",
    [
        lambda: SandboxJobLease("foreign", "a" * 64, "b" * 32, 1, 1),
        lambda: SandboxJobLease("job_" + "a" * 64, "bad-digest", "b" * 32, 1, 1),
        lambda: SandboxJobLease("job_" + "a" * 64, "a" * 64, "foreign", 1, 1),
        lambda: SandboxJobLease("job_" + "a" * 64, "a" * 64, "b" * 32, True, 1),
        lambda: SandboxJobLease("job_" + "a" * 64, "a" * 64, "b" * 32, 0, 1),
        lambda: SandboxJobLease("job_" + "a" * 64, "a" * 64, "b" * 32, 1, float("nan")),
        lambda: SandboxJobLease("job_" + "a" * 64, "a" * 64, "b" * 32, 1, 0),
    ],
    ids=[
        "job-id",
        "admission-digest",
        "owner",
        "boolean-generation",
        "zero-generation",
        "non-finite-expiry",
        "zero-expiry",
    ],
)
def test_job_lease_rejects_malformed_or_expired_ownership_evidence(jobs, factory):
    with pytest.raises(WorkflowExecutionError):
        factory()


@pytest.mark.parametrize(
    "factory",
    [
        lambda: SandboxJobBackendBinding("foreign", "a" * 64, "deployment", "instance"),
        lambda: SandboxJobBackendBinding("job_" + "a" * 64, "bad-digest", "deployment", "instance"),
        lambda: SandboxJobBackendBinding("job_" + "a" * 64, "a" * 64, "bad deployment", "instance"),
        lambda: SandboxJobBackendBinding("job_" + "a" * 64, "a" * 64, "deployment", "bad instance"),
    ],
    ids=["job-id", "request-digest", "controller-id", "instance-id"],
)
def test_backend_binding_requires_the_exact_bounded_peer_identity(jobs, factory):
    with pytest.raises(WorkflowExecutionError):
        factory()


@pytest.mark.parametrize(
    "damage",
    ["wrong-id", "wrong-kind", "nul-command", "script-shape", "container-shape", "arguments"],
    ids=[
        "execution-id",
        "operation-kind",
        "nul-byte",
        "script-fields",
        "container-fields",
        "argument-bounds",
    ],
)
def test_executable_request_shape_is_validated_before_it_can_become_durable(jobs, damage):
    from open_workflow_agent.sandbox.jobs import _request, job_id

    _, _, _, identity, execution, _ = jobs
    selected_identity = identity
    candidate = execution
    if damage == "wrong-id":
        candidate = replace(execution, execution_id="job_" + "0" * 64)
    elif damage == "wrong-kind":
        candidate = replace(execution, kind="container")
    elif damage == "nul-command":
        candidate = replace(execution, command="bad\x00command")
    elif damage == "script-shape":
        selected_identity = replace(identity, operation="run:script")
        candidate = replace(
            execution,
            execution_id=job_id(selected_identity),
            kind="script",
            command=None,
            script_language=None,
            script_code="print('ok')",
            image=None,
        )
    elif damage == "container-shape":
        selected_identity = replace(identity, operation="run:container")
        candidate = replace(
            execution,
            execution_id=job_id(selected_identity),
            kind="container",
            command=None,
            image=None,
        )
    else:
        candidate = replace(execution, arguments=("arg",) * 129)
    with pytest.raises(WorkflowExecutionError):
        _request(candidate, selected_identity)


def test_job_event_state_guards_reject_illegal_transitions_without_changing_evidence(jobs):
    store = jobs[1]
    job = admit(jobs)
    lease = store.claim(job.job_id)

    with pytest.raises(WorkflowExecutionError):
        store.mark_uncertain(lease, reason="invented-reason")
    with pytest.raises(InvocationStateConflict):
        store.mark_uncertain(lease, reason="controller_lost")
    with pytest.raises(InvocationStateConflict):
        store.record_accepted(lease, binding(job))
    with pytest.raises(WorkflowExecutionError):
        store.acknowledge_control(lease, binding(job), generation=1)
    before = snapshot(jobs)
    assert store.get(job.job_id).status == "ready"
    assert snapshot(jobs) == before

    store.request_control(lease, reason="cancel", principal="original-owner")
    terminal = store.settle_unstarted_control(lease)
    assert terminal.status == "terminal"
    with pytest.raises(InvocationStateConflict):
        store.mark_uncertain(lease, reason="controller_lost")
    store.release(lease)


def test_control_acknowledgment_and_first_control_reject_conflicting_generations(jobs, monkeypatch):
    store, state = jobs[1], jobs[5]
    job = admit(jobs)
    lease = store.claim(job.job_id)
    store.begin_submit(lease)
    peer = binding(job)
    store.record_accepted(lease, peer)

    with pytest.raises(WorkflowExecutionError):
        store.acknowledge_control(lease, peer, generation=1)
    control = store.request_control(lease, reason="cancel", principal="original-owner")
    with pytest.raises(WorkflowExecutionError):
        store.acknowledge_control(lease, peer, generation=2)
    with pytest.raises(InvocationStateConflict):
        store.settle_unstarted_control(lease)

    monkeypatch.setattr(store, "_now", lambda: state["deadline"] + 1)
    with pytest.raises(InvocationStateConflict):
        store.request_control(lease, reason="deadline")
    assert store.request_control(lease, reason="cancel", principal="original-owner") == control
    store.acknowledge_control(lease, peer, generation=control["generation"])
    store.release(lease)
