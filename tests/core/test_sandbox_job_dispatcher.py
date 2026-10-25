"""Actual common-store delivery invariants with a fake, nonlaunching backend."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest
from open_workflow_agent.errors import SandboxPolicyError
from open_workflow_agent.sandbox import background_qualification
from open_workflow_agent.sandbox.job_backend import SandboxJobObservation
from open_workflow_agent.sandbox.job_dispatcher import SandboxJobDispatcher
from open_workflow_agent.sandbox.manager import SandboxManager
from test_sandbox_jobs import admit, binding, outcome
from test_sandbox_jobs import jobs as jobs


class _Foreground:
    def capabilities(self):
        return {"enabled": True, "backend": "internal"}


class _Manager(SandboxManager):
    # Dispatcher protocol fixtures have no real process backend. Actual manager
    # request admission is covered separately against InternalSandboxBackend.
    def validate_job_request(self, request):
        return None


class _Peer:
    """Protocol fake: never creates an OS process or proves hard isolation."""

    def __init__(self, tree, *, behavior="accepted"):
        self.store = tree[1]
        self.selected = tree[5]["profile"]
        self.behavior = behavior
        self.calls = []
        self.current = SandboxJobObservation("unknown")
        self.entered = asyncio.Event()

    def profile(self):
        return self.selected

    async def submit(self, launch):
        self.store.verify(launch.lease)
        job = self.store.get(launch.lease.job_id)
        assert job.status == "submitting"
        self.calls.append(("submit", launch.submit_id, launch.lease.generation))
        self.current = SandboxJobObservation("accepted", binding(job))
        self.entered.set()
        if self.behavior == "lost":
            raise OSError("synthetic lost acceptance reply")
        if self.behavior == "unknown":
            self.current = SandboxJobObservation("unknown")
        if self.behavior == "held":
            await asyncio.Event().wait()
        return self.current

    async def inspect(self, launch, known):
        self.store.verify(launch.lease)
        self.calls.append(("inspect", launch.submit_id, launch.lease.generation))
        if known is not None:
            assert known == self.current.binding
        return self.current

    async def cancel(self, launch, known, *, generation, reason):
        job = self.store.verify(launch.lease)
        control = json.loads(job.control_json)
        assert control["generation"] == generation and control["reason"] == reason
        self.calls.append(("cancel", launch.submit_id, launch.lease.generation))
        self.current = SandboxJobObservation(
            "terminal", known, outcome(job, exit_code=-15), reason, generation
        )
        return self.current


def dispatcher(tree, *, behavior="accepted", **options):
    peer = _Peer(tree, behavior=behavior)
    manager = _Manager(_Foreground(), job_backend=peer)
    return SandboxJobDispatcher(tree[1], manager, **options), peer


@pytest.fixture(autouse=True)
def qualify_fake_delivery_profile(monkeypatch):
    # These protocol-fake tests exercise post-admission delivery only. The
    # production capability gate is covered independently by both the published
    # profile and forced-unqualified manager tests in test_background_job_runtime.py.
    monkeypatch.setattr(background_qualification, "BACKGROUND_EXECUTION_PROFILE_QUALIFIED", True)


@pytest.mark.asyncio
@pytest.mark.parametrize("behavior", ["accepted", "lost", "unknown"])
async def test_ready_submission_is_durable_and_never_relaunched_on_observation(jobs, behavior):
    job = admit(jobs)
    worker, peer = dispatcher(jobs, behavior=behavior)
    await worker.deliver(job.job_id)
    after = jobs[1].get(job.job_id)
    assert after.status == ("accepted" if behavior == "accepted" else "uncertain")
    first_submit = after.submit_json
    await worker.deliver(job.job_id)
    current = jobs[1].get(job.job_id)
    assert current.submit_json == first_submit
    assert [call[0] for call in peer.calls] == ["submit", "inspect"]
    assert peer.calls[0][1] == peer.calls[1][1]
    assert peer.calls[0][2] < peer.calls[1][2]
    assert current.status == ("uncertain" if behavior == "unknown" else "accepted")
    assert current.terminal_json is None


@pytest.mark.asyncio
async def test_host_drain_releases_observer_without_cancelling_independent_job(jobs):
    job = admit(jobs)
    worker, peer = dispatcher(jobs, behavior="held")
    running = asyncio.create_task(worker.deliver(job.job_id))
    await asyncio.wait_for(peer.entered.wait(), 2)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    pending = jobs[1].get(job.job_id)
    assert pending.status == "submitting" and pending.control_json is None
    peer.behavior = "accepted"
    await worker.deliver(job.job_id)
    assert [call[0] for call in peer.calls] == ["submit", "inspect"]


@pytest.mark.asyncio
async def test_control_intent_precedes_backend_cancel_and_known_exit_is_retained(jobs):
    job = admit(jobs)
    worker, peer = dispatcher(jobs)
    await worker.deliver(job.job_id)
    lease = jobs[1].claim(job.job_id)
    jobs[1].request_control(lease, reason="cancel", principal="original-owner")
    jobs[1].release(lease)
    await worker.deliver(job.job_id)
    terminal = jobs[1].get(job.job_id)
    assert terminal.status == "terminal" and terminal.control_ack_json is not None
    assert json.loads(terminal.terminal_json)["outcome"]["exit_code"] == -15
    assert [call[0] for call in peer.calls] == ["submit", "inspect", "cancel"]


@pytest.mark.asyncio
async def test_unstarted_explicit_control_does_not_fabricate_a_process_outcome(jobs):
    job = admit(jobs)
    lease = jobs[1].claim(job.job_id)
    jobs[1].request_control(lease, reason="cancel", principal="original-owner")
    jobs[1].release(lease)
    worker, peer = dispatcher(jobs)
    await worker.deliver(job.job_id)
    current = jobs[1].get(job.job_id)
    assert current.status == "terminal"
    terminal = json.loads(current.terminal_json)
    assert terminal["outcome"] is None and terminal["binding"] is None
    assert peer.calls == [] and current.submit_json is None


@pytest.mark.asyncio
@pytest.mark.parametrize("unavailable", [False, True])
async def test_changed_or_absent_manager_binding_rejects_before_submit_intent(jobs, unavailable):
    job = admit(jobs)
    worker, peer = dispatcher(jobs)
    if unavailable:
        worker.manager = SandboxManager(_Foreground())
    else:
        peer.selected = replace(peer.selected, revision="changed")
    with pytest.raises(SandboxPolicyError, match="profile"):
        await worker.deliver(job.job_id)
    current = jobs[1].get(job.job_id)
    assert current.status == "ready" and current.submit_json is None and peer.calls == []


@pytest.mark.parametrize(
    "options",
    [
        {"lease_seconds": 0},
        {"io_timeout": 16},
        {"poll_seconds": float("nan")},
        {"scan_limit": True},
        {"scan_limit": 1001},
    ],
)
def test_dispatcher_requires_bounded_observation_and_queue_selection(jobs, options):
    with pytest.raises(ValueError, match="bounds"):
        dispatcher(jobs, **options)


@pytest.mark.asyncio
async def test_transient_scan_failure_does_not_stop_later_valid_delivery(jobs, monkeypatch):
    job = admit(jobs)
    worker, peer = dispatcher(jobs, poll_seconds=0.01)
    pending = jobs[1].pending
    attempts = []

    def scan(**kwargs):
        attempts.append(True)
        if len(attempts) == 1:
            raise OSError("private shared database diagnostic")
        return pending(**kwargs)

    monkeypatch.setattr(jobs[1], "pending", scan)
    await worker.start()
    try:
        async with asyncio.timeout(10):
            while jobs[1].get(job.job_id).status != "accepted":
                await asyncio.sleep(0.01)
        assert not worker._task.done()
        assert worker.errors == ("OSError",)
        assert [call[0] for call in peer.calls].count("submit") == 1
    finally:
        await worker.stop()
