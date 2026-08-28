"""Kubernetes/OpenShift sandbox backend using a restricted local controller."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any

import httpx

from ...config import SandboxConfig
from ...errors import (
    SandboxPolicyError,
    SandboxProcessError,
    SandboxTimeoutError,
)
from ..contract import SandboxExecutionRequest, SandboxExecutionResult, SandboxSecretReference
from .controller import cancel_controller_execution, controller_error


class KubernetesSandboxBackend:
    """Execute containers through a loopback-only Kubernetes controller boundary.

    The Open Workflow Agent runtime does not receive Kubernetes credentials. A
    sidecar controller owns the projected service-account token and namespace
    permissions required to create and clean up ephemeral sandbox Jobs.
    """

    def __init__(
        self,
        config: SandboxConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config
        self._client = httpx.AsyncClient(
            base_url=config.kubernetes.controller_url,
            transport=transport,
            timeout=config.timeout_seconds + 10.0,
            trust_env=False,
        )
        self._active: set[str] = set()

    def capabilities(self) -> dict[str, Any]:
        selected = self.config.kubernetes
        enabled = self.config.enabled and self.config.backend == "kubernetes"
        process_limit = self.config.process_count is not None and selected.process_limit_enforced
        return {
            "enabled": enabled,
            "backend": "kubernetes",
            "platform": selected.platform,
            "internalProcess": False,
            "script": {"enabled": False, "runtimes": [], "externalSource": False},
            "shell": {"enabled": False},
            "container": {
                "enabled": enabled,
                "imagePolicy": (
                    "exact_digest_allowlist" if selected.require_digest else "exact_allowlist"
                ),
                "ports": False,
                "volumes": False,
                "stdin": False,
            },
            "cancellation": True,
            "resourceLimits": {
                "posixRlimit": False,
                "workspaceQuota": True,
                "outputBytes": True,
                "timeout": True,
                "cpu": True,
                "memory": self.config.memory_bytes is not None,
                "fileSize": False,
                "processCount": process_limit,
            },
            "filesystemIsolation": "isolated_root",
            "networkIsolation": (
                "denied" if selected.network_policy_enforced else "not_guaranteed"
            ),
            "hardIsolation": True,
            "controllerTransport": "loopback_http",
            "nativeIdentifiersExposed": False,
        }

    async def execute(self, request: SandboxExecutionRequest) -> SandboxExecutionResult:
        selected = self.config.kubernetes
        if not self.config.enabled or self.config.backend != "kubernetes":
            raise SandboxPolicyError("Kubernetes sandbox execution is disabled")
        if request.kind != "container":
            raise SandboxPolicyError(
                "Kubernetes sandbox backend accepts only run.container executions",
                details={"kind": request.kind},
            )
        image = request.image
        if not image or image not in selected.allowed_images:
            raise SandboxPolicyError("container image is not deployment-approved")
        if request.stdin is not None:
            raise SandboxPolicyError("Kubernetes sandbox backend does not support container stdin")
        if selected.network == "denied" and not selected.network_policy_enforced:
            raise SandboxPolicyError(
                "Kubernetes sandbox network isolation cannot be guaranteed by this deployment"
            )
        if self.config.process_count is not None and not selected.process_limit_enforced:
            raise SandboxPolicyError(
                "Kubernetes sandbox process-count isolation cannot be guaranteed by this deployment"
            )

        environment = self._encode_environment(request)
        payload = {
            "execution_id": request.execution_id,
            "image": image,
            "command": request.command,
            "arguments": list(request.arguments),
            "environment": environment,
            "limits": {
                "timeout_seconds": self.config.timeout_seconds,
                "max_output_bytes": self.config.max_output_bytes,
                "max_workspace_bytes": self.config.max_workspace_bytes,
                "memory_bytes": self.config.memory_bytes,
                "process_count": self.config.process_count,
            },
            "isolation": {
                "network": selected.network,
                "network_policy_enforced": selected.network_policy_enforced,
                "process_limit_enforced": selected.process_limit_enforced,
                "read_only_root": True,
                "run_as_non_root": True,
                "drop_all_capabilities": True,
                "allow_privilege_escalation": False,
                "seccomp_profile": "RuntimeDefault",
                "host_mounts": False,
                "host_network": False,
                "host_pid": False,
                "host_ipc": False,
                "automount_service_account_token": False,
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
            response = await self._client.post(
                "/v1/executions",
                content=encoded,
                headers={"Content-Type": "application/json"},
            )
        except asyncio.CancelledError:
            await self._best_effort_cancel(request.execution_id)
            raise
        except httpx.TimeoutException as exc:
            await self._best_effort_cancel(request.execution_id)
            raise SandboxTimeoutError("Kubernetes sandbox controller request timed out") from exc
        except httpx.HTTPError as exc:
            raise SandboxProcessError("Kubernetes sandbox controller is unavailable") from exc
        finally:
            self._active.discard(request.execution_id)

        if response.status_code >= 400:
            raise self._controller_error(response)
        try:
            value = response.json()
            if not isinstance(value, Mapping):
                raise TypeError("response is not an object")
            return SandboxExecutionResult(
                execution_id=request.execution_id,
                exit_code=int(value["exit_code"]),
                stdout=str(value.get("stdout", "")),
                stderr=str(value.get("stderr", "")),
                duration=float(value["duration"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SandboxProcessError(
                "Kubernetes sandbox controller returned an invalid response"
            ) from exc

    async def cancel(self, execution_id: str) -> None:
        if execution_id not in self._active:
            return
        await self._cancel_controller_execution(execution_id)

    async def shutdown(self) -> None:
        for execution_id in tuple(self._active):
            await self._best_effort_cancel(execution_id)
        await self._client.aclose()

    def _encode_environment(
        self, request: SandboxExecutionRequest
    ) -> dict[str, str | dict[str, str]]:
        selected = self.config.kubernetes
        environment: dict[str, str | dict[str, str]] = {}
        for name, configured_value in request.environment:
            if isinstance(configured_value, SandboxSecretReference):
                if configured_value.name not in selected.secret_keys:
                    raise SandboxPolicyError(
                        "sandbox secret reference is not deployment-approved",
                        details={"name": configured_value.name},
                    )
                if not selected.secret_name:
                    raise SandboxPolicyError("Kubernetes sandbox secret store is not configured")
                environment[name] = {"secret_ref": configured_value.name}
            else:
                environment[name] = configured_value
        return environment

    async def _best_effort_cancel(self, execution_id: str) -> None:
        try:
            await asyncio.shield(self._cancel_controller_execution(execution_id))
        except Exception:
            return

    async def _cancel_controller_execution(self, execution_id: str) -> None:
        await cancel_controller_execution(
            self._client, backend_label="Kubernetes", execution_id=execution_id
        )

    @staticmethod
    def _controller_error(response: httpx.Response) -> Exception:
        return controller_error(response, backend_label="Kubernetes")


__all__ = ["KubernetesSandboxBackend"]
