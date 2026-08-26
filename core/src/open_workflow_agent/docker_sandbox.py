"""Docker sandbox backend that talks only to a restricted local controller."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import httpx

from .config import SandboxConfig
from .errors import (
    SandboxOutputLimitError,
    SandboxPolicyError,
    SandboxProcessError,
    SandboxResourceLimitError,
    SandboxTimeoutError,
)
from .sandbox import SandboxExecutionRequest, SandboxExecutionResult, SandboxSecretReference

_ERROR_TYPES = {
    "sandbox_policy_error": SandboxPolicyError,
    "sandbox_timeout": SandboxTimeoutError,
    "sandbox_output_limit": SandboxOutputLimitError,
    "sandbox_resource_limit": SandboxResourceLimitError,
    "sandbox_process_error": SandboxProcessError,
}


class DockerSandboxBackend:
    """Execute containers through a minimal controller Unix socket.

    The main runtime never opens the Docker daemon socket. The controller owns
    daemon access and independently enforces its image/isolation policy.
    """

    def __init__(
        self,
        config: SandboxConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config
        selected_transport = transport or httpx.AsyncHTTPTransport(
            uds=config.docker.controller_socket
        )
        self._client = httpx.AsyncClient(
            base_url="http://owa-sandbox-controller",
            transport=selected_transport,
            timeout=config.timeout_seconds + 5.0,
            trust_env=False,
        )
        self._active: set[str] = set()

    def capabilities(self) -> dict[str, Any]:
        enabled = self.config.enabled and self.config.backend == "docker"
        return {
            "enabled": enabled,
            "backend": "docker",
            "internalProcess": False,
            "script": {"enabled": False, "runtimes": [], "externalSource": False},
            "shell": {"enabled": False},
            "container": {
                "enabled": enabled,
                "imagePolicy": (
                    "exact_digest_allowlist"
                    if self.config.docker.require_digest
                    else "exact_allowlist"
                ),
                "ports": False,
                "volumes": False,
            },
            "cancellation": True,
            "resourceLimits": {
                "posixRlimit": False,
                "workspaceQuota": True,
                "outputBytes": True,
                "timeout": True,
                "cpu": False,
                "memory": self.config.memory_bytes is not None,
                "fileSize": False,
                "processCount": self.config.process_count is not None,
            },
            "filesystemIsolation": "isolated_root",
            "networkIsolation": self.config.docker.network,
            "hardIsolation": True,
            "controllerTransport": "unix_socket",
        }

    async def execute(self, request: SandboxExecutionRequest) -> SandboxExecutionResult:
        if not self.config.enabled or self.config.backend != "docker":
            raise SandboxPolicyError("Docker sandbox execution is disabled")
        if request.kind != "container":
            raise SandboxPolicyError(
                "Docker sandbox backend accepts only run.container executions",
                details={"kind": request.kind},
            )
        image = request.image
        if not image or image not in self.config.docker.allowed_images:
            raise SandboxPolicyError("container image is not deployment-approved")

        environment = self._resolve_environment(request)
        payload = {
            "execution_id": request.execution_id,
            "image": image,
            "command": request.command,
            "arguments": list(request.arguments),
            "stdin": request.stdin,
            "environment": environment,
            "limits": {
                "timeout_seconds": self.config.timeout_seconds,
                "max_output_bytes": self.config.max_output_bytes,
                "max_workspace_bytes": self.config.max_workspace_bytes,
                "memory_bytes": self.config.memory_bytes,
                "process_count": self.config.process_count,
            },
            "isolation": {
                "run_as_user": self.config.docker.run_as_user,
                "network": self.config.docker.network,
                "read_only_root": True,
                "drop_all_capabilities": True,
                "no_new_privileges": True,
                "host_mounts": False,
                "host_network": False,
            },
        }
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if len(encoded) > self.config.max_input_bytes:
            raise SandboxPolicyError(
                "sandbox input limit exceeded",
                details={"max_input_bytes": self.config.max_input_bytes},
            )

        self._active.add(request.execution_id)
        try:
            response = await self._client.post("/v1/executions", content=encoded)
        except asyncio.CancelledError:
            await self._cancel_controller_execution(request.execution_id)
            raise
        except httpx.TimeoutException as exc:
            await self._cancel_controller_execution(request.execution_id)
            raise SandboxTimeoutError("Docker sandbox controller request timed out") from exc
        except httpx.HTTPError as exc:
            raise SandboxProcessError("Docker sandbox controller is unavailable") from exc
        finally:
            self._active.discard(request.execution_id)

        if response.status_code >= 400:
            raise self._controller_error(response)
        try:
            value = response.json()
            return SandboxExecutionResult(
                execution_id=request.execution_id,
                exit_code=int(value["exit_code"]),
                stdout=str(value.get("stdout", "")),
                stderr=str(value.get("stderr", "")),
                duration=float(value["duration"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SandboxProcessError("Docker sandbox controller returned an invalid response") from exc

    async def cancel(self, execution_id: str) -> None:
        if execution_id not in self._active:
            return
        await self._cancel_controller_execution(execution_id)

    async def shutdown(self) -> None:
        executions = tuple(self._active)
        for execution_id in executions:
            try:
                await self._cancel_controller_execution(execution_id)
            except SandboxProcessError:
                pass
        await self._client.aclose()

    async def _cancel_controller_execution(self, execution_id: str) -> None:
        try:
            response = await self._client.delete(f"/v1/executions/{execution_id}")
        except httpx.HTTPError as exc:
            raise SandboxProcessError("Docker sandbox cancellation request failed") from exc
        if response.status_code not in {200, 202, 204, 404}:
            raise self._controller_error(response)

    def _resolve_environment(self, request: SandboxExecutionRequest) -> dict[str, str]:
        environment: dict[str, str] = {}
        for name, configured_value in request.environment:
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
        return environment

    @staticmethod
    def _controller_error(response: httpx.Response) -> Exception:
        code = "sandbox_process_error"
        try:
            body = response.json()
            error = body.get("error", {}) if isinstance(body, dict) else {}
            if isinstance(error, dict) and isinstance(error.get("code"), str):
                code = error["code"]
        except ValueError:
            pass
        error_type = _ERROR_TYPES.get(code, SandboxProcessError)
        return error_type(
            "Docker sandbox controller rejected execution",
            details={"controller_code": code, "status": response.status_code},
        )


__all__ = ["DockerSandboxBackend"]
