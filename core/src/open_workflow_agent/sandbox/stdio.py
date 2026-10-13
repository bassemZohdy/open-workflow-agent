"""Bounded duplex bytes owned by the common internal process backend."""

from __future__ import annotations

import asyncio
import math
import os
import signal
import tempfile
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import anyio

from ..errors import (
    SandboxOutputLimitError,
    SandboxPolicyError,
    SandboxProcessError,
    SandboxTimeoutError,
)
from .contract import SandboxExecutionRequest, SandboxSecretReference, SandboxStdioRequest
from .stdio_policy import validate_stdio_transport

if TYPE_CHECKING:
    from ._windows_job import WindowsProcessJob
    from .backends.internal import InternalSandboxBackend


class _StdioSession:
    def __init__(
        self,
        backend: InternalSandboxBackend,
        process: asyncio.subprocess.Process,
        workspace: Path,
        deadline: float,
        job: WindowsProcessJob | None,
    ) -> None:
        self.backend, self.process, self.workspace = backend, process, workspace
        self.deadline, self.job = deadline, job
        self.frames: asyncio.Queue[bytes] = asyncio.Queue(
            backend.config.mcp_stdio.max_pending_frames
        )
        self.failure: asyncio.Future[BaseException] = asyncio.get_running_loop().create_future()
        self.input_bytes, self.output_bytes = 0, 0
        self.closed = False
        self.send_lock, self.close_lock = asyncio.Lock(), asyncio.Lock()
        self.tasks = [
            asyncio.create_task(self._supervise(self._read_stdout())),
            asyncio.create_task(self._supervise(self._read_stderr())),
            asyncio.create_task(self._supervise(backend._monitor_workspace(process, workspace))),
            asyncio.create_task(self._supervise(self._watch_deadline())),
        ]

    def _remaining(self) -> float:
        remaining = self.deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise SandboxTimeoutError("MCP stdio operation deadline expired")
        return remaining

    async def _supervise(self, operation: Awaitable[Any]) -> None:
        try:
            await operation
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            if not self.failure.done():
                self.failure.set_result(SandboxTimeoutError("MCP stdio operation deadline expired"))
        except BaseException as error:
            if not self.failure.done():
                self.failure.set_result(error)

    async def _watch_deadline(self) -> None:
        await asyncio.sleep(self._remaining())
        if not self.closed:
            if not self.failure.done():
                self.failure.set_result(SandboxTimeoutError("MCP stdio operation deadline expired"))
            await self.aclose()

    def _output(self, size: int) -> None:
        self.output_bytes += size
        if self.output_bytes > self.backend.config.max_output_bytes:
            raise SandboxOutputLimitError("MCP stdio output limit exceeded")

    async def _read_stdout(self) -> None:
        stream = self.process.stdout
        assert stream is not None
        buffered = bytearray()
        while not self.closed:
            try:
                async with asyncio.timeout(self._remaining()):
                    chunk = await stream.read(
                        min(65_536, self.backend.config.mcp_stdio.max_frame_bytes + 1)
                    )
            except TimeoutError:
                raise SandboxTimeoutError("MCP stdio operation deadline expired") from None
            if not chunk:
                raise SandboxProcessError("MCP stdio stream ended without a complete response")
            self._output(len(chunk))
            buffered.extend(chunk)
            while (newline := buffered.find(b"\n")) != -1:
                if newline > self.backend.config.mcp_stdio.max_frame_bytes:
                    raise SandboxOutputLimitError("MCP stdio frame limit exceeded")
                value = bytes(buffered[:newline])
                del buffered[: newline + 1]
                try:
                    value.decode("utf-8", errors="strict")
                except UnicodeError:
                    raise SandboxProcessError("MCP stdio frame is not valid UTF-8") from None
                async with asyncio.timeout(self._remaining()):
                    await self.frames.put(value)
            if len(buffered) > self.backend.config.mcp_stdio.max_frame_bytes:
                raise SandboxOutputLimitError("MCP stdio frame limit exceeded")

    async def _read_stderr(self) -> None:
        stream = self.process.stderr
        assert stream is not None
        while value := await stream.read(65_536):
            # Diagnostic stderr is never retained or exposed as protocol output.
            self._output(len(value))

    async def send_frame(self, value: bytes) -> None:
        if self.closed:
            raise SandboxProcessError("MCP stdio stream is closed")
        if self.failure.done():
            raise self.failure.result()
        if (
            len(value) > self.backend.config.mcp_stdio.max_frame_bytes
            or b"\n" in value
            or b"\r" in value
        ):
            raise SandboxOutputLimitError("MCP stdio outgoing frame is invalid or oversized")
        try:
            value.decode("utf-8", errors="strict")
        except UnicodeError:
            raise SandboxPolicyError("MCP stdio outgoing frame is not valid UTF-8") from None
        try:
            async with asyncio.timeout(self._remaining()), self.send_lock:
                self.input_bytes += len(value) + 1
                if self.input_bytes > self.backend.config.max_input_bytes:
                    raise SandboxPolicyError("MCP stdio input limit exceeded")
                assert self.process.stdin is not None
                self.process.stdin.write(value + b"\n")
                await self.process.stdin.drain()
        except TimeoutError:
            raise SandboxTimeoutError("MCP stdio operation deadline expired") from None
        except (BrokenPipeError, ConnectionResetError):
            if self.failure.done():
                raise self.failure.result() from None
            raise SandboxProcessError("MCP stdio stream closed during dispatch") from None

    async def receive_frame(self) -> bytes:
        if self.failure.done():
            raise self.failure.result()
        if self.closed:
            raise SandboxProcessError("MCP stdio stream is closed")
        receive = asyncio.create_task(self.frames.get())
        try:
            async with asyncio.timeout(self._remaining()):
                await asyncio.wait((receive, self.failure), return_when=asyncio.FIRST_COMPLETED)
                if self.failure.done():
                    raise self.failure.result()
                return receive.result()
        except TimeoutError:
            raise SandboxTimeoutError("MCP stdio operation deadline expired") from None
        finally:
            receive.cancel()
            await asyncio.gather(receive, return_exceptions=True)

    async def aclose(self) -> None:
        async with self.close_lock:
            if self.closed:
                return
            self.closed = True
            if not self.failure.done():
                self.failure.set_result(SandboxProcessError("MCP stdio stream is closed"))
            current = asyncio.current_task()
            for task in self.tasks:
                if task is not current:
                    task.cancel()
            if self.process.stdin is not None:
                self.process.stdin.close()
            if self.job is not None:
                self.job.close()
            else:
                # Kill the process group even if its leader already exited.
                killpg = getattr(os, "killpg", None)
                kill_signal = getattr(signal, "SIGKILL", None)
                if not callable(killpg) or kill_signal is None:
                    raise SandboxProcessError("MCP stdio process group controls are unavailable")
                for number in (signal.SIGTERM, kill_signal):
                    try:
                        killpg(self.process.pid, number)
                    except ProcessLookupError:
                        break
                    if number == signal.SIGTERM:
                        try:
                            await asyncio.wait_for(self.process.wait(), timeout=0.5)
                        except TimeoutError:
                            pass
            try:
                await asyncio.wait_for(self.process.wait(), timeout=2)
            except TimeoutError:
                self.process.kill()
                await asyncio.wait_for(self.process.wait(), timeout=2)
            await asyncio.gather(
                *(task for task in self.tasks if task is not current), return_exceptions=True
            )


@asynccontextmanager
async def open_internal_stdio(
    backend: InternalSandboxBackend, request: SandboxStdioRequest
) -> AsyncIterator[_StdioSession]:
    transport: dict[str, Any] = {
        "stdio": {"command": request.command, "arguments": list(request.arguments)}
    }
    if request.environment is not None:
        transport["stdio"]["environment"] = dict(request.environment)
    server = validate_stdio_transport(transport, backend.config)
    if backend._stdio_closing:
        raise SandboxPolicyError("MCP stdio sandbox is shutting down")
    if not math.isfinite(request.timeout_seconds) or request.timeout_seconds <= 0:
        raise SandboxPolicyError("MCP stdio operation timeout is invalid")
    if (
        request.execution_id in backend._active
        or request.execution_id in backend._stdio_sessions
        or request.execution_id in backend._stdio_starting
    ):
        raise SandboxPolicyError("MCP stdio execution identity is already active")
    if (
        len(backend._stdio_sessions) + len(backend._stdio_starting)
        >= backend.config.mcp_stdio.max_sessions
    ):
        raise SandboxPolicyError("MCP stdio concurrent session limit exceeded")
    if not Path(server.command).is_file():
        raise SandboxPolicyError("MCP stdio deployment executable is unavailable")
    environment: tuple[tuple[str, str | SandboxSecretReference], ...] = (
        *tuple(server.environment.items()),
        *(
            (name, SandboxSecretReference(reference))
            for name, reference in server.secret_environment.items()
        ),
    )
    binding = SandboxExecutionRequest(
        request.execution_id,
        "shell",
        command=server.command,
        arguments=request.arguments,
        environment=environment,
    )
    backend._validate_request_size(binding)
    root = Path(backend.config.workspace_root)
    root.mkdir(parents=True, exist_ok=True)
    deadline = asyncio.get_running_loop().time() + min(
        request.timeout_seconds, backend.config.timeout_seconds
    )
    with tempfile.TemporaryDirectory(prefix="owa-stdio-", dir=root) as directory:
        workspace = Path(directory)
        child_environment = backend._build_environment(binding, workspace)
        backend._check_workspace(workspace)
        job = None
        if os.name == "nt":
            from ._windows_job import WindowsProcessJob

            job = WindowsProcessJob(backend.config)
        process = None
        session = None
        backend._stdio_starting.add(request.execution_id)
        owner = asyncio.current_task()
        assert owner is not None
        backend._stdio_starts[request.execution_id] = owner
        try:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise SandboxTimeoutError("MCP stdio operation deadline expired")
            async with asyncio.timeout(remaining):
                process = await asyncio.create_subprocess_exec(
                    server.command,
                    *request.arguments,
                    cwd=workspace,
                    env=child_environment,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    start_new_session=os.name == "posix",
                    close_fds=True,
                    preexec_fn=backend._resource_limiter(),
                    creationflags=0x00000004 if os.name == "nt" else 0,  # CREATE_SUSPENDED
                    limit=backend.config.mcp_stdio.max_frame_bytes + 1,
                )
            if job is not None:
                job.assign_and_resume(process.pid)
            session = _StdioSession(backend, process, workspace, deadline, job)
            backend._stdio_sessions[request.execution_id] = session
            backend._stdio_starting.discard(request.execution_id)
            backend._stdio_starts.pop(request.execution_id, None)
            if backend._stdio_closing:
                raise SandboxPolicyError("MCP stdio sandbox is shutting down")
            yield session
        except OSError:
            raise SandboxProcessError("MCP stdio process could not be started") from None
        except TimeoutError:
            raise SandboxTimeoutError("MCP stdio operation deadline expired") from None
        finally:
            with anyio.CancelScope(shield=True):
                if session is not None:
                    await session.aclose()
                else:
                    if job is not None:
                        job.close()
                    if process is not None:
                        await backend._terminate_process(process)
                backend._stdio_sessions.pop(request.execution_id, None)
                backend._stdio_starting.discard(request.execution_id)
                backend._stdio_starts.pop(request.execution_id, None)
