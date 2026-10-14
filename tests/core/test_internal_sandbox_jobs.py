"""Actual bundled-supervisor lifetime and private IPC controls (no model/API)."""

from __future__ import annotations

import asyncio
import errno
import json
import os
import signal
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig, SandboxConfig
from open_workflow_agent.effects import EffectIdentity
from open_workflow_agent.errors import InvocationStateConflict, OwaError, SandboxPolicyError
from open_workflow_agent.sandbox import compile_sandbox_workflow
from open_workflow_agent.sandbox._job_ipc import (
    MAX_FRAME_BYTES,
    check_path,
    create_key,
    private_directory,
    read_frame,
    read_key,
    write_frame,
)
from open_workflow_agent.sandbox.backends.internal import InternalSandboxBackend
from open_workflow_agent.sandbox.contract import SandboxExecutionRequest
from open_workflow_agent.sandbox.internal_jobs import (
    InternalSandboxJobBackend,
    immutable,
    internal_job_profile,
    launch_value,
)
from open_workflow_agent.sandbox.job_backend import SandboxJobLaunch
from open_workflow_agent.sandbox.job_dispatcher import SandboxJobDispatcher
from open_workflow_agent.sandbox.jobs import SandboxJobBackendBinding, SandboxJobStore, job_id
from open_workflow_agent.sandbox.manager import SandboxManager
from open_workflow_agent.services import RuntimeServices


def _windows_pid_is_gone(pid):
    """Check absence/actual exit before accepting Windows stale-PID cleanup."""
    import ctypes
    from ctypes import wintypes

    if type(pid) is not int or pid <= 0:
        return False
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return ctypes.get_last_error() == 87
    try:
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return code.value != 259  # STILL_ACTIVE
    finally:
        kernel.CloseHandle(handle)


@pytest.fixture
def supervisor(tmp_path):
    records = []

    def prepare(code, *, seconds=12, output=4096, workspace=65536):
        root = tmp_path / str(len(records))
        config = RuntimeConfig.model_validate(
            {
                "sandbox": {
                    "enabled": True,
                    "timeout_seconds": 30,
                    "workspace_root": str(root / "workspace"),
                    "executable_search_path": str(Path(sys.executable).parent),
                    "file_size_bytes": None,
                    "process_count": None,
                    "max_output_bytes": output,
                    "max_workspace_bytes": workspace,
                    "inherited_environment": ["SYSTEMROOT"] if os.name == "nt" else [],
                }
            }
        )
        services = RuntimeServices(config, model=FakeModel(), database_root=root)
        services.recovery_definitions.profile_provider = lambda engine: {
            "profile": "actual-internal-supervisor-test",
            "engine": engine,
            "continuation_supported": True,
        }
        plan = compile_sandbox_workflow(
            {
                "document": {
                    "dsl": "1.0.3",
                    "namespace": "jobs",
                    "name": "supervisor",
                    "version": "1.0.0",
                },
                "do": [
                    {
                        "job": {
                            "run": {"await": True, "script": {"language": "python", "code": code}}
                        }
                    }
                ],
            },
            sandbox=config.sandbox,
        )
        handle = services.invocations.create(
            engine="portable",
            session_id="original-context",
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
            script_code=code,
            stdin="original transformed input",
            invocation_id=handle.invocation_id,
            task_reference=identity.task_reference,
        )
        store = SandboxJobStore(
            services, profile_provider=lambda: internal_job_profile(config.sandbox)
        )
        job = store.admit(
            identity,
            handle,
            request,
            {"original": None},
            deadline_at=time.time() + seconds,
            execution_lease=owner,
        )
        lease = store.claim(job.job_id, seconds=90)
        store.begin_submit(lease)
        launch = SandboxJobLaunch.from_verified_job(store.get(job.job_id), lease)
        backend = InternalSandboxJobBackend(config.sandbox, store)
        manager = SandboxManager(InternalSandboxBackend(config.sandbox), job_backend=backend)
        records.append((services, store, lease, owner, backend, launch))
        return services, store, lease, backend, manager, launch

    yield prepare
    for services, store, lease, owner, _, _ in records:
        try:
            store.release(lease)
            services.execution_ownership.release(owner)
        except OwaError:
            pass
        services.close()


async def terminal(backend, launch, binding=None, *, allow_unknown=False):
    async with asyncio.timeout(20):
        while True:
            value = await backend.inspect(launch, binding)
            if value.state == "terminal" or (allow_unknown and value.state == "unknown"):
                return value
            await asyncio.sleep(0.025)


@pytest.mark.parametrize("exit_code", [0, 7])
async def test_actual_supervisor_records_real_exit_once_with_restricted_environment(
    supervisor, tmp_path, monkeypatch, exit_code
):
    ledger = tmp_path / "business.jsonl"
    monkeypatch.setenv("OWA_UNDECLARED_RUNTIME_SECRET", "must-not-cross-boundary")
    code = (
        "import os,sys,json\nfrom pathlib import Path\n"
        f"p=Path({str(ledger)!r})\n"
        "with p.open('a') as f: f.write(json.dumps({'pid':os.getpid(), 'cwd':os.getcwd(), "
        "'ambient':os.getenv('OWA_UNDECLARED_RUNTIME_SECRET'), 'input':sys.stdin.read()})+'\\n')\n"
        f"print('real-output',flush=True)\nsys.exit({exit_code})\n"
    )
    services, store, lease, backend, manager, launch = supervisor(code)
    observed = await backend.submit(launch)
    assert observed.binding is not None and observed.state != "unknown"
    store.record_accepted(lease, observed.binding)
    # Stopping the runtime's synchronous process manager does not own this job.
    await manager.shutdown()
    result = await terminal(backend, launch, observed.binding)
    assert result.state == "terminal" and result.reason == "exited"
    assert result.outcome.exit_code == exit_code and result.outcome.stdout == "real-output\n"
    assert result.original_deadline is None and result.control_ack_generation is None
    store.record_terminal(lease, result.binding, result.outcome)
    rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["pid"] != os.getpid()
    assert rows[0]["ambient"] is None and rows[0]["input"] == "original transformed input"
    assert Path(rows[0]["cwd"]).parent == backend.root.parent
    assert services.model.calls == []
    with pytest.raises(OwaError):
        await backend.submit(launch)
    assert len([json.loads(line) for line in ledger.read_text().splitlines()]) == 1


@pytest.mark.parametrize("late_control", ["deadline", "cancel"])
async def test_authenticated_autonomous_deadline_keeps_original_cause_and_first_control(
    supervisor, late_control
):
    _, store, lease, backend, manager, launch = supervisor(
        "import time\nprint('started',flush=True)\ntime.sleep(60)\n", seconds=7
    )
    observed = await backend.submit(launch)
    assert observed.state == "running" and observed.binding is not None
    store.record_accepted(lease, observed.binding)
    await manager.shutdown()
    result = await terminal(backend, launch, observed.binding)
    assert result.state == "terminal" and result.reason == "deadline"
    assert result.original_deadline.deadline_at == launch.deadline_at
    assert result.control_ack_generation is None
    control = store.request_control(
        lease,
        reason=late_control,
        principal="original-owner" if late_control == "cancel" else None,
    )
    already_known = await backend.cancel(
        launch, observed.binding, generation=control["generation"], reason=late_control
    )
    assert already_known == result
    dispatcher = SandboxJobDispatcher(store, manager)
    dispatcher._record(lease, result)
    retained = store.get(lease.job_id)
    assert (
        retained.status == "terminal" and json.loads(retained.terminal_json)["reason"] == "deadline"
    )
    assert json.loads(retained.control_json) == control and retained.control_ack_json is None
    assert retained.transformed_input == {"original": None}


async def test_stale_signed_control_does_not_disable_later_authorized_cancel(supervisor):
    _, store, lease, backend, _, launch = supervisor("import time\ntime.sleep(60)\n")
    observed = await backend.submit(launch)
    assert observed.state == "running" and observed.binding is not None
    store.record_accepted(lease, observed.binding)
    directory, key, bootstrap = backend._read(launch)
    write_frame(
        directory / "control.json",
        {
            "launch": launch_value(launch),
            "instance_id": bootstrap["instance_id"],
            "generation": 99,
            "reason": "cancel",
        },
        key,
    )
    await asyncio.sleep(0.15)
    assert (await backend.inspect(launch, observed.binding)).state == "running"
    control = store.request_control(lease, reason="cancel", principal="original-owner")
    value = await backend.cancel(
        launch, observed.binding, generation=control["generation"], reason="cancel"
    )
    assert value.state == "terminal" and value.reason == "cancel"
    assert value.control_ack_generation == control["generation"] and value.original_deadline is None


async def test_actual_job_owner_takeover_fences_old_control_before_ipc(supervisor):
    _, store, old_lease, backend, _, old_launch = supervisor("import time\ntime.sleep(60)\n")
    observed = await backend.submit(old_launch)
    assert observed.state == "running" and observed.binding is not None
    store.record_accepted(old_lease, observed.binding)
    store.release(old_lease)
    current = store.claim(old_lease.job_id, seconds=90)
    assert current.generation == old_lease.generation + 1 and current.owner != old_lease.owner
    launch = SandboxJobLaunch.from_verified_job(store.get(current.job_id), current)
    try:
        control = store.request_control(current, reason="cancel", principal="original-owner")
        path = backend._directory(old_launch) / "control.json"
        assert not path.exists()
        with pytest.raises(OwaError):
            await backend.cancel(
                old_launch, observed.binding, generation=control["generation"], reason="cancel"
            )
        assert not path.exists()
        value = await backend.cancel(
            launch, observed.binding, generation=control["generation"], reason="cancel"
        )
        assert value.state == "terminal" and value.reason == "cancel"
        assert value.control_ack_generation == control["generation"]
    finally:
        store.release(current)


@pytest.mark.parametrize("damage", ["lease", "profile", "nonce", "request"])
async def test_invalid_original_authority_rejects_before_any_ipc_or_process(supervisor, damage):
    _, store, lease, backend, _, launch = supervisor("print('must-not-execute')\n")
    if damage == "lease":
        store.release(lease)
    elif damage == "profile":
        launch = replace(launch, profile=replace(launch.profile, revision="foreign"))
    elif damage == "nonce":
        launch = replace(launch, submit_id="foreign")
    else:
        launch = replace(launch, request=replace(launch.request, script_code="print('foreign')"))
    with pytest.raises(OwaError):
        await backend.submit(launch)
    assert not backend.root.exists()


@pytest.mark.parametrize("failure", ["output", "workspace"])
async def test_actual_resource_failure_is_unknown_without_invented_exit_or_relaunch(
    supervisor, failure, tmp_path
):
    child_marker = tmp_path / "workspace-child.pid"
    code = (
        "print('x'*20000,flush=True)\n"
        if failure == "output"
        else (
            "import os,time\nfrom pathlib import Path\n"
            f"Path({str(child_marker)!r}).write_text(str(os.getpid()))\n"
            "Path('large').write_bytes(b'x'*20000)\n"
            "time.sleep(60)\n"
        )
    )
    _, store, lease, backend, _, launch = supervisor(code, output=2048, workspace=2048)
    observed = await backend.submit(launch)
    child_pid = None
    if failure == "workspace":
        async with asyncio.timeout(5):
            while not child_marker.exists():
                await asyncio.sleep(0.025)
        child_pid = int(child_marker.read_text())
    value = (
        observed
        if observed.state == "unknown"
        else await terminal(backend, launch, observed.binding, allow_unknown=True)
    )
    assert value.state == "unknown" and value.outcome is None
    before = (backend._directory(launch) / "bootstrap.json").read_bytes()
    assert (await backend.submit(launch)).state == "unknown"
    assert (backend._directory(launch) / "bootstrap.json").read_bytes() == before
    assert store.get(lease.job_id).terminal_json is None
    if failure == "workspace":
        assert child_pid is not None
        async with asyncio.timeout(3):
            while _process_is_alive(child_pid):
                await asyncio.sleep(0.025)
        assert not _process_is_alive(child_pid)
        workspace_root = Path(backend.config.workspace_root)
        # The persistent supervisor control directory is deployment state;
        # only the per-job execution workspace must be removed.
        assert (workspace_root / ".owa-job-supervisors").is_dir()
        assert not any(child.name.startswith("owa-job-") for child in workspace_root.iterdir())
        assert backend.capabilities()["resourceLimits"]["workspaceQuota"] == "monitored"


def _process_is_alive(pid: int) -> bool:
    if os.name == "nt":
        return not _windows_pid_is_gone(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def test_actual_supervisor_loss_is_unknown_and_never_relaunches(supervisor, tmp_path):
    marker = tmp_path / "child.pid"
    code = (
        "import os,time\nfrom pathlib import Path\n"
        f"Path({str(marker)!r}).write_text(str(os.getpid()))\n"
        "time.sleep(60)\n"
    )
    _, store, lease, backend, _, launch = supervisor(code)
    observed = await backend.submit(launch)
    assert observed.state == "running" and observed.binding is not None
    async with asyncio.timeout(5):
        while not marker.exists():
            await asyncio.sleep(0.025)
    directory, key, _ = backend._read(launch)
    signed = read_frame(directory / "observation.json", key)
    supervisor_pid = signed["pid"]
    child_pid = int(marker.read_text())
    assert supervisor_pid != os.getpid() and child_pid != supervisor_pid
    bootstrap = (directory / "bootstrap.json").read_bytes()
    try:
        os.kill(supervisor_pid, signal.SIGTERM if os.name == "nt" else signal.SIGKILL)
        await asyncio.sleep(2.25)
        lost = await backend.inspect(launch, observed.binding)
        assert lost.state == "unknown" and lost.outcome is None
        assert (await backend.submit(launch)).state == "unknown"
        assert (directory / "bootstrap.json").read_bytes() == bootstrap
        assert store.get(lease.job_id).terminal_json is None
    finally:
        # Supervisor loss is unknown. On POSIX it also loses future wall-clock
        # enforcement; the fixture explicitly cleans the existing group.
        try:
            if os.name == "posix":
                os.killpg(child_pid, signal.SIGKILL)
            else:
                os.kill(child_pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        except OSError as error:
            if not (
                os.name == "nt"
                and getattr(error, "winerror", None) == 87
                and _windows_pid_is_gone(child_pid)
            ):
                raise


def test_private_ipc_authentication_size_duplicate_json_and_key_integrity(tmp_path):
    directory = tmp_path / "ipc"
    private_directory(directory)
    key = create_key(directory / "auth.key")
    assert read_key(directory / "auth.key") == key
    frame = directory / "frame.json"
    write_frame(frame, {"original": None}, key)
    assert read_frame(frame, key) == {"original": None}
    with pytest.raises(SandboxPolicyError):
        read_frame(frame, b"z" * 32)
    with pytest.raises(SandboxPolicyError):
        write_frame(frame, "x" * MAX_FRAME_BYTES, key)
    frame.write_bytes(b'{"payload":1,"payload":2,"mac":"not-authentic"}')
    with pytest.raises(SandboxPolicyError):
        read_frame(frame, key)
    (directory / "auth.key").write_bytes(b"truncated")
    with pytest.raises(SandboxPolicyError):
        read_key(directory / "auth.key")


@pytest.mark.parametrize("failure", ["write", "fsync", "replace"])
def test_failed_ipc_publication_removes_only_owned_temp_and_keeps_original(
    tmp_path, monkeypatch, failure
):
    from open_workflow_agent.sandbox import _job_ipc

    directory = tmp_path / "ipc"
    private_directory(directory)
    key = create_key(directory / "auth.key")
    target = directory / "observation.json"
    write_frame(target, {"known": "original"}, key)
    retained = target.read_bytes()
    unrelated = directory / "another.tmp-foreign"
    unrelated.write_bytes(b"not-ours")

    def fail(*args, **kwargs):
        raise OSError("controlled fixture write failure")

    if failure == "write":
        original = _job_ipc.os.fdopen

        class Writer:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.stream.close()

            def write(self, value):
                self.stream.write(value[:16])
                self.stream.flush()
                fail()

        monkeypatch.setattr(
            _job_ipc.os, "fdopen", lambda *args, **kwargs: Writer(original(*args, **kwargs))
        )
    else:
        monkeypatch.setattr(_job_ipc.os, failure, fail)
    for _ in range(3):
        with pytest.raises(OSError):
            write_frame(target, {"new": "never-published"}, key)
        assert target.read_bytes() == retained
        assert list(directory.glob("observation.json.tmp-*")) == []
        assert unrelated.read_bytes() == b"not-ours"


def test_failed_exclusive_bootstrap_retains_first_submission_tombstone(tmp_path, monkeypatch):
    from open_workflow_agent.sandbox import _job_ipc

    directory = tmp_path / "ipc"
    private_directory(directory)
    key = create_key(directory / "auth.key")
    target = directory / "bootstrap.json"

    def fail(*args, **kwargs):
        raise OSError("controlled fixture fsync failure")

    monkeypatch.setattr(_job_ipc.os, "fsync", fail)
    with pytest.raises(OSError):
        write_frame(target, {"first": "retained-only"}, key, exclusive=True)
    assert target.exists()
    retained = target.read_bytes()
    with pytest.raises(FileExistsError):
        write_frame(target, {"second": "must-not-replace"}, key, exclusive=True)
    assert target.read_bytes() == retained
    assert list(directory.glob("bootstrap.json.tmp-*")) == []


@pytest.mark.skipif(os.name != "posix", reason="POSIX owner/mode control")
def test_posix_private_ipc_rejects_broadened_permissions_and_symlink(tmp_path):
    directory = tmp_path / "ipc"
    private_directory(directory)
    os.chmod(directory, 0o755)
    with pytest.raises(SandboxPolicyError):
        check_path(directory, directory=True)
    os.chmod(directory, 0o700)
    target = tmp_path / "other"
    target.mkdir()
    (directory / "link").symlink_to(target, target_is_directory=True)
    with pytest.raises(SandboxPolicyError):
        private_directory(directory / "link")


@pytest.mark.skipif(os.name != "nt", reason="Windows protected DACL control")
def test_windows_private_ipc_rejects_broadened_dacl_without_repair(tmp_path):
    import ctypes
    from ctypes import wintypes

    from open_workflow_agent.sandbox._job_ipc import _windows_private

    directory = tmp_path / "ipc"
    private_directory(directory)
    loader = ctypes.WinDLL
    advapi, kernel = (
        loader("advapi32", use_last_error=True),
        loader("kernel32", use_last_error=True),
    )
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    advapi.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    advapi.SetFileSecurityW.restype = wintypes.BOOL
    kernel.LocalFree.argtypes, kernel.LocalFree.restype = [ctypes.c_void_p], ctypes.c_void_p
    descriptor = ctypes.c_void_p()
    assert advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        "D:P(A;;FA;;;WD)", 1, ctypes.byref(descriptor), None
    )
    try:
        assert advapi.SetFileSecurityW(str(directory), 0x80000004, descriptor)
        with pytest.raises(SandboxPolicyError):
            check_path(directory, directory=True)
        with pytest.raises(SandboxPolicyError):
            private_directory(directory)
    finally:
        kernel.LocalFree(descriptor)
        _windows_private(directory)


def test_unsupported_platform_limits_reject_before_launch(tmp_path):
    from open_workflow_agent.sandbox._job_runner import validate_platform

    config = SandboxConfig.model_validate(
        {
            "enabled": True,
            "workspace_root": str(tmp_path),
            "executable_search_path": str(Path(sys.executable).parent),
            "file_size_bytes": None if os.name != "nt" else 1,
            "process_count": 1
            if os.name == "posix" and getattr(os, "getuid", lambda: 0)() == 0
            else None,
        }
    )
    if os.name == "nt" or config.process_count is not None:
        with pytest.raises(SandboxPolicyError):
            validate_platform(InternalSandboxBackend(config))
    else:
        validate_platform(InternalSandboxBackend(config))


@pytest.mark.skipif(os.name != "nt", reason="Windows denied-open observation race")
@pytest.mark.parametrize("mode", ["transient", "persistent", "control-takeover"])
async def test_observation_denied_open_rechecks_proofs_with_bounded_wait(
    supervisor, monkeypatch, mode
):
    """Exercise signed private frames without launching or changing a job."""
    from open_workflow_agent.sandbox import internal_jobs

    services, store, lease, backend, _, launch = supervisor("print('must not launch')")
    persistent = mode == "persistent"
    control_takeover = mode == "control-takeover"
    Path(backend.config.workspace_root).mkdir(parents=True, exist_ok=True)
    private_directory(backend.root)
    directory = backend._directory(launch)
    private_directory(directory)
    key = create_key(directory / "auth.key")
    instance_id = "supervisor_" + "a" * 32
    write_frame(
        directory / "bootstrap.json",
        {
            "launch": launch_value(launch),
            "profile": asdict(backend.profile()),
            "config": backend.config.model_dump(mode="json", exclude_unset=True),
            "database": str(backend.database),
            "instance_id": instance_id,
        },
        key,
        exclusive=True,
    )
    target = directory / "observation.json"
    write_frame(
        target,
        {
            "immutable": immutable(launch),
            "instance_id": instance_id,
            "state": "running",
            "outcome": None,
            "reason": "exited",
            "control_ack_generation": None,
            "pid": os.getpid(),
        },
        key,
        exclusive=True,
    )
    write_frame(
        directory / "heartbeat.json", {"instance_id": instance_id, "time": time.time()}, key
    )
    control = (
        store.request_control(lease, reason="cancel", principal="original-owner")
        if control_takeover
        else None
    )

    def database_snapshot():
        snapshots = []
        for connection in (store.connection, services.invocations.connection):
            tables = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            snapshots.append(
                {
                    name: sorted(
                        repr(tuple(row))
                        for row in connection.execute(
                            'SELECT * FROM "' + name.replace('"', '""') + '"'
                        ).fetchall()
                    )
                    for (name,) in tables
                }
            )
        return snapshots

    before_database = database_snapshot()
    before_files = {item.name: item.read_bytes() for item in directory.iterdir()}
    attempts, verifications, reads, delays, observations, takeovers = [], [], [], [], [], []
    real_open, real_verify, real_read, real_sleep, real_observation = (
        os.open,
        backend._verify,
        backend._read,
        asyncio.sleep,
        backend._observation,
    )
    denial = PermissionError(errno.EACCES, "controlled observation denied-open")

    def denied_open(path, flags, *args, **kwargs):
        if Path(path) == target:
            attempts.append(path)
            if persistent or len(attempts) == 1:
                raise denial
        return real_open(path, flags, *args, **kwargs)

    def verified_read(value):
        reads.append(value)
        return real_read(value)

    def verify(*args, **kwargs):
        verifications.append(args)
        return real_verify(*args, **kwargs)

    def observe(*args, **kwargs):
        observations.append(args)
        return real_observation(*args, **kwargs)

    async def bounded_sleep(delay):
        delays.append(delay)
        if control_takeover:
            assert len(delays) == 1
            store.release(lease)
            replacement = store.claim(lease.job_id, seconds=90)
            assert replacement.generation == lease.generation + 1
            assert replacement.owner != lease.owner
            takeovers.append((replacement, database_snapshot()))
        await real_sleep(delay)

    def forbidden_launch(*args, **kwargs):
        pytest.fail("observation inspection must never launch or resubmit")

    monkeypatch.setattr(os, "open", denied_open)
    monkeypatch.setattr(backend, "_read", verified_read)
    monkeypatch.setattr(backend, "_verify", verify)
    monkeypatch.setattr(backend, "_observation", observe)
    monkeypatch.setattr(internal_jobs.asyncio, "sleep", bounded_sleep)
    monkeypatch.setattr(internal_jobs.subprocess, "Popen", forbidden_launch)
    if control_takeover:
        with pytest.raises(InvocationStateConflict, match="ownership is stale"):
            await backend.cancel(
                launch,
                SandboxJobBackendBinding(
                    lease.job_id, launch.request_digest, "embedded-sqlite-supervisor", instance_id
                ),
                generation=control["generation"],
                reason="cancel",
            )
    elif persistent:
        with pytest.raises(PermissionError) as raised:
            await backend.inspect(launch, None)
        assert raised.value is denial
    else:
        result = await backend.inspect(launch, None)
        assert result.state == "running" and result.outcome is None
        assert result.binding is not None
        assert result.binding.job_id == lease.job_id
        assert result.binding.request_digest == launch.request_digest
        assert result.binding.instance_id == instance_id
    expected_attempts = 1 if control_takeover else 3 if persistent else 2
    assert len(attempts) == len(reads) == expected_attempts
    assert len(verifications) == (2 if control_takeover else expected_attempts)
    assert len(observations) == (0 if persistent or control_takeover else 1)
    assert all(item == launch for item in reads)
    expected_verification = (
        (launch, "control", control["generation"], "cancel")
        if control_takeover
        else (launch, "inspect", None, None)
    )
    assert all(item == expected_verification for item in verifications)
    assert delays == ([0.02, 0.04] if persistent else [0.02])
    assert sum(delays) <= 0.06
    if control_takeover:
        assert database_snapshot() == takeovers[0][1]
        for original, replaced in zip(before_database, takeovers[0][1], strict=True):
            assert {
                name: rows for name, rows in original.items() if name != "sandbox_job_ownership"
            } == {name: rows for name, rows in replaced.items() if name != "sandbox_job_ownership"}
        assert (
            json.loads(store.get(lease.job_id).admission_json)["deadline_at"] == launch.deadline_at
        )
    else:
        assert database_snapshot() == before_database
    assert {item.name: item.read_bytes() for item in directory.iterdir()} == before_files
    assert not (directory / "control.json").exists()
    if control_takeover:
        store.release(takeovers[0][0])
