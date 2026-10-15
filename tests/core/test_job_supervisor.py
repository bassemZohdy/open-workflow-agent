"""In-process supervisor orchestration over real private IPC and admission proofs.

Only the OS runner is intercepted; these tests do not qualify process isolation.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import time
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.effects import EffectIdentity
from open_workflow_agent.errors import OwaError, SandboxPolicyError
from open_workflow_agent.sandbox import compile_sandbox_workflow, job_supervisor
from open_workflow_agent.sandbox._job_ipc import (
    check_path,
    create_key,
    private_directory,
    read_frame,
    write_frame,
)
from open_workflow_agent.sandbox.contract import SandboxExecutionRequest, SandboxExecutionResult
from open_workflow_agent.sandbox.internal_jobs import (
    InternalSandboxJobBackend,
    immutable,
    internal_job_profile,
    launch_value,
)
from open_workflow_agent.sandbox.job_backend import SandboxJobLaunch
from open_workflow_agent.sandbox.jobs import SandboxJobStore, job_id
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.storage import StorageConnection


@pytest.fixture
def supervisor_record(tmp_path, monkeypatch):
    config = RuntimeConfig.model_validate(
        {
            "sandbox": {
                "enabled": True,
                "workspace_root": str(tmp_path / "workspace"),
                "executable_search_path": str(Path(sys.executable).parent),
                "file_size_bytes": None,
                "process_count": None,
                "timeout_seconds": 30,
                "max_output_bytes": 4096,
                "max_workspace_bytes": 65536,
            }
        }
    )
    services = RuntimeServices(config, model=FakeModel(), database_root=tmp_path)
    # This fixture qualifies supervisor/common proofs, not physical SDK packages.
    services.recovery_definitions.profile_provider = lambda engine: {
        "profile": "supervisor-orchestration-test",
        "engine": engine,
        "continuation_supported": True,
    }
    plan = compile_sandbox_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "supervisor-orchestration",
                "name": uuid4().hex,
                "version": "1.0.0",
            },
            "do": [
                {
                    "work": {
                        "run": {
                            "await": True,
                            "script": {"language": "python", "code": "print('bounded')"},
                        }
                    }
                }
            ],
        },
        sandbox=config.sandbox,
    )
    handle = services.invocations.create(
        engine="portable",
        session_id="original-session",
        user_id="original-user",
        owner_principal="original-owner",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    owner = services.execution_ownership.acquire(handle, seconds=120)
    handle._execution_lease = owner
    services.recovery_definitions.admit(plan, handle, {"original": None}, execution_lease=owner)
    identity = EffectIdentity(
        handle.invocation_id,
        plan.fingerprint,
        plan.tasks[0].reference,
        ((plan.tasks[0].reference, 1),),
        "run:script",
    )
    request = SandboxExecutionRequest(
        job_id(identity),
        "script",
        script_language="python",
        script_code="print('bounded')",
        stdin="original input",
        invocation_id=handle.invocation_id,
        task_reference=identity.task_reference,
    )
    store = SandboxJobStore(services, profile_provider=lambda: internal_job_profile(config.sandbox))
    job = store.admit(
        identity,
        handle,
        request,
        {"original": None},
        deadline_at=time.time() + 5,
        execution_lease=owner,
    )
    lease = store.claim(job.job_id, seconds=90)
    assert lease is not None
    store.begin_submit(lease)
    launch = SandboxJobLaunch.from_verified_job(store.get(job.job_id), lease)
    backend = InternalSandboxJobBackend(config.sandbox, store)
    Path(config.sandbox.workspace_root).mkdir()
    private_directory(backend.root)
    directory = backend.root / job.job_id
    private_directory(directory)
    key = create_key(directory / "auth.key")
    bootstrap = {
        "launch": launch_value(launch),
        "profile": asdict(backend.profile()),
        "config": config.sandbox.model_dump(mode="json", exclude_unset=True),
        "database": str(backend.database),
        "instance_id": "supervisor_" + uuid4().hex,
    }
    write_frame(directory / "bootstrap.json", bootstrap, key, exclusive=True)
    connections = []

    class ObservedConnection(StorageConnection):
        def __init__(self, raw, kind):
            super().__init__(raw, kind)
            connections.append(self)

        def close(self):
            assert self.raw.total_changes == 0
            assert self.execute("PRAGMA query_only").fetchone()[0] == 1
            super().close()

    monkeypatch.setattr(job_supervisor, "StorageConnection", ObservedConnection)
    record = SimpleNamespace(
        services=services,
        store=store,
        lease=lease,
        owner=owner,
        handle=handle,
        launch=launch,
        backend=backend,
        directory=directory,
        key=key,
        bootstrap=bootstrap,
        connections=connections,
    )
    try:
        yield record
    finally:
        try:
            retained = store.connection.execute(
                "SELECT owner,generation,expires_at FROM sandbox_job_ownership WHERE job_id=?",
                (lease.job_id,),
            ).fetchone()
            if tuple(retained) != (lease.owner, lease.generation, 0):
                store.release(lease)
        finally:
            services.execution_ownership.release(owner)
            services.close()


def assert_cleanup(record, original_tasks):
    assert len(record.connections) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        record.connections[0].execute("SELECT 1")
    assert asyncio.all_tasks() == original_tasks
    assert record.services.model.calls == []


def running_observation(record):
    check_path(record.directory, directory=True)
    value = read_frame(record.directory / "observation.json", record.key)
    heartbeat = read_frame(record.directory / "heartbeat.json", record.key)
    assert value["state"] == "running" and value["outcome"] is None
    assert value["immutable"] == immutable(record.launch)
    assert value["instance_id"] == heartbeat["instance_id"] == record.bootstrap["instance_id"]
    assert value["pid"] == os.getpid() and value["control_ack_generation"] is None
    assert 0 <= time.time() - heartbeat["time"] < 2
    return value


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["exited", "cancel", "deadline"])
async def test_supervisor_verifies_submit_and_publishes_authenticated_running_then_terminal(
    supervisor_record, monkeypatch, mode
):
    record = supervisor_record
    calls = []
    outcome = SandboxExecutionResult(record.launch.request.execution_id, 7, "bounded\n", "", 0.1)

    async def intercepted_runner(
        backend,
        request,
        *,
        remaining,
        started,
        stop,
        deadline_reached,
        deadline_due,
        launch_fence,
        control_mutation,
    ):
        calls.append(request)
        assert backend.config == record.backend.config
        assert request == record.launch.request
        assert request.invocation_id == record.handle.invocation_id
        assert request.task_reference == record.launch.request.task_reference
        assert 0 < remaining <= record.launch.profile.timeout_seconds
        assert not stop.is_set() and not deadline_due()
        with launch_fence() as verify:
            verify()
            started(8817)
        running_observation(record)
        if mode == "cancel":
            control = record.store.request_control(
                record.lease, reason="cancel", principal=record.handle.owner_principal
            )
            write_frame(
                record.directory / "control.json",
                {
                    "launch": launch_value(record.launch),
                    "instance_id": record.bootstrap["instance_id"],
                    "generation": control["generation"],
                    "reason": "cancel",
                },
                record.key,
            )
            await asyncio.wait_for(stop.wait(), 2)
            mutation = control_mutation()
            assert mutation is not None and control_mutation() is None
            fence, applied = mutation
            assert (
                read_frame(record.directory / "observation.json", record.key)[
                    "control_ack_generation"
                ]
                is None
            )
            with fence as verify:
                verify()
                applied()
        elif mode == "deadline":
            await asyncio.wait_for(stop.wait(), 6)
            assert time.time() >= record.launch.deadline_at and deadline_due()
            assert control_mutation() is None
            deadline_reached()
        else:
            assert control_mutation() is None
            await asyncio.sleep(0)
        return outcome, mode != "exited"

    monkeypatch.setattr(job_supervisor, "run_job", intercepted_runner)
    tasks = asyncio.all_tasks()
    await job_supervisor.supervise(record.directory)
    terminal = read_frame(record.directory / "observation.json", record.key)
    assert calls == [record.launch.request]
    assert terminal["state"] == "terminal" and terminal["outcome"] == asdict(outcome)
    assert terminal["reason"] == mode
    assert terminal["control_ack_generation"] == (1 if mode == "cancel" else None)
    assert terminal["immutable"] == immutable(record.launch)
    assert record.store.get(record.lease.job_id).status == "submitting"
    assert record.services.invocations.read_input(record.handle) == {"original": None}
    assert_cleanup(record, tasks)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["before_started", "after_started", "cancelled"])
async def test_runner_failure_publishes_unknown_without_serializing_exception_or_known_outcome(
    supervisor_record, monkeypatch, failure
):
    record = supervisor_record
    calls = []

    async def intercepted_runner(_backend, _request, *, started, launch_fence, **_kwargs):
        calls.append(_request)
        with launch_fence() as verify:
            verify()
            if failure != "before_started":
                started(8817)
                running_observation(record)
        await asyncio.sleep(0)
        if failure == "cancelled":
            raise asyncio.CancelledError
        raise RuntimeError("private-provider-secret-must-not-be-published")

    monkeypatch.setattr(job_supervisor, "run_job", intercepted_runner)
    tasks = asyncio.all_tasks()
    expected = asyncio.CancelledError if failure == "cancelled" else RuntimeError
    with pytest.raises(expected):
        await job_supervisor.supervise(record.directory)
    unknown = read_frame(record.directory / "observation.json", record.key)
    assert unknown["state"] == "unknown" and unknown["outcome"] is None
    assert unknown["control_ack_generation"] is None
    assert "private-provider-secret" not in json.dumps(unknown)
    assert unknown["immutable"] == immutable(record.launch)
    assert calls == [record.launch.request]
    assert record.store.get(record.lease.job_id).status == "submitting"
    assert_cleanup(record, tasks)


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["envelope", "profile", "database", "mac", "submit_owner"])
async def test_supervisor_refuses_invalid_original_bootstrap_or_submit_before_runner(
    supervisor_record, monkeypatch, damage
):
    record = supervisor_record
    bootstrap = dict(record.bootstrap)
    if damage == "envelope":
        bootstrap["undeclared"] = True
    elif damage == "profile":
        bootstrap["profile"] = {**bootstrap["profile"], "version": "different"}
    elif damage == "database":
        bootstrap["database"] = "relative-runtime.sqlite3"
    elif damage == "submit_owner":
        record.store.release(record.lease)
    if damage != "submit_owner":
        write_frame(record.directory / "bootstrap.json", bootstrap, record.key)
    if damage == "mac":
        frame = json.loads((record.directory / "bootstrap.json").read_text(encoding="utf-8"))
        frame["mac"] = "0" * 64
        (record.directory / "bootstrap.json").write_text(json.dumps(frame), encoding="utf-8")
    calls = []

    async def forbidden_runner(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("invalid bootstrap reached OS execution")

    monkeypatch.setattr(job_supervisor, "run_job", forbidden_runner)
    tasks = asyncio.all_tasks()
    with pytest.raises(OwaError):
        await job_supervisor.supervise(record.directory)
    assert not calls and not (record.directory / "observation.json").exists()
    if damage != "mac":
        assert read_frame(record.directory / "bootstrap.json", record.key) == bootstrap
    if damage == "submit_owner":
        assert_cleanup(record, tasks)
    else:
        assert record.connections == [] and asyncio.all_tasks() == tasks


@pytest.mark.asyncio
async def test_supervisor_refuses_relative_directory_before_reading_bootstrap(monkeypatch):
    calls = []

    async def forbidden_runner(*_args, **_kwargs):
        calls.append(True)

    monkeypatch.setattr(job_supervisor, "run_job", forbidden_runner)
    with pytest.raises(SandboxPolicyError, match="invalid internal supervisor directory"):
        await job_supervisor.supervise(Path("job_relative"))
    assert calls == []


@pytest.mark.parametrize("mode,exit_code", [("arguments", 2), ("failure", 1), ("success", None)])
def test_supervisor_entrypoint_preserves_exit_category_without_logging_failure(
    monkeypatch, capsys, tmp_path, mode, exit_code
):
    calls = []

    async def intercepted_supervise(directory):
        calls.append(directory)
        if mode == "failure":
            raise RuntimeError("private-entrypoint-secret")

    monkeypatch.setattr(job_supervisor, "supervise", intercepted_supervise)
    monkeypatch.setattr(
        sys, "argv", ["supervisor"] if mode == "arguments" else ["supervisor", str(tmp_path)]
    )
    if exit_code is None:
        job_supervisor.main()
    else:
        with pytest.raises(SystemExit) as failure:
            job_supervisor.main()
        assert failure.value.code == exit_code
    assert calls == ([] if mode == "arguments" else [tmp_path])
    output = capsys.readouterr()
    assert output.out == output.err == ""
