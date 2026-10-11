"""Builders for shell, script, container, and nested workflow tasks."""

from __future__ import annotations

import asyncio
import os
import shlex
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from openworkflow_adk.adk_compat import FunctionNode
from openworkflow_adk.durations import duration_seconds
from openworkflow_adk.expressions import bind
from openworkflow_adk.models import Task

from .common import _kill_process_tree, _sandbox_preexec
from .simple import _dynamic

if TYPE_CHECKING:
    from openworkflow_adk.translator import NodeBuilderRegistry


def _run_workflow_builder(name: str, task: Task, registry: NodeBuilderRegistry) -> FunctionNode:
    configuration = task.run or {}
    reference = configuration.get("workflow")
    if not isinstance(reference, dict):
        raise ValueError(f"workflow task {name!r} requires a workflow reference")
    if registry.workflow_registry is None:
        raise ValueError("run: workflow requires a workflow registry")
    target = registry.workflow_registry.resolve(
        reference.get("namespace", ""),
        reference.get("name", ""),
        reference.get("version", "latest"),
    )
    from openworkflow_adk.translator import build_workflow

    nested = build_workflow(
        target,
        broker=registry.broker,
        model_factory=registry.model_factory,
        function_registry=registry.function_registry,
        workflow_registry=registry.workflow_registry,
    )

    async def run_workflow_node(ctx: Any) -> Any:
        inputs = bind(reference.get("input", {}), ctx.state.to_dict())
        return await ctx.run_node(nested, node_input=inputs)

    return _dynamic(FunctionNode(func=run_workflow_node, name=name))


def _container_builder(name: str, task: Task) -> FunctionNode:
    async def run_container(ctx: Any) -> Any:
        import docker

        configuration = bind(task.run or {}, ctx.state.to_dict())
        process = configuration.get("container")
        if not isinstance(process, dict) or not process.get("image"):
            raise ValueError(f"container task {name!r} requires container.image")

        def _docker() -> tuple[Any, Any, Any, dict[str, Any]]:
            client = docker.from_env()
            image = process["image"]
            pull_policy = process.get("pullPolicy", "ifNotPresent")
            if pull_policy == "always":
                client.images.pull(image)
            elif pull_policy == "ifNotPresent":
                try:
                    client.images.get(image)
                except docker.errors.ImageNotFound:
                    client.images.pull(image)
            elif pull_policy != "never":
                raise ValueError(f"unsupported container pull policy: {pull_policy!r}")

            command = process.get("command")
            arguments = list(process.get("arguments") or [])
            if command:
                command = [*shlex.split(command), *arguments]
            elif arguments:
                command = arguments

            volume_allowlist = _container_volume_allowlist()
            volumes: dict[str, dict[str, Any]] = {}
            for host, target in (process.get("volumes") or {}).items():
                resolved_host = str(_resolve_container_volume(host, volume_allowlist))
                if isinstance(target, dict):
                    bind = target["bind"]
                    mode = target.get("mode", "ro")
                else:
                    bind = target
                    mode = "ro"
                volumes[resolved_host] = {"bind": bind, "mode": mode}

            ports: dict[str, Any] = {}
            if process.get("ports"):
                if not _container_ports_allowed():
                    raise PermissionError(
                        "container host port publishing is disabled; set "
                        "WORKFLOW_CONTAINER_PORTS_ALLOWED=1 to enable it"
                    )
                ports = {
                    container_port: host_port
                    for container_port, host_port in process.get("ports") or {}.items()
                }

            network_mode = _container_network_mode(process.get("network"))
            limits = _container_limits()
            return (
                client,
                command,
                ports,
                {
                    "volumes": volumes or None,
                    "network_mode": network_mode,
                    "cpuset_cpus": limits.get("cpus"),
                    "mem_limit": limits.get("memory"),
                    "pids_limit": limits.get("pids"),
                },
            )

        def _wait_for_container(
            client: Any,
            process_config: dict[str, Any],
            command: Any,
            ports: dict[str, Any],
            run_kwargs: dict[str, Any],
        ) -> tuple[int, str, str]:
            container = client.containers.run(
                process_config["image"],
                command=command,
                name=process_config.get("name"),
                environment=process_config.get("environment") or {},
                ports=ports or None,
                stdin_open=process_config.get("stdin") is not None,
                detach=True,
                remove=False,
                **run_kwargs,
            )
            try:
                if process_config.get("stdin") is not None:
                    socket_ = container.attach_socket(params={"stdin": 1, "stream": 1})
                    if socket_ is not None:
                        socket_._sock.send(str(process_config["stdin"]).encode())
                status = container.wait()
                stdout = container.logs(stdout=True, stderr=False).decode()
                stderr = container.logs(stdout=False, stderr=True).decode()
            finally:
                container.remove(force=True)
                client.close()
            return status.get("StatusCode", 1), stdout, stderr

        client, command, ports, run_kwargs = await asyncio.to_thread(_docker)
        code, stdout, stderr = await asyncio.to_thread(
            _wait_for_container, client, process, command, ports, run_kwargs
        )
        values = {"stdout": stdout, "stderr": stderr, "code": code}
        return (
            None
            if configuration.get("return", "stdout") == "none"
            else (
                values
                if configuration.get("return", "stdout") == "all"
                else values[configuration.get("return", "stdout")]
            )
        )

    return _dynamic(FunctionNode(func=run_container, name=name))


def _container_volume_allowlist() -> list[Path]:
    raw = os.environ.get("WORKFLOW_CONTAINER_VOLUME_ALLOWLIST", "")
    return [Path(item.strip()).resolve() for item in raw.split(",") if item.strip()]


def _container_ports_allowed() -> bool:
    return os.environ.get("WORKFLOW_CONTAINER_PORTS_ALLOWED", "").lower() in {
        "1",
        "true",
        "yes",
    }


def _container_network_mode(requested: Any) -> str:
    """Return the container network mode, defaulting to the isolated ``none``.

    Any non-``none`` mode must be explicitly requested in the workflow and
    present on ``WORKFLOW_CONTAINER_NETWORK_ALLOWLIST`` (comma-separated).
    """
    if requested in {None, "", "none"}:
        return "none"
    allowlist = {
        item.strip()
        for item in os.environ.get("WORKFLOW_CONTAINER_NETWORK_ALLOWLIST", "").split(",")
        if item.strip()
    }
    if requested not in allowlist:
        raise PermissionError(
            f"container network mode {requested!r} is not on WORKFLOW_CONTAINER_NETWORK_ALLOWLIST"
        )
    return str(requested)


def _container_limits() -> dict[str, Any]:
    """Read hard resource caps for containers from the environment."""
    limits: dict[str, Any] = {}
    cpus = os.environ.get("WORKFLOW_CONTAINER_CPU_LIMIT")
    if cpus:
        limits["cpus"] = cpus
    memory = os.environ.get("WORKFLOW_CONTAINER_MEMORY_LIMIT")
    if memory:
        limits["memory"] = memory
    pids = os.environ.get("WORKFLOW_CONTAINER_PIDS_LIMIT")
    if pids:
        limits["pids"] = int(pids)
    return limits


def _resolve_script_source(source: str, base_dir: Path) -> Path:
    """Resolve a script source path and ensure it stays inside ``base_dir``."""
    path = Path(source)
    if not path.is_absolute():
        path = base_dir / path
    path = path.resolve()
    try:
        path.relative_to(base_dir)
    except ValueError as exc:
        raise ValueError(
            f"script source {source!r} escapes the configured script base directory"
        ) from exc
    return path


def _resolve_container_volume(host: str, allowlist: list[Path]) -> Path:
    """Resolve a container host volume path and ensure it is within the allowlist.

    With an empty allowlist the guard fails closed: any host volume mount is
    denied because an unconstrained mount exposes arbitrary host paths to the
    container (and ``rw`` mode permits host writes).
    """
    if not allowlist:
        raise PermissionError(
            "container volume mounts are disabled: set WORKFLOW_CONTAINER_VOLUME_ALLOWLIST "
            "to the allowed host root path(s)"
        )
    path = Path(host).resolve()
    for root in allowlist:
        try:
            path.relative_to(root)
            return path
        except ValueError:
            continue
    raise ValueError(
        f"container volume host path {host!r} is not under any allowed root: "
        f"{[str(root) for root in allowlist]}"
    )


def _run_builder(
    name: str, task: Task, registry: NodeBuilderRegistry | None = None
) -> FunctionNode:
    if "workflow" in (task.run or {}):
        if registry is None:
            raise ValueError("run: workflow requires a workflow registry")
        return _run_workflow_builder(name, task, registry)
    if "container" in (task.run or {}):
        return _container_builder(name, task)

    async def run_process_uncached(ctx: Any) -> Any:
        configuration = bind(task.run or {}, ctx.state.to_dict())
        return_type = configuration.get("return", "stdout")
        if "shell" in configuration:
            process = configuration["shell"]
            command = [process["command"], *process.get("arguments", [])]
        elif "script" in configuration:
            process = configuration["script"]
            language = process.get("language", "python").lower()
            code = process.get("code")
            if code is None:
                source = process.get("source")
                source = source.get("uri") if isinstance(source, dict) else source
                if not source:
                    raise ValueError("script source must be a local path")
                script_base = Path(
                    os.environ.get("WORKFLOW_SCRIPT_BASE_DIR", str(Path.cwd()))
                ).resolve()
                code = _resolve_script_source(source, script_base).read_text()
            if language in {"python", "py"}:
                command = [sys.executable, "-c", code, *process.get("arguments", [])]
            elif language in {"javascript", "js", "node"}:
                node = shutil.which("node")
                if node is None:
                    raise RuntimeError("JavaScript scripts require the node executable")
                command = [node, "-e", code, *process.get("arguments", [])]
            else:
                raise NotImplementedError(f"unsupported script language: {language}")
        else:
            raise NotImplementedError("run handler supports shell and Python script")
        default_timeout = float(os.environ.get("WORKFLOW_RUN_DEFAULT_TIMEOUT", "60"))
        if default_timeout <= 0:
            raise ValueError("WORKFLOW_RUN_DEFAULT_TIMEOUT must be greater than zero")
        child_environment = {
            key: value
            for key, value in {**os.environ, **(process.get("environment") or {})}.items()
            if not key.startswith("WORKFLOW_SECRET__")
        }
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE if process.get("stdin") is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=child_environment,
            preexec_fn=_sandbox_preexec(process.get("limits")) if os.name == "posix" else None,
        )
        stdin = process.get("stdin")
        timeout = default_timeout
        if isinstance(task.timeout, dict) and task.timeout.get("after") is not None:
            timeout = duration_seconds(task.timeout["after"])
        elif isinstance(task.timeout, str):
            timeout = duration_seconds(task.timeout)
        communicate = proc.communicate(str(stdin).encode() if stdin is not None else None)
        try:
            stdout, stderr = await asyncio.wait_for(communicate, float(timeout))
        except asyncio.TimeoutError as error:
            _kill_process_tree(proc)
            await proc.wait()
            raise TimeoutError(f"run task {name!r} exceeded its timeout") from error
        values = {
            "stdout": stdout.decode().replace("\r\n", "\n"),
            "stderr": stderr.decode().replace("\r\n", "\n"),
            "code": proc.returncode,
        }
        if return_type == "none":
            return None
        if return_type == "all":
            return values
        return values[return_type]

    async def run_process(ctx: Any) -> Any:
        if registry is None or registry.memoization is None:
            return await run_process_uncached(ctx)
        arguments = bind(task.run or {}, ctx.state.to_dict())
        cache = registry.memoization
        return await cache.get_or_compute(
            cache.key(f"run:{name}", arguments), lambda: run_process_uncached(ctx)
        )

    return FunctionNode(func=run_process, name=name)
