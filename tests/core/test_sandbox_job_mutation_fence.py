"""Real SQLite/process mutation gates, separate from IPC transport witnesses."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path

import pytest
from open_workflow_agent.errors import OwaError
from open_workflow_agent.sandbox._job_runner import run_job
from open_workflow_agent.sandbox.backends.internal import InternalSandboxBackend
from open_workflow_agent.sandbox.job_validation import backend_mutation_fence, verify_backend_launch
from open_workflow_agent.storage import StorageConnection
from test_internal_sandbox_jobs import supervisor as supervisor


def database(services):
    return Path(services.effect_journal.connection.execute("PRAGMA database_list").fetchone()[2])


def snapshot(services):
    connection = services.effect_journal.connection
    return tuple(
        connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
        for table in (
            "sandbox_jobs",
            "sandbox_job_ownership",
            "sandbox_job_delivery",
            "workflow_effects",
            "workflow_effect_invocations",
            "recovery_invocation_definitions",
            "recovery_definitions",
            "invocation_inputs",
        )
    )


async def wait_file(path):
    async with asyncio.timeout(5):
        while not path.exists():
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("control", ["absent", "authorized", "malformed", "oversize"])
def test_readonly_submit_preserves_null_and_rejects_every_retained_control(
    supervisor, monkeypatch, control
):
    services, store, lease, _, _, launch = supervisor("print('finite')")
    connection = services.effect_journal.connection
    if control == "authorized":
        store.request_control(lease, reason="cancel", principal="original-owner")
    elif control != "absent":
        connection.execute(
            "UPDATE sandbox_job_delivery SET control_json=? WHERE job_id=?",
            ("not-json" if control == "malformed" else "x" * 1_048_577, lease.job_id),
        )
        connection.commit()
    else:
        assert (
            connection.execute(
                "SELECT control_json FROM sandbox_job_delivery WHERE job_id=?", (lease.job_id,)
            ).fetchone()[0]
            is None
        )
    before = snapshot(services)
    statements = []
    original = StorageConnection.execute

    def select_only(selected, statement, parameters=()):
        if selected is connection:
            statements.append(statement)
            assert statement.lstrip().startswith("SELECT ")
        return original(selected, statement, parameters)

    with monkeypatch.context() as guard:
        guard.setattr(StorageConnection, "execute", select_only)
        if control == "absent":
            verify_backend_launch(connection, launch, expected_profile=launch.profile)
        else:
            with pytest.raises(OwaError):
                verify_backend_launch(connection, launch, expected_profile=launch.profile)
    assert statements and snapshot(services) == before


def test_mutation_fence_blocks_takeover_without_writing_proofs(supervisor):
    services, _, lease, _, _, launch = supervisor("print('finite')")
    original = snapshot(services)
    other = sqlite3.connect(database(services), timeout=0)
    try:
        with backend_mutation_fence(
            database(services), launch, expected_profile=launch.profile, purpose="submit"
        ) as verify:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                other.execute(
                    "UPDATE sandbox_job_ownership SET generation=generation+1 WHERE job_id=?",
                    (lease.job_id,),
                )
            other.rollback()
            verify()
        assert snapshot(services) == original
        other.execute(
            "UPDATE sandbox_job_ownership SET generation=generation+1 WHERE job_id=?",
            (lease.job_id,),
        )
        other.commit()
        changed = snapshot(services)
        with pytest.raises(OwaError):
            with backend_mutation_fence(
                database(services), launch, expected_profile=launch.profile, purpose="submit"
            ):
                pytest.fail("stale generation reached mutation")
        assert snapshot(services) == changed
    finally:
        other.close()


@pytest.mark.parametrize("boundary", ["lease", "original-deadline"])
def test_held_fence_rechecks_live_clock_without_extending_budget(supervisor, boundary):
    services, _, lease, _, _, launch = supervisor("print('finite')", seconds=2)
    if boundary == "lease":
        services.effect_journal.connection.execute(
            "UPDATE sandbox_job_ownership SET expires_at="
            "((julianday('now')-2440587.5)*86400.0)+1 WHERE job_id=?",
            (lease.job_id,),
        )
        services.effect_journal.connection.commit()
    before = snapshot(services)
    with backend_mutation_fence(
        database(services), launch, expected_profile=launch.profile, purpose="submit"
    ) as verify:
        # A held write reservation excludes takeover, not the passage of time.
        delay = 1.05 if boundary == "lease" else max(0, launch.deadline_at - time.time()) + 0.05
        time.sleep(delay)
        with pytest.raises(OwaError):
            verify()
    assert snapshot(services) == before


@pytest.mark.parametrize("damage", ["takeover", "expiry", "cancel", "admission"])
async def test_launch_gate_rejects_changed_evidence_before_process_creation(
    supervisor, monkeypatch, damage
):
    services, store, lease, _, _, launch = supervisor("print('must-not-run')")
    connection = services.effect_journal.connection
    if damage == "cancel":
        store.request_control(lease, reason="cancel", principal="original-owner")
    elif damage == "admission":
        connection.execute("DELETE FROM sandbox_jobs WHERE job_id=?", (lease.job_id,))
        connection.commit()
    else:
        assignment = "generation=generation+1" if damage == "takeover" else "expires_at=0"
        connection.execute(
            f"UPDATE sandbox_job_ownership SET {assignment} WHERE job_id=?", (lease.job_id,)
        )
        connection.commit()
    before = snapshot(services)
    attempted = []

    async def forbidden(*args, **kwargs):
        attempted.append(True)
        pytest.fail("unverified launch reached process creation")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    with pytest.raises(OwaError):
        await run_job(
            InternalSandboxBackend(services.config.sandbox),
            launch.request,
            remaining=5,
            started=lambda pid: None,
            stop=asyncio.Event(),
            deadline_reached=lambda: None,
            deadline_due=lambda: False,
            launch_fence=lambda: backend_mutation_fence(
                database(services), launch, expected_profile=launch.profile, purpose="submit"
            ),
            control_mutation=lambda: None,
        )
    assert attempted == [] and snapshot(services) == before


@pytest.mark.parametrize("stale", [False, True])
async def test_control_fence_stops_only_current_generation_and_preserves_live_job(
    supervisor, tmp_path, stale
):
    ready, proceed = tmp_path / "ready", tmp_path / "proceed"
    code = (
        "import time\nfrom pathlib import Path\n"
        f"Path({str(ready)!r}).write_text('started')\n"
        "due=time.monotonic()+6\n"
        f"while not Path({str(proceed)!r}).exists() and time.monotonic()<due: time.sleep(.01)\n"
        "print('continued',flush=True)\n"
    )
    services, store, lease, _, _, launch = supervisor(code)
    stop = asyncio.Event()
    acknowledged = []
    control = []

    def mutation():
        if not control:
            return None
        generation = control.pop()["generation"]
        return (
            backend_mutation_fence(
                database(services),
                launch,
                expected_profile=launch.profile,
                purpose="control",
                control_generation=generation,
                reason="cancel",
            ),
            lambda: acknowledged.append(generation),
        )

    task = asyncio.create_task(
        run_job(
            InternalSandboxBackend(services.config.sandbox),
            launch.request,
            remaining=8,
            started=lambda pid: None,
            stop=stop,
            deadline_reached=lambda: pytest.fail("original budget was reset or expired early"),
            deadline_due=lambda: False,
            launch_fence=lambda: backend_mutation_fence(
                database(services), launch, expected_profile=launch.profile, purpose="submit"
            ),
            control_mutation=mutation,
        )
    )
    try:
        await wait_file(ready)
        control.append(store.request_control(lease, reason="cancel", principal="original-owner"))
        if stale:
            services.effect_journal.connection.execute(
                "UPDATE sandbox_job_ownership SET generation=generation+1 WHERE job_id=?",
                (lease.job_id,),
            )
            services.effect_journal.connection.commit()
        before = snapshot(services)
        stop.set()
        if stale:
            async with asyncio.timeout(3):
                while stop.is_set():
                    await asyncio.sleep(0.01)
            assert not task.done() and acknowledged == []
            proceed.write_text("continue")
        async with asyncio.timeout(5):
            result, terminated = await task
        assert snapshot(services) == before
        if stale:
            assert not terminated and result.exit_code == 0 and result.stdout == "continued\n"
        else:
            assert terminated and acknowledged == [1]
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_original_deadline_terminates_without_current_observer_lease(supervisor, tmp_path):
    ready = tmp_path / "ready"
    services, _, lease, _, _, launch = supervisor(
        "from pathlib import Path\nimport time\n"
        f"Path({str(ready)!r}).write_text('ready')\ntime.sleep(30)",
        seconds=3,
    )
    expired = []
    task = asyncio.create_task(
        run_job(
            InternalSandboxBackend(services.config.sandbox),
            launch.request,
            remaining=launch.deadline_at - time.time(),
            started=lambda pid: None,
            stop=asyncio.Event(),
            deadline_reached=lambda: expired.append(True),
            deadline_due=lambda: time.time() >= launch.deadline_at,
            launch_fence=lambda: backend_mutation_fence(
                database(services), launch, expected_profile=launch.profile, purpose="submit"
            ),
            control_mutation=lambda: pytest.fail("autonomous deadline adopted a control"),
        )
    )
    try:
        await wait_file(ready)
        services.effect_journal.connection.execute(
            "UPDATE sandbox_job_ownership SET expires_at=0 WHERE job_id=?",
            (lease.job_id,),
        )
        services.effect_journal.connection.commit()
        before = snapshot(services)
        async with asyncio.timeout(6):
            result, terminated = await task
        assert terminated and expired == [True] and result.exit_code != 0
        assert snapshot(services) == before
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
