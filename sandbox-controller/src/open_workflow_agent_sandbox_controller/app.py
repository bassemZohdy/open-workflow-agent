"""Restricted Docker controller with a deliberately tiny execution API."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_IMAGE_DIGEST = re.compile(r"^.+@sha256:[0-9a-fA-F]{64}$")
_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ControllerConfig(StrictModel):
    docker_socket: str = "/var/run/docker.sock"
    docker_binary: str = "/usr/local/bin/docker"
    allowed_images: list[str] = Field(default_factory=list)
    run_as_user: str = "65532:65532"
    max_timeout_seconds: float = 60.0
    max_input_bytes: int = 1_048_576
    max_output_bytes: int = 1_048_576
    max_workspace_bytes: int = 33_554_432
    max_memory_bytes: int = 536_870_912
    max_process_count: int = 64

    @classmethod
    def from_environment(cls) -> ControllerConfig:
        allowed = [
            item.strip()
            for item in os.getenv("OWA_SANDBOX_CONTROLLER_ALLOWED_IMAGES", "").split(",")
            if item.strip()
        ]
        return cls(
            docker_socket=os.getenv(
                "OWA_SANDBOX_CONTROLLER_DOCKER_SOCKET", "/var/run/docker.sock"
            ),
            docker_binary=os.getenv(
                "OWA_SANDBOX_CONTROLLER_DOCKER_BINARY", "/usr/local/bin/docker"
            ),
            allowed_images=allowed,
            run_as_user=os.getenv("OWA_SANDBOX_CONTROLLER_RUN_AS_USER", "65532:65532"),
            max_timeout_seconds=float(
                os.getenv("OWA_SANDBOX_CONTROLLER_MAX_TIMEOUT_SECONDS", "60")
            ),
            max_input_bytes=int(
                os.getenv("OWA_SANDBOX_CONTROLLER_MAX_INPUT_BYTES", "1048576")
            ),
            max_output_bytes=int(
                os.getenv("OWA_SANDBOX_CONTROLLER_MAX_OUTPUT_BYTES", "1048576")
            ),
            max_workspace_bytes=int(
                os.getenv("OWA_SANDBOX_CONTROLLER_MAX_WORKSPACE_BYTES", "33554432")
            ),
            max_memory_bytes=int(
                os.getenv("OWA_SANDBOX_CONTROLLER_MAX_MEMORY_BYTES", "536870912")
            ),
            max_process_count=int(
                os.getenv("OWA_SANDBOX_CONTROLLER_MAX_PROCESS_COUNT", "64")
            ),
        )

    @field_validator("docker_socket", "docker_binary")
    @classmethod
    def validate_absolute_path(cls, value: str) -> str:
        path = Path(value)
        if not path.is_absolute():
            raise ValueError("Docker controller paths must be absolute")
        return str(path)

    @field_validator("allowed_images")
    @classmethod
    def validate_images(cls, value: list[str]) -> list[str]:
        images = [image.strip() for image in value]
        if len(set(images)) != len(images):
            raise ValueError("controller allowed_images must not contain duplicates")
        if any(not _IMAGE_DIGEST.fullmatch(image) for image in images):
            raise ValueError("controller images must use immutable sha256 digests")
        return images

    @field_validator("run_as_user")
    @classmethod
    def validate_user(cls, value: str) -> str:
        if not re.fullmatch(r"[1-9][0-9]*(?::[0-9]+)?", value):
            raise ValueError("controller run_as_user must be a non-root numeric uid[:gid]")
        return value

    @field_validator(
        "max_timeout_seconds",
        "max_input_bytes",
        "max_output_bytes",
        "max_workspace_bytes",
        "max_memory_bytes",
        "max_process_count",
    )
    @classmethod
    def validate_positive_limit(cls, value: float | int) -> float | int:
        if value <= 0:
            raise ValueError("controller limits must be greater than zero")
        return value


class ExecutionLimits(StrictModel):
    timeout_seconds: float
    max_output_bytes: int
    max_workspace_bytes: int
    memory_bytes: int | None = None
    process_count: int | None = None


class IsolationPolicy(StrictModel):
    run_as_user: str
    network: str
    read_only_root: bool
    drop_all_capabilities: bool
    no_new_privileges: bool
    host_mounts: bool
    host_network: bool


class ExecutionRequest(StrictModel):
    execution_id: str
    image: str
    command: str | None = None
    arguments: list[str] = Field(default_factory=list)
    stdin: str | None = None
    environment: dict[str, str] = Field(default_factory=dict)
    limits: ExecutionLimits
    isolation: IsolationPolicy

    @field_validator("execution_id")
    @classmethod
    def validate_execution_id(cls, value: str) -> str:
        if not value or len(value) > 512:
            raise ValueError("execution_id must contain between 1 and 512 characters")
        return value

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not _ENVIRONMENT_NAME.fullmatch(name) for name in value):
            raise ValueError("container environment names must be valid")
        return value

    @model_validator(mode="after")
    def validate_command(self) -> ExecutionRequest:
        if self.command is not None and not self.command.strip():
            raise ValueError("container command cannot be empty")
        return self


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    exit_code: int
    stdout: str
    stderr: str
    duration: float


class ControllerFailure(Exception):
    def __init__(self, code: str, message: str, status_code: int = 500) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class ExecutionRunner(Protocol):
    async def ready(self) -> bool: ...

    async def execute(self, request: ExecutionRequest) -> ExecutionResult: ...

    async def cancel(self, execution_id: str) -> None: ...

    async def shutdown(self) -> None: ...


@dataclass(slots=True)
class _ActiveExecution:
    process: asyncio.subprocess.Process
    container_name: str


class _OutputBudget:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0

    def add(self, amount: int) -> None:
        self.used += amount
        if self.used > self.limit:
            raise ControllerFailure(
                "sandbox_output_limit",
                "sandbox output limit exceeded",
            )


class DockerCliRunner:
    """Use fixed Docker CLI argv through the controller-owned daemon socket."""

    def __init__(self, config: ControllerConfig) -> None:
        self.config = config
        self._active: dict[str, _ActiveExecution] = {}

    async def ready(self) -> bool:
        return Path(self.config.docker_socket).is_socket() and Path(
            self.config.docker_binary
        ).is_file()

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        self._validate_request(request)
        container_name = self._container_name(request.execution_id)
        environment_file = self._write_environment_file(request.environment)
        started = time.perf_counter()
        process: asyncio.subprocess.Process | None = None
        tasks: list[asyncio.Task[Any]] = []
        try:
            argv = self._docker_run_argv(request, container_name, environment_file)
            process = await asyncio.create_subprocess_exec(
                *argv,
                env=self._docker_environment(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                close_fds=True,
            )
            self._active[request.execution_id] = _ActiveExecution(process, container_name)
            budget = _OutputBudget(request.limits.max_output_bytes)
            tasks = [
                asyncio.create_task(self._write_stdin(process, request.stdin)),
                asyncio.create_task(self._read_stream(process.stdout, budget)),
                asyncio.create_task(self._read_stream(process.stderr, budget)),
                asyncio.create_task(process.wait()),
            ]
            try:
                async with asyncio.timeout(request.limits.timeout_seconds):
                    results = await asyncio.gather(*tasks)
            except TimeoutError as exc:
                await self._force_remove(container_name)
                await self._terminate_cli(process)
                await self._cancel_tasks(tasks)
                raise ControllerFailure(
                    "sandbox_timeout",
                    "sandbox execution timed out",
                ) from exc
            except asyncio.CancelledError:
                await self._force_remove(container_name)
                await self._terminate_cli(process)
                await self._cancel_tasks(tasks)
                raise
            except ControllerFailure:
                await self._force_remove(container_name)
                await self._terminate_cli(process)
                await self._cancel_tasks(tasks)
                raise
            stdout = str(results[1])
            stderr = str(results[2])
            exit_code = int(results[3])
            if exit_code != 0:
                raise ControllerFailure(
                    "sandbox_process_error",
                    "sandbox container exited with a non-zero status",
                )
            return ExecutionResult(
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                duration=time.perf_counter() - started,
            )
        except OSError as exc:
            raise ControllerFailure(
                "sandbox_process_error",
                "Docker CLI failed to start",
            ) from exc
        finally:
            self._active.pop(request.execution_id, None)
            if process is not None and process.returncode is None:
                await self._force_remove(container_name)
                await self._terminate_cli(process)
            if tasks:
                await self._cancel_tasks(tasks)
            environment_file.unlink(missing_ok=True)

    async def cancel(self, execution_id: str) -> None:
        active = self._active.get(execution_id)
        if active is None:
            return
        await self._force_remove(active.container_name)
        await self._terminate_cli(active.process)

    async def shutdown(self) -> None:
        active = tuple(self._active.items())
        for execution_id, execution in active:
            try:
                await self._force_remove(execution.container_name)
                await self._terminate_cli(execution.process)
            finally:
                self._active.pop(execution_id, None)

    def _validate_request(self, request: ExecutionRequest) -> None:
        if request.image not in self.config.allowed_images:
            raise ControllerFailure(
                "sandbox_policy_error",
                "container image is not controller-approved",
                422,
            )
        serialized = json.dumps(request.model_dump(), separators=(",", ":")).encode()
        if len(serialized) > self.config.max_input_bytes:
            raise ControllerFailure(
                "sandbox_policy_error",
                "sandbox input limit exceeded",
                422,
            )
        limits = request.limits
        if limits.timeout_seconds <= 0 or limits.timeout_seconds > self.config.max_timeout_seconds:
            self._limit_error("timeout_seconds")
        if limits.max_output_bytes <= 0 or limits.max_output_bytes > self.config.max_output_bytes:
            self._limit_error("max_output_bytes")
        if (
            limits.max_workspace_bytes <= 0
            or limits.max_workspace_bytes > self.config.max_workspace_bytes
        ):
            self._limit_error("max_workspace_bytes")
        if limits.memory_bytes is not None and (
            limits.memory_bytes <= 0 or limits.memory_bytes > self.config.max_memory_bytes
        ):
            self._limit_error("memory_bytes")
        if limits.process_count is not None and (
            limits.process_count <= 0 or limits.process_count > self.config.max_process_count
        ):
            self._limit_error("process_count")
        isolation = request.isolation
        if isolation.run_as_user != self.config.run_as_user:
            self._policy_error("run_as_user")
        if isolation.network != "denied":
            self._policy_error("network")
        if not isolation.read_only_root:
            self._policy_error("read_only_root")
        if not isolation.drop_all_capabilities:
            self._policy_error("drop_all_capabilities")
        if not isolation.no_new_privileges:
            self._policy_error("no_new_privileges")
        if isolation.host_mounts:
            self._policy_error("host_mounts")
        if isolation.host_network:
            self._policy_error("host_network")

    def _docker_run_argv(
        self,
        request: ExecutionRequest,
        container_name: str,
        environment_file: Path,
    ) -> tuple[str, ...]:
        argv = [
            self.config.docker_binary,
            "run",
            "--rm",
            "--pull=never",
            "--name",
            container_name,
            "--read-only",
            "--network",
            "none",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            self.config.run_as_user,
            "--workdir",
            "/workspace",
            "--tmpfs",
            f"/tmp:rw,noexec,nosuid,nodev,size={request.limits.max_workspace_bytes}",
            "--tmpfs",
            f"/workspace:rw,noexec,nosuid,nodev,size={request.limits.max_workspace_bytes}",
            "--env-file",
            str(environment_file),
        ]
        if request.limits.memory_bytes is not None:
            argv.extend(["--memory", str(request.limits.memory_bytes)])
        if request.limits.process_count is not None:
            argv.extend(["--pids-limit", str(request.limits.process_count)])
        argv.append(request.image)
        if request.command is not None:
            argv.append(request.command)
        argv.extend(request.arguments)
        return tuple(argv)

    def _write_environment_file(self, environment: dict[str, str]) -> Path:
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix="owa-sandbox-env-",
            delete=False,
        )
        try:
            os.chmod(handle.name, 0o600)
            for name, value in environment.items():
                if "\n" in value or "\r" in value:
                    raise ControllerFailure(
                        "sandbox_policy_error",
                        "container environment values cannot contain newlines",
                        422,
                    )
                handle.write(f"{name}={value}\n")
        finally:
            handle.close()
        return Path(handle.name)

    def _docker_environment(self) -> dict[str, str]:
        return {
            "DOCKER_HOST": f"unix://{self.config.docker_socket}",
            "HOME": "/tmp",
            "PATH": "/usr/local/bin:/usr/bin:/bin",
        }

    async def _force_remove(self, container_name: str) -> None:
        try:
            process = await asyncio.create_subprocess_exec(
                self.config.docker_binary,
                "rm",
                "--force",
                container_name,
                env=self._docker_environment(),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                close_fds=True,
            )
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
                process.kill()
                await process.wait()
        except OSError:
            return

    @staticmethod
    async def _write_stdin(process: asyncio.subprocess.Process, value: str | None) -> None:
        if process.stdin is None:
            return
        try:
            if value is not None:
                process.stdin.write(value.encode())
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
    async def _read_stream(
        stream: asyncio.StreamReader | None,
        budget: _OutputBudget,
    ) -> str:
        if stream is None:
            return ""
        chunks: list[bytes] = []
        while True:
            chunk = await stream.read(65_536)
            if not chunk:
                break
            budget.add(len(chunk))
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8", errors="replace")

    @staticmethod
    async def _cancel_tasks(tasks: list[asyncio.Task[Any]]) -> None:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    async def _terminate_cli(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=1)
        except TimeoutError:
            process.kill()
            await process.wait()

    @staticmethod
    def _container_name(execution_id: str) -> str:
        digest = hashlib.sha256(execution_id.encode()).hexdigest()[:24]
        return f"owa-sbx-{digest}"

    @staticmethod
    def _limit_error(name: str) -> None:
        raise ControllerFailure(
            "sandbox_resource_limit",
            f"requested {name} exceeds controller policy",
            422,
        )

    @staticmethod
    def _policy_error(name: str) -> None:
        raise ControllerFailure(
            "sandbox_policy_error",
            f"requested isolation policy is not allowed: {name}",
            422,
        )


def create_app(
    *,
    config: ControllerConfig | None = None,
    runner: ExecutionRunner | None = None,
) -> FastAPI:
    selected_config = config or ControllerConfig.from_environment()
    selected_runner = runner or DockerCliRunner(selected_config)
    app = FastAPI(title="Open Workflow Agent Sandbox Controller", version="0.1.0")

    @app.exception_handler(ControllerFailure)
    async def controller_error(_request: Any, exc: ControllerFailure) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready() -> Any:
        if not selected_config.allowed_images or not await selected_runner.ready():
            return JSONResponse(status_code=503, content={"status": "not_ready"})
        return {"status": "ok"}

    @app.get("/v1/capabilities")
    async def capabilities() -> dict[str, Any]:
        return {
            "backend": "docker",
            "container": True,
            "imagePolicy": "exact_digest_allowlist",
            "pullPolicy": "never",
            "network": "denied",
            "readOnlyRoot": True,
            "hostMounts": False,
            "hostNetwork": False,
            "privileged": False,
            "dropAllCapabilities": True,
            "noNewPrivileges": True,
            "runAsUser": selected_config.run_as_user,
        }

    @app.post("/v1/executions")
    async def execute(request: ExecutionRequest) -> dict[str, Any]:
        result = await selected_runner.execute(request)
        return {
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "duration": result.duration,
        }

    @app.delete("/v1/executions/{execution_id}")
    async def cancel(execution_id: str) -> dict[str, str]:
        await selected_runner.cancel(execution_id)
        return {"status": "cancelled"}

    @app.on_event("shutdown")
    async def shutdown() -> None:
        await selected_runner.shutdown()

    app.state.config = selected_config
    app.state.runner = selected_runner
    return app


app = create_app()


__all__ = [
    "ControllerConfig",
    "DockerCliRunner",
    "ExecutionRequest",
    "ExecutionResult",
    "ExecutionRunner",
    "create_app",
]
