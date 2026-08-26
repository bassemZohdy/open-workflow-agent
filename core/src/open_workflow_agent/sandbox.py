"""Framework-neutral bounded internal sandbox execution."""

from __future__ import annotations

import asyncio
import copy
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, cast
from uuid import uuid4

from .catalog import FunctionCatalog
from .config import SandboxConfig
from .errors import (
    SandboxOutputLimitError,
    SandboxPolicyError,
    SandboxProcessError,
    SandboxResourceLimitError,
    SandboxTimeoutError,
    UnsupportedWorkflowFeature,
    WorkflowSemanticError,
)
from .workflow import (
    ExecutionState,
    WorkflowExecutor,
    WorkflowPlan,
    generate_default_workflow,
    load_workflow,
    normalize_workflow,
    validate_capabilities,
    validate_schema,
)

SandboxKind = Literal["script", "shell"]
SandboxStatus = Literal["completed"]
_RESERVED_ENVIRONMENT = frozenset({"PATH", "HOME", "TMPDIR"})


@dataclass(frozen=True, slots=True)
class SandboxSecretReference:
    """Deployment-owned environment variable reference, never a serialized secret value."""

    name: str


@dataclass(frozen=True, slots=True)
class SandboxExecutionRequest:
    execution_id: str
    kind: SandboxKind
    command: str | None = None
    arguments: tuple[str, ...] = ()
    stdin: str | None = None
    environment: tuple[tuple[str, str | SandboxSecretReference], ...] = ()
    script_language: str | None = None
    script_code: str | None = None
    invocation_id: str | None = None
    task_reference: str | None = None


@dataclass(frozen=True, slots=True)
class SandboxExecutionResult:
    execution_id: str
    exit_code: int
    stdout: str
    stderr: str
    duration: float
    status: SandboxStatus = "completed"

    def as_output(self) -> dict[str, Any]:
        return {
            "exitCode": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


class SandboxBackend(Protocol):
    async def execute(self, request: SandboxExecutionRequest) -> SandboxExecutionResult: ...

    async def cancel(self, execution_id: str) -> None: ...

    async def shutdown(self) -> None: ...

    def capabilities(self) -> dict[str, Any]: ...


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
        return {
            "enabled": self.config.enabled,
            "backend": "internal",
            "internalProcess": self.config.enabled,
            "script": {
                "enabled": self.config.enabled,
                "runtimes": list(self.config.script_runtimes),
                "externalSource": False,
            },
            "shell": {"enabled": self.config.enabled and self.config.allow_shell},
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
        if not self.config.enabled:
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
        values = [request.script_code or "", request.stdin or "", request.command or ""]
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
            value = os.getenv(name)
            if value is not None:
                environment[name] = value
        for name, value in request.environment:
            if name in _RESERVED_ENVIRONMENT:
                raise SandboxPolicyError(
                    "sandbox environment cannot override runtime isolation variables",
                    details={"name": name},
                )
            if isinstance(value, SandboxSecretReference):
                if value.name not in self.config.secret_environment:
                    raise SandboxPolicyError(
                        "sandbox secret reference is not deployment-approved",
                        details={"name": value.name},
                    )
                resolved = os.getenv(value.name)
                if resolved is None:
                    raise SandboxPolicyError(
                        "sandbox secret reference is unavailable",
                        details={"name": value.name},
                    )
                environment[name] = resolved
            else:
                environment[name] = value
        total = sum(len(key.encode()) + len(value.encode()) for key, value in environment.items())
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
        return b"".join(chunks).decode("utf-8", errors="replace")

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

            limits: list[tuple[int, int | None]] = [
                (resource.RLIMIT_CPU, self.config.cpu_seconds),
                (resource.RLIMIT_AS, self.config.memory_bytes),
                (resource.RLIMIT_FSIZE, self.config.file_size_bytes),
            ]
            process_limit = getattr(resource, "RLIMIT_NPROC", None)
            if process_limit is not None:
                limits.append((process_limit, self.config.process_count))
            for resource_id, value in limits:
                if value is not None:
                    resource.setrlimit(resource_id, (value, value))

        return apply_limits

    @staticmethod
    def _resource_signal(number: int) -> bool:
        names = {signal.SIGKILL}
        if hasattr(signal, "SIGXCPU"):
            names.add(signal.SIGXCPU)
        if hasattr(signal, "SIGXFSZ"):
            names.add(signal.SIGXFSZ)
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
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
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
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            return
        await process.wait()


class SandboxManager:
    def __init__(self, backend: SandboxBackend) -> None:
        self.backend = backend

    async def execute(self, request: SandboxExecutionRequest) -> SandboxExecutionResult:
        return await self.backend.execute(request)

    async def cancel(self, execution_id: str) -> None:
        await self.backend.cancel(execution_id)

    async def shutdown(self) -> None:
        await self.backend.shutdown()

    def capabilities(self) -> dict[str, Any]:
        return self.backend.capabilities()


class SandboxWorkflowExecutor(WorkflowExecutor):
    """Common executor extension that routes executable run tasks to SandboxManager."""

    async def _run_subworkflow(self, definition: Any, state: ExecutionState) -> Any:
        if not isinstance(definition, Mapping):
            raise WorkflowSemanticError("run configuration must be an object")
        if "script" in definition:
            return await self._run_script(definition["script"], state)
        if "shell" in definition:
            return await self._run_shell(definition["shell"], state)
        if "container" in definition:
            raise UnsupportedWorkflowFeature("run.container requires an external sandbox backend")
        return await super()._run_subworkflow(definition, state)

    async def _run_script(self, definition: Any, state: ExecutionState) -> Any:
        if not isinstance(definition, Mapping):
            raise WorkflowSemanticError("run.script must be an object")
        request = SandboxExecutionRequest(
            execution_id=self._execution_id(state),
            kind="script",
            arguments=tuple(str(value) for value in definition.get("arguments", [])),
            stdin=self._stdin(definition, state),
            environment=self._environment(definition, state),
            script_language=str(definition.get("language", "")),
            script_code=cast(str | None, definition.get("code")),
            invocation_id=cast(str | None, state.context.get("invocation_id")),
            task_reference=cast(str | None, state.variables.get("_task_reference")),
        )
        return await self._execute_sandbox(request, state)

    async def _run_shell(self, definition: Any, state: ExecutionState) -> Any:
        if not isinstance(definition, Mapping):
            raise WorkflowSemanticError("run.shell must be an object")
        request = SandboxExecutionRequest(
            execution_id=self._execution_id(state),
            kind="shell",
            command=str(definition.get("command", "")),
            arguments=tuple(str(value) for value in definition.get("arguments", [])),
            stdin=self._stdin(definition, state),
            environment=self._environment(definition, state),
            invocation_id=cast(str | None, state.context.get("invocation_id")),
            task_reference=cast(str | None, state.variables.get("_task_reference")),
        )
        return await self._execute_sandbox(request, state)

    async def _execute_sandbox(
        self, request: SandboxExecutionRequest, state: ExecutionState
    ) -> dict[str, Any]:
        if self.services is None or not hasattr(self.services, "sandbox"):
            raise SandboxPolicyError("sandbox service is unavailable")
        reference = str(state.variables.get("_task_reference", "unknown-task"))
        name = state.variables.get("_task_name", "run")
        self._emit(
            "TaskProgress",
            {
                **self._task_event(reference, name, state),
                "status": "running",
                "progress": {"phase": "sandbox_start"},
            },
        )
        result = await self._await_with_cancellation(self.services.sandbox.execute(request), state)
        self._emit(
            "TaskProgress",
            {
                **self._task_event(reference, name, state),
                "status": "running",
                "progress": {"phase": "sandbox_finished"},
            },
        )
        return cast(SandboxExecutionResult, result).as_output()

    def _stdin(self, definition: Mapping[str, Any], state: ExecutionState) -> str | None:
        value = definition.get("stdin")
        if value is None:
            return None
        evaluated = self.expressions.evaluate(value, state.data, variables=state.variables)
        return evaluated if isinstance(evaluated, str) else str(evaluated)

    def _environment(
        self, definition: Mapping[str, Any], state: ExecutionState
    ) -> tuple[tuple[str, str | SandboxSecretReference], ...]:
        value = definition.get("environment", {})
        if not isinstance(value, Mapping):
            raise WorkflowSemanticError("sandbox environment must be an object")
        result: list[tuple[str, str | SandboxSecretReference]] = []
        for name, raw in value.items():
            if isinstance(raw, Mapping) and set(raw) == {"fromEnv"}:
                reference = raw.get("fromEnv")
                if not isinstance(reference, str):
                    raise WorkflowSemanticError("sandbox fromEnv reference must be a string")
                result.append((str(name), SandboxSecretReference(reference)))
                continue
            evaluated = self.expressions.evaluate(raw, state.data, variables=state.variables)
            result.append((str(name), evaluated if isinstance(evaluated, str) else str(evaluated)))
        return tuple(result)

    @staticmethod
    def _execution_id(state: ExecutionState) -> str:
        invocation = state.context.get("invocation_id", "unknown-invocation")
        reference = state.variables.get("_task_reference", "unknown-task")
        return f"{invocation}:{reference}:{uuid4().hex[:12]}"


def validate_sandbox_capabilities(
    workflow: Mapping[str, Any],
    *,
    sandbox: SandboxConfig,
    trusted_catalogs: Mapping[str, Any] | None = None,
) -> None:
    rewritten = copy.deepcopy(dict(workflow))
    _rewrite_executable_runs(rewritten, sandbox=sandbox, reference="")
    validate_capabilities(rewritten, trusted_catalogs=trusted_catalogs)


def compile_sandbox_workflow(
    source: str | Path | Mapping[str, Any] | None = None,
    *,
    sandbox: SandboxConfig,
    trusted_catalogs: Mapping[str, Any] | None = None,
) -> WorkflowPlan:
    workflow = generate_default_workflow() if source is None else load_workflow(source)
    validate_schema(workflow)
    validate_sandbox_capabilities(workflow, sandbox=sandbox, trusted_catalogs=trusted_catalogs)
    return normalize_workflow(workflow)


async def resolve_and_compile_sandbox_workflow(
    source: str | Path | Mapping[str, Any] | None = None,
    *,
    sandbox: SandboxConfig,
    trusted_catalogs: Mapping[str, Any] | None = None,
    resolver: Any,
    catalog: FunctionCatalog,
) -> WorkflowPlan:
    workflow = generate_default_workflow() if source is None else load_workflow(source)
    validate_schema(workflow)
    await resolver.resolve_workflow(workflow, catalog)
    validate_sandbox_capabilities(workflow, sandbox=sandbox, trusted_catalogs=trusted_catalogs)
    return normalize_workflow(workflow)


def _rewrite_executable_runs(value: Any, *, sandbox: SandboxConfig, reference: str) -> None:
    if isinstance(value, list):
        for index, item in enumerate(value):
            _rewrite_executable_runs(item, sandbox=sandbox, reference=f"{reference}/{index}")
        return
    if not isinstance(value, dict):
        return
    run = value.get("run")
    if isinstance(run, Mapping):
        run_reference = reference or "/run"
        replacement = _validate_executable_run(run, sandbox=sandbox, reference=run_reference)
        if replacement is not None:
            value["run"] = replacement
    for key, item in list(value.items()):
        if key == "run":
            continue
        _rewrite_executable_runs(item, sandbox=sandbox, reference=f"{reference}/{key}")


def _validate_executable_run(
    run: Mapping[str, Any], *, sandbox: SandboxConfig, reference: str
) -> dict[str, Any] | None:
    if "container" in run:
        raise UnsupportedWorkflowFeature(
            "run.container requires an external sandbox backend",
            details={"reference": reference},
        )
    if "script" in run:
        if not sandbox.enabled:
            raise UnsupportedWorkflowFeature(
                "run.script requires the deployment-enabled internal sandbox",
                details={"reference": reference},
            )
        script = run["script"]
        if not isinstance(script, Mapping):
            raise WorkflowSemanticError(f"run.script must be an object at {reference}")
        if "source" in script:
            raise UnsupportedWorkflowFeature(
                "external script resources are not enabled",
                details={"reference": reference},
            )
        language = script.get("language")
        if language not in sandbox.script_runtimes:
            raise UnsupportedWorkflowFeature(
                "script runtime is not enabled",
                details={"reference": reference, "runtime": language},
            )
        if not isinstance(script.get("code"), str):
            raise WorkflowSemanticError(f"run.script requires inline code at {reference}")
        _validate_environment(script.get("environment"), sandbox, reference)
        return _placeholder_workflow_run()
    if "shell" in run:
        if not sandbox.enabled or not sandbox.allow_shell:
            raise UnsupportedWorkflowFeature(
                "run.shell requires deployment-enabled shell execution",
                details={"reference": reference},
            )
        shell = run["shell"]
        if not isinstance(shell, Mapping):
            raise WorkflowSemanticError(f"run.shell must be an object at {reference}")
        command = shell.get("command")
        if not isinstance(command, str) or not command.strip():
            raise WorkflowSemanticError(f"run.shell requires command at {reference}")
        if "${" in command:
            raise UnsupportedWorkflowFeature(
                "dynamic shell executable names are not enabled",
                details={"reference": reference},
            )
        _validate_environment(shell.get("environment"), sandbox, reference)
        return _placeholder_workflow_run()
    return None


def _validate_environment(value: Any, sandbox: SandboxConfig, reference: str) -> None:
    if value is None:
        return
    if not isinstance(value, Mapping):
        raise WorkflowSemanticError(f"sandbox environment must be an object at {reference}")
    for name, raw in value.items():
        if str(name) in _RESERVED_ENVIRONMENT:
            raise UnsupportedWorkflowFeature(
                "sandbox environment cannot override runtime isolation variables",
                details={"reference": reference, "name": str(name)},
            )
        if isinstance(raw, Mapping) and set(raw) == {"fromEnv"}:
            environment_name = raw.get("fromEnv")
            if (
                not isinstance(environment_name, str)
                or environment_name not in sandbox.secret_environment
            ):
                raise UnsupportedWorkflowFeature(
                    "sandbox secret reference is not deployment-approved",
                    details={"reference": reference, "name": environment_name},
                )


def _placeholder_workflow_run() -> dict[str, Any]:
    return {
        "workflow": {
            "namespace": "open-workflow-agent",
            "name": "sandbox-placeholder",
            "version": "0.0.0",
        }
    }


__all__ = [
    "InternalSandboxBackend",
    "SandboxBackend",
    "SandboxExecutionRequest",
    "SandboxExecutionResult",
    "SandboxManager",
    "SandboxSecretReference",
    "SandboxWorkflowExecutor",
    "compile_sandbox_workflow",
    "resolve_and_compile_sandbox_workflow",
    "validate_sandbox_capabilities",
]
