"""Explicit background deployment and atomic acceptance boundaries."""

from __future__ import annotations

import os
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig, SandboxBackgroundConfig
from open_workflow_agent.effects import EffectIdentity
from open_workflow_agent.errors import ConfigurationError, OwaError
from open_workflow_agent.sandbox import background_qualification, compile_sandbox_workflow
from open_workflow_agent.sandbox.contract import SandboxExecutionRequest
from open_workflow_agent.sandbox.jobs import (
    SandboxJobProfile,
    SandboxJobStore,
    job_id,
    policy_identity,
)
from open_workflow_agent.services import RuntimeServices
from pydantic import ValidationError


@pytest.mark.parametrize(
    "policy",
    [
        {"lease_seconds": float("nan")},
        {"io_timeout": float("inf")},
        {"poll_seconds": 0},
        {"scan_limit": 1001},
        {"max_pending": 0},
        {"lease_seconds": 10, "io_timeout": 6},
    ],
)
def test_background_delivery_limits_reject_unbounded_policies(policy):
    with pytest.raises(ValidationError):
        SandboxBackgroundConfig.model_validate(policy)


@pytest.mark.parametrize("backend", ["docker", "kubernetes", "disabled", "memory", "postgresql"])
def test_background_runtime_rejects_unqualified_profile_before_store_or_native_setup(
    tmp_path, backend
):
    value = {"sandbox": {"enabled": True, "allow_shell": True, "background": {"enabled": True}}}
    if backend in {"docker", "kubernetes"}:
        value["sandbox"].update(backend=backend, enabled=False)
    if backend == "disabled":
        value["sandbox"]["enabled"] = False
    if backend == "memory":
        value["persistence"] = {"database": ":memory:"}
    if backend == "postgresql":
        value["persistence"] = {"datasource": "postgresql://unreachable.invalid/never"}
    with pytest.raises(ConfigurationError):
        RuntimeServices(RuntimeConfig.model_validate(value), model=FakeModel())
    assert not (tmp_path / "runtime.sqlite3").exists()


@pytest.fixture(params=["sqlite", "postgresql"])
def admission(tmp_path, request, monkeypatch):
    # Isolate the common-ledger seam from future release-gate changes. Physical
    # native recovery workers exercise the actual published flag without override.
    monkeypatch.setattr(background_qualification, "BACKGROUND_EXECUTION_PROFILE_QUALIFIED", True)
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if request.param == "postgresql" else None
    if request.param == "postgresql" and datasource is None:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    config = RuntimeConfig.model_validate(
        {
            "persistence": {"datasource": datasource},
            "sandbox": {
                "enabled": True,
                "allow_shell": True,
                "workspace_root": str(tmp_path / "workspace"),
            },
        }
    )
    services = RuntimeServices(
        config, model=FakeModel(), database_root=None if datasource else tmp_path
    )
    assert services.effect_journal.connection.kind == request.param
    # Pure common-ledger seam: no supervisor constructed or runtime activated.
    config.sandbox.background.enabled = True
    services.recovery_definitions.profile_provider = lambda engine: {
        "profile": "background-atomic-store-only",
        "engine": engine,
        "continuation_supported": True,
    }
    plan = compile_sandbox_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "test",
                "name": "background",
                "version": "1.0.0",
            },
            "do": [
                {"job": {"run": {"await": False, "shell": {"command": Path(sys.executable).name}}}}
            ],
        },
        sandbox=config.sandbox,
    )
    handle = services.invocations.create(
        engine="portable",
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    lease = services.execution_ownership.acquire(handle)
    handle._execution_lease = lease
    services.recovery_definitions.admit(plan, handle, {"raw": True}, execution_lease=lease)
    identity = EffectIdentity(
        handle.invocation_id,
        plan.fingerprint,
        plan.tasks[0].reference,
        ((plan.tasks[0].reference, 1),),
        "run:shell",
    )
    store = SandboxJobStore(
        services,
        profile_provider=lambda: SandboxJobProfile("internal", "unit-ledger-seam", "1", "b" * 64),
    )
    executable = SandboxExecutionRequest(
        job_id(identity),
        "shell",
        command=Path(sys.executable).name,
        invocation_id=handle.invocation_id,
        task_reference=identity.task_reference,
    )
    try:
        yield services, store, handle, identity, executable, time.time() + 20
    finally:
        if datasource:
            identifier = job_id(identity)
            for table in ("sandbox_job_delivery", "sandbox_job_ownership", "sandbox_jobs"):
                services.effect_journal.connection.execute(
                    f"DELETE FROM {table} WHERE job_id=?", (identifier,)
                )
            services.effect_journal.connection.commit()
        services.execution_ownership.release(lease)
        services.close()


def test_configured_background_capability_stays_hidden_before_qualification(tmp_path, monkeypatch):
    monkeypatch.setattr(background_qualification, "BACKGROUND_EXECUTION_PROFILE_QUALIFIED", False)
    config = RuntimeConfig.model_validate(
        {
            "sandbox": {
                "enabled": True,
                "allow_shell": True,
                "background": {"enabled": True},
                "file_size_bytes": None,
                "process_count": None,
                "workspace_root": str(tmp_path / "workspace"),
            }
        }
    )
    services = RuntimeServices(config, model=FakeModel(), database_root=tmp_path)
    try:
        capabilities = services.sandbox.capabilities()
        assert capabilities["backgroundExecution"] is False
        assert "backgroundProfile" not in capabilities
        with pytest.raises(OwaError, match="profile has not passed qualification"):
            services.sandbox.validate_job_profile(services.sandbox._job_backend.profile())
        assert not (tmp_path / "workspace" / ".owa-job-supervisors").exists()
    finally:
        services.close()


def test_qualified_background_profile_still_requires_explicit_deployment_opt_in(tmp_path):
    assert background_qualification.BACKGROUND_EXECUTION_PROFILE_QUALIFIED is True
    for enabled in (False, True):
        config = RuntimeConfig.model_validate(
            {
                "sandbox": {
                    "enabled": True,
                    "allow_shell": True,
                    "background": {"enabled": enabled},
                    "file_size_bytes": None,
                    "process_count": None,
                    "workspace_root": str(tmp_path / "workspace"),
                }
            }
        )
        services = RuntimeServices(config, model=FakeModel(), database_root=tmp_path / str(enabled))
        try:
            value = services.sandbox.capabilities()
            assert value["backgroundExecution"] is enabled
            if enabled:
                assert value["backgroundProfile"]["scriptRuntimes"] == ["python"]
                assert value["backgroundProfile"]["storage"] == "embedded-sqlite"
                assert value["backgroundProfile"]["automaticAmbiguousRelaunch"] is False
                assert value["backgroundProfile"]["hardIsolation"] is False
            else:
                assert "backgroundProfile" not in value
            assert not (tmp_path / "workspace" / ".owa-job-supervisors").exists()
        finally:
            services.close()


def accept(tree, **changes):
    services, store, handle, identity, executable, deadline = tree
    return store.admit_action(
        identity,
        handle,
        changes.get("request", executable),
        changes.get("input", {"transformed": None}),
        deadline_at=deadline,
        execution_lease=handle._execution_lease,
    )


def test_atomic_acceptance_preserves_transformed_input_and_two_independent_proofs(admission):
    services, store, _, identity, _, _ = admission
    job, receipt = accept(admission)
    assert receipt.output == {"transformed": None} and receipt.status == "completed"
    assert receipt.owner == "sandbox-job:" + job.job_id
    assert services.effect_journal.get(identity) == receipt
    assert services.effect_journal.get(policy_identity(identity)) == job.receipt
    assert accept(admission) == (job, receipt)
    assert store.pending() == (job.job_id,)


@pytest.mark.parametrize("damage", ["run", "policy", "run_index", "policy_index", "request"])
def test_accepted_job_cannot_adopt_missing_or_changed_proof(admission, damage):
    services, store, _, identity, executable, _ = admission
    job, _ = accept(admission)
    connection = services.effect_journal.connection
    if damage != "request":
        selected = policy_identity(identity) if damage.startswith("policy") else identity
        table = "workflow_effect_invocations" if damage.endswith("index") else "workflow_effects"
        connection.execute(f"DELETE FROM {table} WHERE identity=?", (selected.key,))
        connection.commit()
    with pytest.raises(OwaError):
        accept(
            admission,
            request=replace(executable, command="changed") if damage == "request" else executable,
        )
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM sandbox_jobs WHERE job_id=?", (job.job_id,)
        ).fetchone()[0]
        == 1
    )


def test_action_insert_failure_rolls_back_job_policy_and_ready_delivery(admission, monkeypatch):
    import open_workflow_agent.sandbox.jobs as module

    services, store, _, identity, _, _ = admission
    original = module.insert_completed_receipt

    def fail(connection, selected, receipt):
        original(connection, selected, receipt)
        if selected == identity:
            raise RuntimeError("atomic action fixture failure")

    monkeypatch.setattr(module, "insert_completed_receipt", fail)
    with pytest.raises(RuntimeError):
        accept(admission)
    assert store.get(job_id(identity)) is None
    assert services.effect_journal.get(identity) is None
    assert services.effect_journal.get(policy_identity(identity)) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("transformed", [None, False, {"nested": [1, None]}])
async def test_background_executor_returns_exact_input_after_both_native_dependency_notifications(
    admission, transformed
):
    from open_workflow_agent.sandbox.executor import SandboxWorkflowExecutor
    from open_workflow_agent.workflow import ExecutionState

    services, store, handle, identity, request, _ = admission
    services.sandbox_jobs = store
    observed = []

    async def observer(selected, receipt):
        # Both outcomes already exist even at the first observer boundary.
        assert services.effect_journal.get(identity).status == "completed"
        assert services.effect_journal.get(policy_identity(identity)).status == "completed"
        observed.append((selected, receipt))

    state = ExecutionState(
        data=transformed,
        variables={"_task_reference": identity.task_reference},
        metadata={
            "_workflow_fingerprint": identity.workflow_fingerprint,
            "invocation_id": identity.invocation_id,
            "_invocation_handle": handle,
            "_effect_receipt_observer": observer,
        },
        execution_path=identity.occurrence,
    )
    executor = SandboxWorkflowExecutor(services.catalog, services=services)
    result = await executor._execute_background(request, state)
    assert result == transformed and type(result) is type(transformed)
    assert [selected for selected, _ in observed] == [policy_identity(identity), identity]
    assert services.model.calls == []
    assert store.get(job_id(identity)).status == "ready"


@pytest.mark.asyncio
@pytest.mark.parametrize("prior", [False, True])
async def test_expired_retry_can_only_reconcile_existing_background_acceptance(admission, prior):
    from open_workflow_agent.sandbox.executor import SandboxWorkflowExecutor
    from open_workflow_agent.workflow import _RECONCILE_EXPIRED_RETRY, ExecutionState

    services, store, handle, identity, request, _ = admission
    services.sandbox_jobs = store
    if prior:
        accept(admission)
    executor = SandboxWorkflowExecutor(services.catalog, services=services)
    state = ExecutionState(
        data={"transformed": None},
        variables={"_task_reference": identity.task_reference},
        metadata={
            "_workflow_fingerprint": identity.workflow_fingerprint,
            "invocation_id": identity.invocation_id,
            "_invocation_handle": handle,
        },
        execution_path=identity.occurrence,
    )
    token = _RECONCILE_EXPIRED_RETRY.set(True)
    try:
        if prior:
            assert await executor._execute_background(request, state) == {"transformed": None}
        else:
            with pytest.raises(OwaError, match="retry budget expired"):
                await executor._execute_background(request, state)
            assert store.get(job_id(identity)) is None
            assert services.effect_journal.get(identity) is None
    finally:
        _RECONCILE_EXPIRED_RETRY.reset(token)
