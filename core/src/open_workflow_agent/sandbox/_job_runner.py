"""Independent job lifetime using the existing common internal OS machinery."""

from __future__ import annotations

import asyncio
import os
import signal
import tempfile
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

from ..errors import OwaError, SandboxPolicyError
from .backends.internal import InternalSandboxBackend, _OutputBudget
from .contract import SandboxExecutionRequest, SandboxExecutionResult

MUTATION_TIMEOUT_SECONDS = 3.0
MutationFence = AbstractContextManager[Callable[[], None]]
ControlMutation = tuple[MutationFence, Callable[[], None]]


class _OriginalDeadlineReached(Exception):
    """An authenticated control cannot replace the original terminal cause."""


def validate_platform(backend: InternalSandboxBackend) -> None:
    config = backend.config
    if not config.enabled or config.backend != "internal":
        raise SandboxPolicyError("independent internal sandbox jobs are disabled")
    if os.name == "nt":
        if config.file_size_bytes is not None:
            raise SandboxPolicyError("Windows background file-size enforcement is unavailable")
    elif os.name == "posix":
        import resource

        if not callable(getattr(resource, "setrlimit", None)) or not callable(
            getattr(os, "killpg", None)
        ):
            raise SandboxPolicyError("background process resource/group control is unavailable")
        for name, value in (
            ("RLIMIT_CPU", config.cpu_seconds),
            ("RLIMIT_AS", config.memory_bytes),
            ("RLIMIT_FSIZE", config.file_size_bytes),
            ("RLIMIT_NPROC", config.process_count),
        ):
            if value is not None and not hasattr(resource, name):
                raise SandboxPolicyError(
                    "requested background process resource control is unavailable"
                )
        if config.process_count is not None and getattr(os, "getuid", lambda: 0)() == 0:
            raise SandboxPolicyError(
                "privileged background process-count enforcement is unavailable"
            )
    else:
        raise SandboxPolicyError("independent background platform is unavailable")


async def run_job(
    backend: InternalSandboxBackend,
    request: SandboxExecutionRequest,
    *,
    remaining: float,
    started: Callable[[int], None],
    stop: asyncio.Event,
    deadline_reached: Callable[[], None],
    deadline_due: Callable[[], bool],
    launch_fence: Callable[[], MutationFence],
    control_mutation: Callable[[], ControlMutation | None],
) -> tuple[SandboxExecutionResult, bool]:
    """Drain a real exit, including nonzero codes; no fabricated cancelled result."""
    validate_platform(backend)
    backend._validate_request_size(request)
    root = Path(backend.config.workspace_root)
    root.mkdir(parents=True, exist_ok=True)
    begin = time.monotonic()
    process = None
    windows: Any = None
    tasks: list[asyncio.Task[Any]] = []
    with tempfile.TemporaryDirectory(prefix="owa-job-", dir=root) as temporary:
        workspace = Path(temporary)
        executable, arguments = backend._prepare_command(request, workspace)
        environment = backend._build_environment(request, workspace)
        from .credential_guard import process_credential_guard

        output_guard = process_credential_guard(request, environment)
        backend._check_workspace(workspace)
        if os.name == "nt":
            from ._windows_job import WindowsProcessJob

            windows = WindowsProcessJob(backend.config)
        try:
            async with asyncio.timeout(MUTATION_TIMEOUT_SECONDS):
                with launch_fence() as verify:
                    verify()
                    process = await asyncio.create_subprocess_exec(
                        executable,
                        *arguments,
                        cwd=workspace,
                        env=environment,
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        close_fds=True,
                        start_new_session=os.name == "posix",
                        preexec_fn=backend._resource_limiter(),
                        creationflags=0x00000004 if os.name == "nt" else 0,
                    )
                    if windows is not None:
                        # Time can expire while creating a suspended process,
                        # even though competing DB writers remain fenced out.
                        verify()
                        windows.assign_and_resume(process.pid)
                    started(process.pid)
            budget = _OutputBudget(min(backend.config.max_output_bytes, 262_144))
            tasks = [
                asyncio.create_task(backend._write_stdin(process, request.stdin)),
                asyncio.create_task(backend._read_stream(process.stdout, budget)),
                asyncio.create_task(backend._read_stream(process.stderr, budget)),
                asyncio.create_task(process.wait()),
                asyncio.create_task(backend._monitor_workspace(process, workspace)),
            ]
            completion = asyncio.gather(*tasks)
            stopping = asyncio.create_task(stop.wait())
            try:
                while True:
                    done, _ = await asyncio.wait(
                        (completion, stopping),
                        timeout=max(0, remaining - (time.monotonic() - begin)),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if completion in done:
                        terminated = False
                        values = await completion
                        break
                    expired = time.monotonic() - begin >= remaining or deadline_due()
                    # asyncio's timer may wake up before the absolute due at
                    # clock resolution. A timer wake is not a control request.
                    mutation = control_mutation() if stop.is_set() and not expired else None
                    if mutation is None and not expired:
                        stop.clear()
                        stopping.cancel()
                        await asyncio.gather(stopping, return_exceptions=True)
                        stopping = asyncio.create_task(stop.wait())
                        continue
                    if mutation is not None:
                        fence, applied = mutation
                        entered = False
                        try:
                            async with asyncio.timeout(MUTATION_TIMEOUT_SECONDS):
                                with fence as verify:
                                    verify()
                                    if deadline_due():
                                        raise _OriginalDeadlineReached
                                    entered = True
                                    if windows is not None:
                                        windows.close()
                                    else:
                                        await backend._terminate_process(process)
                                    applied()
                        except _OriginalDeadlineReached:
                            expired = True
                        except (OwaError, OSError):
                            if entered:
                                raise
                            # A stale control is not authority to terminate or
                            # unwind into process cleanup. Keep the original due.
                            stop.clear()
                            stopping.cancel()
                            await asyncio.gather(stopping, return_exceptions=True)
                            stopping = asyncio.create_task(stop.wait())
                            continue
                    if mutation is None or expired:
                        deadline_reached()
                        async with asyncio.timeout(MUTATION_TIMEOUT_SECONDS):
                            if windows is not None:
                                windows.close()
                            else:
                                await backend._terminate_process(process)
                    terminated = True
                    # Streams must actually close and drain before a terminal
                    # result is known; otherwise the supervisor records unknown.
                    async with asyncio.timeout(2):
                        values = await completion
                    break
                backend._check_workspace(workspace)
                return (
                    SandboxExecutionResult(
                        request.execution_id,
                        values[3],
                        output_guard.text(values[1]),
                        output_guard.text(values[2]),
                        time.monotonic() - begin,
                    ),
                    terminated,
                )
            finally:
                stopping.cancel()
                await asyncio.gather(stopping, return_exceptions=True)
        finally:
            if windows is not None:
                windows.close()
            if process is not None:
                # A leader's exit does not end an owned descendant group.
                if os.name == "posix":
                    try:
                        killpg = getattr(os, "killpg", None)
                        sigkill = getattr(signal, "SIGKILL", None)
                        if killpg is not None and sigkill is not None:
                            killpg(process.pid, sigkill)
                    except ProcessLookupError:
                        pass
                await backend._terminate_process(process)
            await backend._cancel_tasks(tasks)
