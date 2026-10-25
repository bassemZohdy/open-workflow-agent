"""Actual awaited process/group controls; no engine launcher or paid API."""

from __future__ import annotations

import asyncio
import json
import os
import select
import sys
import time
from pathlib import Path

import pytest
from open_workflow_agent.config import SandboxConfig
from open_workflow_agent.errors import (
    SandboxError,
    SandboxOutputLimitError,
    SandboxPolicyError,
    SandboxProcessError,
    SandboxResourceLimitError,
    SandboxTimeoutError,
)
from open_workflow_agent.sandbox.backends.internal import InternalSandboxBackend
from open_workflow_agent.sandbox.contract import SandboxExecutionRequest, SandboxSecretReference


def backend(tmp_path, **overrides):
    return InternalSandboxBackend(
        SandboxConfig.model_validate(
            {
                "enabled": True,
                "workspace_root": str(tmp_path / "workspace"),
                "executable_search_path": str(Path(sys.executable).parent),
                "inherited_environment": ["SYSTEMROOT"] if os.name == "nt" else [],
                "file_size_bytes": None if os.name == "nt" else 65536,
                "process_count": None
                if os.name == "posix" and getattr(os, "getuid", lambda: 0)() == 0
                else 64,
                "timeout_seconds": 8,
                **overrides,
            }
        )
    )


def script(code, *, identity="controlled-await", **overrides):
    return SandboxExecutionRequest(
        identity, "script", script_language="python", script_code=code, **overrides
    )


class ObservedProcess:
    """Hold the real current OS process identity, not a later adopted PID."""

    def __init__(self, pid):
        self.pid, self.handle, self.pidfd, self.kernel = pid, None, None, None
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            loader = getattr(ctypes, "WinDLL", None)
            assert loader is not None
            self.kernel = loader("kernel32", use_last_error=True)
            self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            self.kernel.OpenProcess.restype = wintypes.HANDLE
            self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            self.kernel.WaitForSingleObject.restype = wintypes.DWORD
            self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            self.kernel.CloseHandle.restype = wintypes.BOOL
            self.handle = self.kernel.OpenProcess(0x100000 | 0x1000, False, pid)
            assert self.handle, "controlled process must be alive at observation"
        elif callable(getattr(os, "pidfd_open", None)):
            self.pidfd = os.pidfd_open(pid)

    def alive(self):
        if self.handle is not None:
            value = self.kernel.WaitForSingleObject(self.handle, 0)
            assert value in {0, 0x102}
            return value == 0x102
        if self.pidfd is not None:
            poll = select.poll()
            poll.register(self.pidfd, select.POLLIN)
            return not poll.poll(0)
        status = Path(f"/proc/{self.pid}/stat")
        if status.exists() and status.read_text().rsplit(")", 1)[1].split()[0] == "Z":
            return False
        try:
            os.kill(self.pid, 0)
            return True
        except ProcessLookupError:
            return False

    def close(self):
        if self.handle is not None:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
        if self.pidfd is not None:
            os.close(self.pidfd)
            self.pidfd = None


async def wait_path(path, running):
    async with asyncio.timeout(5):
        while not path.exists():
            assert not running.done(), "controlled process ended before the observation boundary"
            await asyncio.sleep(0.01)


async def dead(process):
    async with asyncio.timeout(3):
        while process.alive():
            await asyncio.sleep(0.01)


def parent_and_child(tmp_path, *, inherited_pipes, failure=None):
    child_ready, parent_ready = tmp_path / "child.pid", tmp_path / "parent.pid"
    release, late = tmp_path / "release", tmp_path / "late-business"
    child_code = (
        "import os,time\nfrom pathlib import Path\n"
        f"p=Path({str(child_ready)!r})\n"
        "p.with_suffix('.tmp').write_text(str(os.getpid()))\n"
        "os.replace(p.with_suffix('.tmp'),p)\n"
        f"while not Path({str(release)!r}).exists(): time.sleep(.01)\n"
        "time.sleep(1.5)\n"
        f"Path({str(late)!r}).write_text('must-not-survive')\n"
        "time.sleep(60)\n"
    )
    pipes = "" if inherited_pipes else ",stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL"
    code = (
        "import os,sys,time,subprocess\nfrom pathlib import Path\n"
        f"p=Path({str(parent_ready)!r})\n"
        "p.with_suffix('.tmp').write_text(str(os.getpid()))\n"
        "os.replace(p.with_suffix('.tmp'),p)\n"
        f"subprocess.Popen([sys.executable,'-c',{child_code!r}],stdin=subprocess.DEVNULL{pipes},close_fds=True)\n"
        f"while not Path({str(child_ready)!r}).exists(): time.sleep(.01)\n"
        f"while not Path({str(release)!r}).exists(): time.sleep(.01)\n"
    )
    if failure == "output":
        code += "print('x'*20000,flush=True)\n"
    elif failure == "workspace":
        code += "Path('large').write_bytes(b'x'*20000)\n"
    else:
        code += (
            "print('actual-parent-output',flush=True)\n"
            "sys.stderr.write('actual-parent-stderr\\n')\n"
        )
    return code, child_ready, parent_ready, release, late


@pytest.mark.asyncio
@pytest.mark.parametrize("inherited_pipes", [False, True])
async def test_actual_normal_leader_exit_cleans_descendant_without_output_deadlock(
    tmp_path, inherited_pipes
):
    selected = backend(tmp_path)
    code, child_ready, parent_ready, release, late = parent_and_child(
        tmp_path, inherited_pipes=inherited_pipes
    )
    running = asyncio.create_task(selected.execute(script(code)))
    child = parent = None
    try:
        await wait_path(child_ready, running)
        child = ObservedProcess(int(child_ready.read_text()))
        parent = ObservedProcess(int(parent_ready.read_text()))
        assert child.alive() and parent.alive()
        release.write_text("allow-parent-normal-exit")
        result = await asyncio.wait_for(asyncio.shield(running), 5)
        assert result.exit_code == 0 and result.stdout == "actual-parent-output\n"
        assert result.stderr == "actual-parent-stderr\n" and result.status == "completed"
        await dead(child)
        await dead(parent)
        await asyncio.sleep(1.6)
        assert not late.exists() and list(Path(selected.config.workspace_root).iterdir()) == []
        assert (
            selected._active == {}
            and selected._awaited_jobs == {}
            and selected._awaited_starts == {}
        )
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await selected.shutdown()
        if child is not None:
            child.close()
        if parent is not None:
            parent.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("control", ["task-cancel", "backend-cancel", "shutdown", "deadline"])
async def test_actual_interruption_cleans_owned_parent_and_descendant(tmp_path, control):
    selected = backend(tmp_path, timeout_seconds=3)
    code, child_ready, parent_ready, _release, _late = parent_and_child(
        tmp_path, inherited_pipes=True
    )
    running = asyncio.create_task(selected.execute(script(code)))
    child = parent = None
    try:
        await wait_path(child_ready, running)
        child = ObservedProcess(int(child_ready.read_text()))
        parent = ObservedProcess(int(parent_ready.read_text()))
        assert child.alive() and parent.alive()
        if control == "task-cancel":
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(running, 5)
        elif control == "deadline":
            with pytest.raises(SandboxTimeoutError):
                await asyncio.wait_for(running, 6)
        else:
            await asyncio.wait_for(
                selected.cancel("controlled-await")
                if control == "backend-cancel"
                else selected.shutdown(),
                5,
            )
            with pytest.raises(SandboxProcessError):
                await asyncio.wait_for(running, 5)
        await dead(child)
        await dead(parent)
        assert list(Path(selected.config.workspace_root).iterdir()) == [] and selected._active == {}
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await selected.shutdown()
        if child is not None:
            child.close()
        if parent is not None:
            parent.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["output", "workspace"])
async def test_actual_resource_failure_retains_common_error_and_cleans_descendant(
    tmp_path, failure
):
    selected = backend(tmp_path, max_output_bytes=2048, max_workspace_bytes=8192)
    code, child_ready, parent_ready, release, _late = parent_and_child(
        tmp_path, inherited_pipes=True, failure=failure
    )
    running = asyncio.create_task(selected.execute(script(code)))
    child = parent = None
    try:
        await wait_path(child_ready, running)
        child = ObservedProcess(int(child_ready.read_text()))
        parent = ObservedProcess(int(parent_ready.read_text()))
        release.write_text("allow-controlled-resource-failure")
        with pytest.raises(
            SandboxOutputLimitError if failure == "output" else SandboxResourceLimitError
        ):
            await asyncio.wait_for(running, 5)
        await dead(child)
        await dead(parent)
        assert list(Path(selected.config.workspace_root).iterdir()) == []
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await selected.shutdown()
        if child is not None:
            child.close()
        if parent is not None:
            parent.close()


@pytest.mark.asyncio
async def test_concurrent_start_identity_is_fenced_and_pending_launch_is_cancelled(
    tmp_path, monkeypatch
):
    selected = backend(tmp_path)
    entered = asyncio.Event()
    launches = []

    async def held(*args, **kwargs):
        launches.append(args)
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", held)
    request = script("print('no-business')")
    first = asyncio.create_task(selected.execute(request))
    await asyncio.wait_for(entered.wait(), 2)
    with pytest.raises(SandboxPolicyError):
        await selected.execute(request)
    await asyncio.wait_for(selected.cancel(request.execution_id), 2)
    with pytest.raises(asyncio.CancelledError):
        await first
    assert len(launches) == 1 and selected._awaited_starts == {} and selected._active == {}
    assert list(Path(selected.config.workspace_root).iterdir()) == []


@pytest.mark.asyncio
async def test_setup_consumes_original_wall_budget_before_launch(tmp_path, monkeypatch):
    selected = backend(tmp_path, timeout_seconds=0.02)
    prepare = selected._prepare_command
    launches = []

    def slow(*args):
        value = prepare(*args)
        time.sleep(0.03)
        return value

    async def forbidden(*args, **kwargs):
        launches.append(args)
        raise AssertionError("setup cannot reset the original wall budget")

    monkeypatch.setattr(selected, "_prepare_command", slow)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    with pytest.raises(SandboxTimeoutError):
        await selected.execute(script("print('must-not-launch')"))
    assert launches == [] and list(Path(selected.config.workspace_root).iterdir()) == []


@pytest.mark.asyncio
async def test_shutdown_rejects_new_awaited_launch_before_workspace_creation(tmp_path, monkeypatch):
    selected = backend(tmp_path)
    launches = []

    async def forbidden(*args, **kwargs):
        launches.append(args)
        raise AssertionError("shutdown must close new awaited admission")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    await selected.shutdown()
    with pytest.raises(SandboxPolicyError):
        await selected.execute(script("print('must-not-launch')"))
    assert launches == [] and not Path(selected.config.workspace_root).exists()
    assert selected.capabilities()["enabled"] is False
    assert selected.capabilities()["resourceLimits"]["processTree"] == "unavailable"


@pytest.mark.asyncio
async def test_real_environment_stdin_argv_and_inheritable_fd_boundary(tmp_path, monkeypatch):
    monkeypatch.setenv("UNDECLARED_RUNTIME_SECRET", "must-not-inherit")
    monkeypatch.setenv("APPROVED_TEST_REFERENCE", "controlled-secret")
    selected = backend(tmp_path, secret_environment=["APPROVED_TEST_REFERENCE"])
    path = tmp_path / "inherited-file"
    with path.open("wb") as source:
        os.set_inheritable(source.fileno(), True)
        identity = os.fstat(source.fileno())
        code = (
            "import os,sys,json,tempfile\n"
            "try:\n"
            f" s=os.fstat({source.fileno()})\n"
            f" inherited=(s.st_dev,s.st_ino)==({identity.st_dev},{identity.st_ino})\n"
            "except OSError: inherited=False\n"
            "print(json.dumps({'cwd':os.getcwd(),'stdin':sys.stdin.read(),'argv':sys.argv[1:],"
            "'ambient':os.getenv('UNDECLARED_RUNTIME_SECRET'),'approved':os.getenv('TOKEN')=='controlled-secret',"
            "'temp':os.getenv('TEMP'),'tmp':os.getenv('TMP'),'tempfile':tempfile.gettempdir(),"
            "'inherited_fd':inherited}))\n"
        )
        result = await selected.execute(
            script(
                code,
                stdin="original input",
                arguments=("one argument",),
                environment=(("TOKEN", SandboxSecretReference("APPROVED_TEST_REFERENCE")),),
            )
        )
    value = json.loads(result.stdout)
    assert value == {
        "cwd": value["cwd"],
        "stdin": "original input",
        "argv": ["one argument"],
        "ambient": None,
        "approved": True,
        "temp": value["cwd"],
        "tmp": value["cwd"],
        "tempfile": value["cwd"],
        "inherited_fd": False,
    }
    assert Path(value["cwd"]).parent == Path(selected.config.workspace_root)
    assert not Path(value["cwd"]).exists()
    assert "controlled-secret" not in result.stdout and "must-not-inherit" not in result.stdout


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name", ["PATH", "HOME", "TMPDIR", "TEMP", "TMP", "path", "home", "tmpdir", "temp", "tmp"]
)
async def test_runtime_isolation_environment_cannot_be_overridden(tmp_path, name):
    selected = backend(tmp_path)
    with pytest.raises(SandboxPolicyError, match="cannot override runtime isolation variables"):
        await selected.execute(script("print('not-started')", environment=((name, "C:/outside"),)))
    assert list(Path(selected.config.workspace_root).iterdir()) == []


@pytest.mark.asyncio
async def test_nonzero_leader_exit_preserves_common_error(tmp_path):
    selected = backend(tmp_path)
    with pytest.raises(SandboxProcessError) as failure:
        await selected.execute(script("import sys\nsys.exit(7)\n"))
    assert failure.value.details == {"exit_code": 7}
    assert list(Path(selected.config.workspace_root).iterdir()) == []


@pytest.mark.skipif(os.name != "nt", reason="actual Windows Job controls")
@pytest.mark.asyncio
async def test_windows_unsupported_file_limit_rejects_before_workspace_or_process(
    tmp_path, monkeypatch
):
    selected = backend(tmp_path, file_size_bytes=1024)
    touched = []

    async def forbidden(*args, **kwargs):
        touched.append("launch")
        raise AssertionError("unsupported requested limit cannot launch")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    assert selected.capabilities()["enabled"] is False
    with pytest.raises(SandboxPolicyError):
        await selected.execute(script("print('must-not-launch')"))
    assert touched == [] and not Path(selected.config.workspace_root).exists()


@pytest.mark.skipif(os.name != "nt", reason="actual suspended Windows process")
@pytest.mark.asyncio
async def test_windows_assignment_failure_never_runs_script_and_reaps_suspended_child(
    tmp_path, monkeypatch
):
    from open_workflow_agent.sandbox import _windows_job

    selected = backend(tmp_path)
    original = _windows_job.WindowsProcessJob
    create = asyncio.create_subprocess_exec
    processes = []

    class RejectAssignment(original):
        def assign_and_resume(self, pid):
            raise SandboxProcessError("controlled assignment failure")

    async def observe(*args, **kwargs):
        assert kwargs["creationflags"] & 0x4
        process = await create(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(_windows_job, "WindowsProcessJob", RejectAssignment)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", observe)
    marker = tmp_path / "must-not-run"
    with pytest.raises(SandboxProcessError):
        await selected.execute(
            script(f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n")
        )
    assert len(processes) == 1 and processes[0].returncode is not None and not marker.exists()
    assert selected._active == {} and selected._awaited_jobs == {}


@pytest.mark.skipif(os.name != "nt", reason="actual Windows Job resource enforcement")
@pytest.mark.asyncio
@pytest.mark.parametrize("limit", ["cpu", "memory", "process-count"])
async def test_actual_windows_requested_job_resources_are_enforced(tmp_path, limit):
    if limit == "cpu":
        # Hosted Windows runners can take several wall seconds to accrue one
        # process CPU second under the full test matrix. Keep the operation
        # deadline much longer than the Job CPU quota so the assertion proves
        # CPU enforcement rather than a wall-clock timeout.
        selected = backend(tmp_path, cpu_seconds=1, timeout_seconds=20)
        marker = tmp_path / "cpu-started"
        code = (
            "from pathlib import Path\n"
            f"Path({str(marker)!r}).write_text('started')\n"
            "while True: pass\n"
        )
        with pytest.raises(SandboxError) as failure:
            await asyncio.wait_for(selected.execute(script(code)), 25)
        assert not isinstance(failure.value, SandboxTimeoutError), (
            "CPU proof must not be a wall timeout"
        )
        assert marker.read_text() == "started"
    elif limit == "memory":
        selected = backend(tmp_path, memory_bytes=96 * 1024 * 1024)
        code = (
            "try:\n value=bytearray(768*1024*1024)\n print('unrestricted')\n"
            "except MemoryError:\n print('bounded')\n"
        )
        assert (await selected.execute(script(code))).stdout == "bounded\n"
    else:
        selected = backend(tmp_path, process_count=2)
        code = (
            "import subprocess,sys,time\nchildren=[]; bounded=False\n"
            "try:\n"
            " for index in range(4):\n"
            "  try:\n"
            "   p=subprocess.Popen([sys._base_executable,'-c','import time;time.sleep(60)'],"
            "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
            "  except OSError:\n   bounded=True; break\n"
            "  children.append(p); time.sleep(.1)\n"
            "  if p.poll() is not None:\n   bounded=True; break\n"
            " print('bounded' if bounded else 'unrestricted')\n"
            "finally:\n"
            " for p in children:\n"
            "  if p.poll() is None: p.terminate()\n"
            "  p.wait(timeout=2)\n"
        )
        assert (await selected.execute(script(code))).stdout == "bounded\n"
    assert (
        selected.capabilities()["resourceLimits"][
            {"cpu": "cpu", "memory": "memory", "process-count": "processCount"}[limit]
        ]
        is True
    )
    assert list(Path(selected.config.workspace_root).iterdir()) == []


@pytest.mark.skipif(os.name != "posix", reason="actual POSIX file-size control")
@pytest.mark.asyncio
async def test_actual_posix_file_size_limit_is_enforced(tmp_path):
    selected = backend(tmp_path, file_size_bytes=2048)
    code = (
        "from pathlib import Path\ntry:\n Path('bounded').write_bytes(b'x'*8192)\n"
        "except OSError:\n print('bounded')\nelse:\n print('unrestricted')\n"
    )
    assert (await selected.execute(script(code))).stdout == "bounded\n"
    assert list(Path(selected.config.workspace_root).iterdir()) == []


@pytest.mark.parametrize("invalid", [False, True])
def test_awaited_capabilities_report_only_current_enforced_platform_controls(tmp_path, invalid):
    selected = backend(tmp_path)
    if invalid:
        selected.config.timeout_seconds = float("inf")
    value = selected.capabilities()
    assert value["enabled"] is (not invalid) and value["internalProcess"] is (not invalid)
    assert value["hardIsolation"] is False and value["networkIsolation"] == "none"
    assert value["resourceLimits"]["windowsJob"] is (os.name == "nt" and not invalid)
    assert value["resourceLimits"]["posixRlimit"] is (os.name == "posix" and not invalid)
    assert value["resourceLimits"]["fileSize"] is (os.name == "posix" and not invalid)
    assert value["script"]["enabled"] is (not invalid)
