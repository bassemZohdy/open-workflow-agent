"""Reusable internal sandbox backend: controlled child-process execution.

Shared utility consumed by every engine through SandboxManager. This module
owns all OS-level machinery (environment filtering, rlimits, process-tree
termination, workspace monitoring) so engines never build their own execution
paths.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
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
    RESERVED_ENVIRONMENT,
    SandboxExecutionRequest,
    SandboxExecutionResult,
    SandboxSecretReference,
)


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

    def capabilities(self) -> dict[str, Any]:
        posix = os.name == "posix"
        enabled = self.config.enabled and self.config.backend == "internal"
        return {
            "enabled": enabled,
            "backend": "internal",
            "internalProcess": enabled,
            "script": {
                "enabled": enabled,
                "runtimes": list(self.config.script_runtimes),
                "externalSource": False,
            },
            "shell": {"enabled": enabled and self.config.allow_shell},
            "container": {"enabled": False},
            "cancellation": True,
            "resourceLimits": {
                "posixRlimit": posix,
                "workspaceQuota": "monitored",
                "outputBytes": True,
                "timeout": True,
            },
            "filesystemIsolation": "workspace_cwd_only",
            "networkIsolation": "none",
            "hardIsolation": False,
        }

    async def execute(self, request: SandboxExecutionRequest) -> SandboxExecutionResult:
        if not self.config.enabled or self.config.backend != "internal":
            raise SandboxPolicyError("internal sandbox execution is disabled")
        self._validate_request_size(request)
        root = Path(self.config.workspace_root)
        root.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="owa-", dir=root) as temporary:
            workspace = Path(temporary)
            executable, arguments = self._prepare_command(request, workspace)
            environment = self._build_environment(request, workspace)
            self._check_workspace(workspace)
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
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise SandboxProcessError(
                    "sandbox process failed to start",
                    details={"execution_id": request.execution_id},
                ) from exc
            self._active[request.execution_id] = process
            budget = _OutputBudget(self.config.max_output_bytes)
            tasks: list[asyncio.Task[Any]] = [
                asyncio.create_task(self._write_stdin(process, request.stdin)),
                asyncio.create_task(self._read_stream(process.stdout, budget)),
                asyncio.create_task(self._read_stream(process.stderr, budget)),
                asyncio.create_task(process.wait()),
                asyncio.create_task(self._monitor_workspace(process, workspace)),
            ]
            try:
                async with asyncio.timeout(self.config.timeout_seconds):
                    results = await asyncio.gather(*tasks)
            except TimeoutError as exc:
                await self._terminate_process(process)
                await self._cancel_tasks(tasks)
                raise SandboxTimeoutError(
                    "sandbox execution timed out",
                    details={"timeout_seconds": self.config.timeout_seconds},
                ) from exc
            except asyncio.CancelledError:
                await self._terminate_process(process)
                await self._cancel_tasks(tasks)
                raise
            except Exception:
                await self._terminate_process(process)
                await self._cancel_tasks(tasks)
                raise
            finally:
                self._active.pop(request.execution_id, None)
            stdout = cast(str, results[1])
            stderr = cast(str, results[2])
            exit_code = cast(int, results[3])
            self._check_workspace(workspace)
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
        process = self._active.get(execution_id)
        if process is not None:
            await self._terminate_process(process)

    async def shutdown(self) -> None:
        processes = list(self._active.values())
        await asyncio.gather(
            *(self._terminate_process(process) for process in processes),
            return_exceptions=True,
        )
        self._active.clear()

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
        for name, configured_value in request.environment:
            if name in RESERVED_ENVIRONMENT:
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
