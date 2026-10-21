"""Reusable internal sandbox backend: controlled child-process execution.

Shared utility consumed by every engine through SandboxManager. This module
owns all OS-level machinery (environment filtering, rlimits, process-tree
termination, workspace monitoring) so engines never build their own execution
paths.
"""

from __future__ import annotations

import asyncio
import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

from ...config import SandboxConfig
from ...errors import (
    SandboxOutputLimitError,
    SandboxPolicyError,
    SandboxProcessError,
    SandboxResourceLimitError,
    SandboxTimeoutError,
)
from ..contract import (
    SandboxExecutionRequest,
    SandboxExecutionResult,
    SandboxSecretReference,
    SandboxStdioRequest,
    SandboxStdioSession,
    is_reserved_environment_name,
)

WORKSPACE_TEMPORARY_ENVIRONMENT = frozenset({"TEMP", "TMP"})


class _OutputBudget:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0

    def add(self, amount: int) -> None:
        self.used += amount
        if self.used > self.limit:
            raise SandboxOutputLimitError(
                "sandbox output limit exceeded",
                details={"max_output_bytes": self.limit},
            )


class InternalSandboxBackend:
    """Controlled child-process backend that requires no Docker/Kubernetes runtime."""

    def __init__(self, config: SandboxConfig) -> None:
        self.config = config
        self._active: dict[str, asyncio.subprocess.Process] = {}
        self._awaited_jobs: dict[str, Any] = {}
        self._awaited_starts: dict[str, asyncio.Task[Any]] = {}
        self._awaited_stops: set[str] = set()
        self._stdio_sessions: dict[str, SandboxStdioSession] = {}
        self._stdio_starting: set[str] = set()
        self._stdio_starts: dict[str, asyncio.Task[Any]] = {}
        self._stdio_closing = False

    @asynccontextmanager
    async def open_stdio(self, request: SandboxStdioRequest) -> AsyncIterator[SandboxStdioSession]:
        from ..stdio import open_internal_stdio

        async with open_internal_stdio(self, request) as session:
            yield session

    def capabilities(self) -> dict[str, Any]:
        posix = os.name == "posix"
        enabled = self.config.enabled and self.config.backend == "internal"
        awaited = enabled and not self._stdio_closing
        try:
            self._validate_awaited_controls()
        except SandboxPolicyError:
            awaited = False
        stdio_enabled = enabled and self.config.mcp_stdio.enabled and not self._stdio_closing
        if stdio_enabled:
            from ..stdio_policy import validate_stdio_transport

            try:
                server = self.config.mcp_stdio.servers[0]
                validate_stdio_transport(
                    {"stdio": {"command": server.command, "arguments": list(server.arguments)}},
                    self.config,
                )
            except (SandboxPolicyError, IndexError):
                stdio_enabled = False
        return {
            "enabled": awaited,
            "backend": "internal",
            "internalProcess": awaited,
            "script": {
                "enabled": awaited,
                "runtimes": list(self.config.script_runtimes),
                "externalSource": False,
            },
            "shell": {"enabled": awaited and self.config.allow_shell},
            "container": {"enabled": False},
            "cancellation": True,
            "resourceLimits": {
                "posixRlimit": posix and awaited,
                "windowsJob": os.name == "nt" and awaited,
                "cpu": awaited and self.config.cpu_seconds is not None,
                "memory": awaited and self.config.memory_bytes is not None,
                "fileSize": posix and awaited and self.config.file_size_bytes is not None,
                "processCount": awaited and self.config.process_count is not None,
                "processTree": (
                    "windows_job"
                    if os.name == "nt" and awaited
                    else "posix_process_group"
                    if posix and awaited
                    else "unavailable"
                ),
                "workspaceQuota": "monitored",
                "outputBytes": True,
                "timeout": True,
            },
            "filesystemIsolation": "workspace_cwd_only",
            "networkIsolation": "none",
            "hardIsolation": False,
            "mcpStdio": {
                "enabled": stdio_enabled,
                "duplex": True,
                "externalBackends": False,
                "processTree": "windows_job" if not posix else "posix_process_group",
                "maxFrameBytes": self.config.mcp_stdio.max_frame_bytes,
                "resourceControls": {
                    "windowsJob": not posix,
                    "posixRlimit": posix,
                    "fileSize": posix,
                    "workspaceQuota": "monitored",
                    "inputOutputBytes": True,
                    "wholeDeadline": True,
                },
            },
        }

    def _validate_awaited_controls(self) -> None:
        if not math.isfinite(self.config.timeout_seconds) or self.config.timeout_seconds <= 0:
            raise SandboxPolicyError("sandbox timeout must be finite and positive")
        if os.name == "nt":
            if self.config.file_size_bytes is not None:
                raise SandboxPolicyError("Windows awaited file-size enforcement is unavailable")
            return
        if os.name != "posix":
            raise SandboxPolicyError("awaited sandbox process controls are unavailable")
        import resource

        if not callable(getattr(resource, "setrlimit", None)) or not callable(
            getattr(os, "killpg", None)
        ):
            raise SandboxPolicyError("awaited sandbox process controls are unavailable")
        for name, value in (
            ("RLIMIT_CPU", self.config.cpu_seconds),
            ("RLIMIT_AS", self.config.memory_bytes),
            ("RLIMIT_FSIZE", self.config.file_size_bytes),
            ("RLIMIT_NPROC", self.config.process_count),
        ):
            if value is not None and not hasattr(resource, name):
                raise SandboxPolicyError(
                    "requested awaited process resource control is unavailable"
                )
        if self.config.process_count is not None and getattr(os, "getuid", lambda: 0)() == 0:
            raise SandboxPolicyError("privileged awaited process-count enforcement is unavailable")

    @staticmethod
    def _close_awaited_tree(process: asyncio.subprocess.Process, job: Any) -> None:
        if job is not None:
            job.close()
        elif os.name == "posix":
            killpg = getattr(os, "killpg", None)
            sigkill = getattr(signal, "SIGKILL", None)
            if not callable(killpg) or sigkill is None:
                raise SandboxProcessError("awaited process group control is unavailable")
            try:
                killpg(process.pid, sigkill)
            except ProcessLookupError:
                pass

    async def _wait_awaited_leader(self, process: asyncio.subprocess.Process, job: Any) -> int:
        # Process.wait can wait for inherited pipes as well as the leader on
        # asyncio. Observe the actual exit before closing descendants' pipes.
        while process.returncode is None:
            await asyncio.sleep(0.01)
        code = process.returncode
        self._close_awaited_tree(process, job)
        return code

    async def execute(self, request: SandboxExecutionRequest) -> SandboxExecutionResult:
        if not self.config.enabled or self.config.backend != "internal":
            raise SandboxPolicyError("internal sandbox execution is disabled")
        if self._stdio_closing:
            raise SandboxPolicyError("internal sandbox is shutting down")
        self._validate_awaited_controls()
        if (
            request.execution_id in self._active
            or request.execution_id in self._awaited_starts
            or request.execution_id in self._stdio_starting
            or request.execution_id in self._stdio_sessions
        ):
            raise SandboxPolicyError("sandbox execution identity is already active")
        owner = asyncio.current_task()
        if owner is None:
            raise SandboxPolicyError("sandbox execution ownership is unavailable")
        self._awaited_starts[request.execution_id] = owner
        try:
            return await self._execute_awaited(request)
        finally:
            self._awaited_starts.pop(request.execution_id, None)
            self._awaited_stops.discard(request.execution_id)

    async def _execute_awaited(self, request: SandboxExecutionRequest) -> SandboxExecutionResult:
        self._validate_request_size(request)
        root = Path(self.config.workspace_root)
        root.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="owa-", dir=root) as temporary:
            workspace = Path(temporary)
            executable, arguments = self._prepare_command(request, workspace)
            environment = self._build_environment(request, workspace)
            from ..credential_guard import process_credential_guard

            output_guard = process_credential_guard(request, environment)
            self._check_workspace(workspace)
            process = None
            job: Any = None
            tasks: list[asyncio.Task[Any]] = []
            if os.name == "nt":
                from .._windows_job import WindowsProcessJob

                job = WindowsProcessJob(self.config)
            try:
                remaining = self.config.timeout_seconds - (time.perf_counter() - started)
                if remaining <= 0:
                    raise TimeoutError
                async with asyncio.timeout(remaining):
                    try:
                        process = await asyncio.create_subprocess_exec(
                            executable,
                            *arguments,
                            cwd=workspace,
                            env=environment,
                            stdin=asyncio.subprocess.PIPE,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                            start_new_session=os.name == "posix",
                            close_fds=True,
                            preexec_fn=self._resource_limiter(),
                            creationflags=0x00000004 if os.name == "nt" else 0,
                        )
                    except (OSError, subprocess.SubprocessError) as exc:
                        raise SandboxProcessError(
                            "sandbox process failed to start",
                            details={"execution_id": request.execution_id},
                        ) from exc
                    self._active[request.execution_id] = process
                    if job is not None:
                        self._awaited_jobs[request.execution_id] = job
                        job.assign_and_resume(process.pid)
                    budget = _OutputBudget(self.config.max_output_bytes)
                    tasks = [
                        asyncio.create_task(self._write_stdin(process, request.stdin)),
                        asyncio.create_task(self._read_stream(process.stdout, budget)),
                        asyncio.create_task(self._read_stream(process.stderr, budget)),
                        asyncio.create_task(self._wait_awaited_leader(process, job)),
                        asyncio.create_task(self._monitor_workspace(process, workspace)),
                    ]
                    results = await asyncio.gather(*tasks)
            except TimeoutError as exc:
                raise SandboxTimeoutError(
                    "sandbox execution timed out",
                    details={"timeout_seconds": self.config.timeout_seconds},
                ) from exc
            finally:
                self._active.pop(request.execution_id, None)
                self._awaited_jobs.pop(request.execution_id, None)
                if process is not None:
                    self._close_awaited_tree(process, job)
                    unwinding = sys.exc_info()[0] is not None
                    try:
                        await asyncio.wait_for(self._terminate_process(process), timeout=2)
                    except TimeoutError:
                        if not unwinding:
                            raise SandboxProcessError(
                                "sandbox process cleanup could not be verified"
                            ) from None
                    finally:
                        await self._cancel_tasks(tasks)
                        transport = getattr(process, "_transport", None)
                        if transport is not None:
                            transport.close()
                elif job is not None:
                    job.close()
            stdout = output_guard.text(cast(str, results[1]))
            stderr = output_guard.text(cast(str, results[2]))
            exit_code = cast(int, results[3])
            self._check_workspace(workspace)
            if request.execution_id in self._awaited_stops:
                raise SandboxProcessError(
                    "sandbox execution was stopped before completion",
                    details={"exit_code": exit_code},
                )
            if exit_code != 0:
                if exit_code < 0 and self._resource_signal(-exit_code):
                    raise SandboxResourceLimitError(
                        "sandbox process was terminated by a resource limit",
                        details={"exit_code": exit_code},
                    )
                raise SandboxProcessError(
                    "sandbox process exited with a non-zero status",
                    details={"exit_code": exit_code},
                )
            return SandboxExecutionResult(
                execution_id=request.execution_id,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                duration=time.perf_counter() - started,
            )

    async def cancel(self, execution_id: str) -> None:
        awaited = self._awaited_starts.get(execution_id)
        if awaited is not None and execution_id not in self._active:
            if awaited is not asyncio.current_task():
                awaited.cancel()
                await asyncio.gather(awaited, return_exceptions=True)
        starting = self._stdio_starts.get(execution_id)
        if starting is not None and starting is not asyncio.current_task():
            starting.cancel()
            await asyncio.gather(starting, return_exceptions=True)
        session = self._stdio_sessions.get(execution_id)
        if session is not None:
            await session.aclose()
        process = self._active.get(execution_id)
        if process is not None:
            if process.returncode is None:
                self._awaited_stops.add(execution_id)
            await self._stop_awaited_process(process, self._awaited_jobs.get(execution_id))

    async def _stop_awaited_process(self, process: asyncio.subprocess.Process, job: Any) -> None:
        if job is not None:
            job.close()
        await self._terminate_process(process)
        self._close_awaited_tree(process, job)

    async def shutdown(self) -> None:
        self._stdio_closing = True
        self._awaited_stops.update(
            identifier for identifier, process in self._active.items() if process.returncode is None
        )
        awaited = tuple(
            owner
            for identifier, owner in self._awaited_starts.items()
            if identifier not in self._active and owner is not asyncio.current_task()
        )
        for owner in awaited:
            owner.cancel()
        await asyncio.gather(*awaited, return_exceptions=True)
        starting = tuple(
            task for task in self._stdio_starts.values() if task is not asyncio.current_task()
        )
        for task in starting:
            task.cancel()
        await asyncio.gather(*starting, return_exceptions=True)
        await asyncio.gather(
            *(session.aclose() for session in tuple(self._stdio_sessions.values())),
            return_exceptions=True,
        )
        self._stdio_sessions.clear()
        await asyncio.gather(
            *(
                self._stop_awaited_process(process, self._awaited_jobs.get(identifier))
                for identifier, process in tuple(self._active.items())
            ),
            return_exceptions=True,
        )
        self._active.clear()
        self._awaited_jobs.clear()

    def _validate_request_size(self, request: SandboxExecutionRequest) -> None:
        values = [
            request.script_code or "",
            request.stdin or "",
            request.command or "",
            request.image or "",
        ]
        values.extend(request.arguments)
        for key, value in request.environment:
            values.append(key)
            if isinstance(value, str):
                values.append(value)
        total = sum(len(value.encode("utf-8")) for value in values)
        if total > self.config.max_input_bytes:
            raise SandboxPolicyError(
                "sandbox input limit exceeded",
                details={"max_input_bytes": self.config.max_input_bytes},
            )

    def validate_background_request(self, request: SandboxExecutionRequest) -> None:
        """Validate a transformed request without workspace, secret reads or launch."""
        if not self.config.enabled or request.kind not in {"script", "shell"}:
            raise SandboxPolicyError("background internal operation is unavailable")
        self._validate_request_size(request)
        if request.kind == "script":
            if (
                request.script_language != "python"
                or request.script_language not in self.config.script_runtimes
                or not isinstance(request.script_code, str)
            ):
                raise SandboxPolicyError("only enabled inline Python jobs are supported")
        else:
            # Shell preparation only resolves the explicitly approved search
            # path; unlike script preparation it creates no workspace file.
            self._prepare_command(request, Path())
        for name, value in request.environment:
            if (
                is_reserved_environment_name(name)
                or not isinstance(value, SandboxSecretReference)
                or value.name not in self.config.secret_environment
            ):
                raise SandboxPolicyError("background environment reference is not approved")

    def _prepare_command(
        self, request: SandboxExecutionRequest, workspace: Path
    ) -> tuple[str, tuple[str, ...]]:
        if request.kind == "container":
            raise SandboxPolicyError("internal sandbox backend does not support containers")
        if request.kind == "script":
            if request.script_language not in self.config.script_runtimes:
                raise SandboxPolicyError(
                    "sandbox script runtime is not enabled",
                    details={"runtime": request.script_language},
                )
            if request.script_language != "python" or request.script_code is None:
                raise SandboxPolicyError("only inline Python scripts are supported")
            path = workspace / "main.py"
            path.write_text(request.script_code, encoding="utf-8")
            return sys.executable, (str(path), *request.arguments)
        if not self.config.allow_shell:
            raise SandboxPolicyError("sandbox shell execution is disabled")
        command = request.command or ""
        if not command or "/" in command or (os.altsep and os.altsep in command):
            raise SandboxPolicyError("shell command must be a direct executable name")
        executable = shutil.which(command, path=self._effective_search_path())
        if executable is None:
            raise SandboxPolicyError(
                "shell executable is not available in the approved search path",
                details={"command": command},
            )
        return executable, request.arguments

    def _build_environment(
        self, request: SandboxExecutionRequest, workspace: Path
    ) -> dict[str, str]:
        environment = {
            "PATH": self._effective_search_path(),
            "HOME": str(workspace),
            "TMPDIR": str(workspace),
            "LANG": "C.UTF-8",
        }
        for name in self.config.inherited_environment:
            inherited_value = os.getenv(name)
            if inherited_value is not None:
                environment[name] = inherited_value
        # Python and Windows native tools prefer TEMP/TMP over TMPDIR. Bind
        # all standard temporary-directory variables back to this execution's
        # workspace after deployment allowlisted values have been applied.
        environment.update({name: str(workspace) for name in WORKSPACE_TEMPORARY_ENVIRONMENT})
        for name, configured_value in request.environment:
            if is_reserved_environment_name(name):
                raise SandboxPolicyError(
                    "sandbox environment cannot override runtime isolation variables",
                    details={"name": name},
                )
            if isinstance(configured_value, SandboxSecretReference):
                if configured_value.name not in self.config.secret_environment:
                    raise SandboxPolicyError(
                        "sandbox secret reference is not deployment-approved",
                        details={"name": configured_value.name},
                    )
                resolved = os.getenv(configured_value.name)
                if resolved is None:
                    raise SandboxPolicyError(
                        "sandbox secret reference is unavailable",
                        details={"name": configured_value.name},
                    )
                environment[name] = resolved
            else:
                environment[name] = configured_value
        if request.kind == "script":
            # The admitted Python source/input are UTF-8 on every host. This
            # controlled interpreter binding overrides a requested host codec;
            # no ambient locale/environment is inherited for encoding identity.
            environment["PYTHONIOENCODING"] = "utf-8:strict"
        total = sum(
            len(key.encode()) + len(environment_value.encode())
            for key, environment_value in environment.items()
        )
        if total > self.config.max_input_bytes:
            raise SandboxPolicyError(
                "sandbox environment limit exceeded",
                details={"max_input_bytes": self.config.max_input_bytes},
            )
        return environment

    def _effective_search_path(self) -> str:
        values = [
            str(Path(sys.executable).parent),
            *self.config.executable_search_path.split(os.pathsep),
        ]
        return os.pathsep.join(dict.fromkeys(value for value in values if value))

    async def _write_stdin(self, process: asyncio.subprocess.Process, value: str | None) -> None:
        if process.stdin is None:
            return
        try:
            if value is not None:
                process.stdin.write(value.encode("utf-8"))
                await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            process.stdin.close()
            try:
                await process.stdin.wait_closed()
            except (BrokenPipeError, ConnectionResetError):
                pass

    @staticmethod
    async def _read_stream(stream: asyncio.StreamReader | None, budget: _OutputBudget) -> str:
        if stream is None:
            return ""
        chunks: list[bytes] = []
        while True:
            chunk = await stream.read(65_536)
            if not chunk:
                break
            budget.add(len(chunk))
            chunks.append(chunk)
        # Keep the observable sandbox contract independent of the host's text
        # mode: Windows child processes commonly emit CRLF while POSIX
        # processes emit LF.
        return b"".join(chunks).decode("utf-8", errors="replace").replace("\r\n", "\n")

    async def _monitor_workspace(
        self, process: asyncio.subprocess.Process, workspace: Path
    ) -> None:
        while process.returncode is None:
            self._check_workspace(workspace)
            await asyncio.sleep(0.05)
        self._check_workspace(workspace)

    def _check_workspace(self, workspace: Path) -> None:
        total = 0
        for root, _, files in os.walk(workspace, followlinks=False):
            for filename in files:
                path = Path(root) / filename
                try:
                    if path.is_symlink():
                        continue
                    total += path.stat().st_size
                except FileNotFoundError:
                    continue
                if total > self.config.max_workspace_bytes:
                    raise SandboxResourceLimitError(
                        "sandbox workspace limit exceeded",
                        details={"max_workspace_bytes": self.config.max_workspace_bytes},
                    )

    def _resource_limiter(self) -> Any:
        if os.name != "posix":
            return None

        def apply_limits() -> None:
            import resource

            setrlimit = getattr(resource, "setrlimit", None)
            if not callable(setrlimit):
                return
            limits: list[tuple[Any, int | None]] = []
            for name, value in (
                ("RLIMIT_CPU", self.config.cpu_seconds),
                ("RLIMIT_AS", self.config.memory_bytes),
                ("RLIMIT_FSIZE", self.config.file_size_bytes),
                ("RLIMIT_NPROC", self.config.process_count),
            ):
                resource_id = getattr(resource, name, None)
                if resource_id is not None:
                    limits.append((resource_id, value))
            for resource_id, value in limits:
                if value is not None:
                    setrlimit(resource_id, (value, value))

        return apply_limits

    @staticmethod
    def _resource_signal(number: int) -> bool:
        names = {
            value
            for value in (
                getattr(signal, "SIGKILL", None),
                getattr(signal, "SIGXCPU", None),
                getattr(signal, "SIGXFSZ", None),
            )
            if value is not None
        }
        return number in names

    @staticmethod
    async def _cancel_tasks(tasks: list[asyncio.Task[Any]]) -> None:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    async def _terminate_process(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            killpg = getattr(os, "killpg", None)
            sigterm = getattr(signal, "SIGTERM", None)
            if os.name == "posix" and callable(killpg) and sigterm is not None:
                killpg(process.pid, sigterm)
            else:
                process.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(process.wait(), timeout=0.5)
            return
        except TimeoutError:
            pass
        try:
            killpg = getattr(os, "killpg", None)
            sigkill = getattr(signal, "SIGKILL", None)
            if os.name == "posix" and callable(killpg) and sigkill is not None:
                killpg(process.pid, sigkill)
            else:
                process.kill()
        except ProcessLookupError:
            return
        await process.wait()
