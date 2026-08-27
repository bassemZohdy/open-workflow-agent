"""Restricted Kubernetes/OpenShift sandbox controller."""

from __future__ import annotations

import asyncio
import hashlib
import math
import os
import re
import ssl
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_IMMUTABLE_IMAGE = re.compile(r"^.+@sha256:[0-9a-fA-F]{64}$")
_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DNS_LABEL = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ControllerConfig(StrictModel):
    """Deployment-owned policy and Kubernetes API connection."""

    api_server: str = "https://kubernetes.default.svc"
    token_file: str = "/var/run/secrets/owa-controller/token"
    ca_file: str = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
    namespace: str = "owa-sandbox"
    workload_service_account: str = "owa-sandbox-workload"
    allowed_images: list[str] = Field(default_factory=list)
    secret_name: str | None = None
    secret_keys: list[str] = Field(default_factory=list)
    platform: str = "kubernetes"
    network_policy_enforced: bool = False
    process_limit_enforced: bool = False
    max_timeout_seconds: float = 60.0
    max_input_bytes: int = 1_048_576
    max_output_bytes: int = 1_048_576
    max_workspace_bytes: int = 33_554_432
    max_memory_bytes: int = 536_870_912
    max_process_count: int = 64
    cpu_limit: str = "500m"
    ttl_seconds_after_finished: int = 60
    poll_interval_seconds: float = 0.25

    @classmethod
    def from_environment(cls) -> ControllerConfig:
        allowed = _csv("OWA_K8S_CONTROLLER_ALLOWED_IMAGES")
        secret_keys = _csv("OWA_K8S_CONTROLLER_SECRET_KEYS")
        return cls(
            api_server=os.getenv("OWA_K8S_CONTROLLER_API_SERVER", "https://kubernetes.default.svc"),
            token_file=os.getenv(
                "OWA_K8S_CONTROLLER_TOKEN_FILE",
                "/var/run/secrets/owa-controller/token",
            ),
            ca_file=os.getenv(
                "OWA_K8S_CONTROLLER_CA_FILE",
                "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt",
            ),
            namespace=os.getenv("OWA_K8S_CONTROLLER_NAMESPACE", "owa-sandbox"),
            workload_service_account=os.getenv(
                "OWA_K8S_CONTROLLER_WORKLOAD_SERVICE_ACCOUNT",
                "owa-sandbox-workload",
            ),
            allowed_images=allowed,
            secret_name=os.getenv("OWA_K8S_CONTROLLER_SECRET_NAME") or None,
            secret_keys=secret_keys,
            platform=os.getenv("OWA_K8S_CONTROLLER_PLATFORM", "kubernetes"),
            network_policy_enforced=_env_bool("OWA_K8S_CONTROLLER_NETWORK_POLICY_ENFORCED", False),
            process_limit_enforced=_env_bool("OWA_K8S_CONTROLLER_PROCESS_LIMIT_ENFORCED", False),
            max_timeout_seconds=float(os.getenv("OWA_K8S_CONTROLLER_MAX_TIMEOUT_SECONDS", "60")),
            max_input_bytes=int(os.getenv("OWA_K8S_CONTROLLER_MAX_INPUT_BYTES", "1048576")),
            max_output_bytes=int(os.getenv("OWA_K8S_CONTROLLER_MAX_OUTPUT_BYTES", "1048576")),
            max_workspace_bytes=int(
                os.getenv("OWA_K8S_CONTROLLER_MAX_WORKSPACE_BYTES", "33554432")
            ),
            max_memory_bytes=int(os.getenv("OWA_K8S_CONTROLLER_MAX_MEMORY_BYTES", "536870912")),
            max_process_count=int(os.getenv("OWA_K8S_CONTROLLER_MAX_PROCESS_COUNT", "64")),
            cpu_limit=os.getenv("OWA_K8S_CONTROLLER_CPU_LIMIT", "500m"),
            ttl_seconds_after_finished=int(os.getenv("OWA_K8S_CONTROLLER_TTL_SECONDS", "60")),
            poll_interval_seconds=float(
                os.getenv("OWA_K8S_CONTROLLER_POLL_INTERVAL_SECONDS", "0.25")
            ),
        )

    @field_validator("api_server")
    @classmethod
    def validate_api_server(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("Kubernetes API server must use HTTPS")
        return value.rstrip("/")

    @field_validator("token_file", "ca_file")
    @classmethod
    def validate_absolute_path(cls, value: str) -> str:
        path = Path(value)
        if not path.is_absolute():
            raise ValueError("Kubernetes controller credential paths must be absolute")
        return str(path)

    @field_validator("namespace", "workload_service_account")
    @classmethod
    def validate_dns_label(cls, value: str) -> str:
        selected = value.strip()
        if len(selected) > 63 or not _DNS_LABEL.fullmatch(selected):
            raise ValueError("Kubernetes namespace/service account must be a DNS label")
        return selected

    @field_validator("platform")
    @classmethod
    def validate_platform(cls, value: str) -> str:
        selected = value.strip().lower()
        if selected not in {"kubernetes", "openshift"}:
            raise ValueError("platform must be kubernetes or openshift")
        return selected

    @field_validator("allowed_images")
    @classmethod
    def validate_images(cls, value: list[str]) -> list[str]:
        images = [image.strip() for image in value]
        if len(set(images)) != len(images):
            raise ValueError("controller allowed_images must not contain duplicates")
        if any(not _IMMUTABLE_IMAGE.fullmatch(image) for image in images):
            raise ValueError("controller images must use immutable sha256 digests")
        return images

    @field_validator("secret_keys")
    @classmethod
    def validate_secret_keys(cls, value: list[str]) -> list[str]:
        keys = [item.strip() for item in value]
        if any(not item for item in keys) or len(keys) != len(set(keys)):
            raise ValueError("controller secret_keys must be non-empty and unique")
        return keys

    @field_validator(
        "max_timeout_seconds",
        "max_input_bytes",
        "max_output_bytes",
        "max_workspace_bytes",
        "max_memory_bytes",
        "max_process_count",
        "ttl_seconds_after_finished",
        "poll_interval_seconds",
    )
    @classmethod
    def validate_positive_limit(cls, value: float | int) -> float | int:
        if value <= 0:
            raise ValueError("controller limits must be greater than zero")
        return value

    @model_validator(mode="after")
    def validate_secret_configuration(self) -> ControllerConfig:
        if self.secret_keys and not self.secret_name:
            raise ValueError("secret_name is required when secret_keys are configured")
        return self


class SecretReference(StrictModel):
    secret_ref: str


class ExecutionLimits(StrictModel):
    timeout_seconds: float
    max_output_bytes: int
    max_workspace_bytes: int
    memory_bytes: int | None = None
    process_count: int | None = None


class IsolationPolicy(StrictModel):
    network: str
    network_policy_enforced: bool
    process_limit_enforced: bool
    read_only_root: bool
    run_as_non_root: bool
    drop_all_capabilities: bool
    allow_privilege_escalation: bool
    seccomp_profile: str
    host_mounts: bool
    host_network: bool
    host_pid: bool
    host_ipc: bool
    automount_service_account_token: bool


class ExecutionRequest(StrictModel):
    execution_id: str
    image: str
    command: str | None = None
    arguments: list[str] = Field(default_factory=list)
    environment: dict[str, str | SecretReference] = Field(default_factory=dict)
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
    def validate_environment(
        cls, value: dict[str, str | SecretReference]
    ) -> dict[str, str | SecretReference]:
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


class KubernetesApiRunner:
    """Create bounded Jobs using namespace-scoped Kubernetes API credentials."""

    def __init__(
        self,
        config: ControllerConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        token: str | None = None,
    ) -> None:
        self.config = config
        if token is None:
            token = Path(config.token_file).read_text(encoding="utf-8").strip()
        if not token:
            raise ValueError("Kubernetes controller token is empty")
        verify: ssl.SSLContext | bool
        verify = (
            False if transport is not None else ssl.create_default_context(cafile=config.ca_file)
        )
        self._client = httpx.AsyncClient(
            base_url=config.api_server,
            headers={"Authorization": f"Bearer {token}"},
            verify=verify,
            transport=transport,
            timeout=10.0,
            trust_env=False,
        )
        self._active: set[str] = set()

    async def ready(self) -> bool:
        path = self._jobs_path()
        try:
            response = await self._client.get(path, params={"limit": "1"})
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        self._validate_request(request)
        job_name = _job_name(request.execution_id)
        started = time.perf_counter()
        self._active.add(request.execution_id)
        try:
            response = await self._client.post(
                self._jobs_path(), json=build_job_manifest(self.config, request)
            )
            if response.status_code == 409:
                raise ControllerFailure(
                    "sandbox_process_error",
                    "an execution with this id already exists",
                    409,
                )
            self._require_success(response, "failed to create sandbox job")

            try:
                async with asyncio.timeout(request.limits.timeout_seconds + 5.0):
                    phase = await self._wait_for_terminal(job_name)
            except TimeoutError as exc:
                await self._delete_job(job_name)
                raise ControllerFailure("sandbox_timeout", "sandbox execution timed out") from exc

            stdout = await self._read_logs(job_name, request.limits.max_output_bytes)
            if phase != "succeeded":
                raise ControllerFailure(
                    "sandbox_process_error",
                    "sandbox job exited with a non-zero status",
                )
            return ExecutionResult(
                exit_code=0,
                stdout=stdout,
                stderr="",
                duration=time.perf_counter() - started,
            )
        except asyncio.CancelledError:
            await self._delete_job(job_name)
            raise
        finally:
            self._active.discard(request.execution_id)
            await self._delete_job(job_name, ignore_errors=True)

    async def cancel(self, execution_id: str) -> None:
        await self._delete_job(_job_name(execution_id), ignore_errors=True)

    async def shutdown(self) -> None:
        for execution_id in tuple(self._active):
            await self.cancel(execution_id)
        self._active.clear()
        await self._client.aclose()

    def _validate_request(self, request: ExecutionRequest) -> None:
        if request.image not in self.config.allowed_images:
            raise ControllerFailure(
                "sandbox_policy_error",
                "container image is not controller-approved",
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
        if limits.process_count is not None:
            if not self.config.process_limit_enforced:
                self._policy_error("process_limit_enforced")
            if limits.process_count <= 0 or limits.process_count > self.config.max_process_count:
                self._limit_error("process_count")

        isolation = request.isolation
        expected = {
            "network": "denied",
            "network_policy_enforced": self.config.network_policy_enforced,
            "process_limit_enforced": self.config.process_limit_enforced,
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
        }
        actual = isolation.model_dump()
        for key, expected_value in expected.items():
            if actual[key] != expected_value:
                self._policy_error(key)
        if isolation.network == "denied" and not self.config.network_policy_enforced:
            self._policy_error("network_policy_enforced")

        for value in request.environment.values():
            if isinstance(value, SecretReference):
                if not self.config.secret_name or value.secret_ref not in self.config.secret_keys:
                    self._policy_error("secret_ref")

    async def _wait_for_terminal(self, job_name: str) -> str:
        path = f"{self._jobs_path()}/{quote(job_name, safe='')}"
        while True:
            response = await self._client.get(path)
            self._require_success(response, "failed to inspect sandbox job")
            body = response.json()
            status = body.get("status", {}) if isinstance(body, Mapping) else {}
            if int(status.get("succeeded", 0) or 0) > 0:
                return "succeeded"
            if int(status.get("failed", 0) or 0) > 0:
                return "failed"
            await asyncio.sleep(self.config.poll_interval_seconds)

    async def _read_logs(self, job_name: str, limit: int) -> str:
        pods_path = f"/api/v1/namespaces/{quote(self.config.namespace, safe='')}/pods"
        response = await self._client.get(
            pods_path,
            params={"labelSelector": f"job-name={job_name}", "limit": "1"},
        )
        self._require_success(response, "failed to locate sandbox workload")
        body = response.json()
        items = body.get("items", []) if isinstance(body, Mapping) else []
        if not items:
            raise ControllerFailure(
                "sandbox_process_error", "sandbox workload logs are unavailable"
            )
        metadata = items[0].get("metadata", {})
        pod_name = metadata.get("name")
        if not isinstance(pod_name, str) or not pod_name:
            raise ControllerFailure(
                "sandbox_process_error", "sandbox workload logs are unavailable"
            )
        path = (
            f"/api/v1/namespaces/{quote(self.config.namespace, safe='')}/pods/"
            f"{quote(pod_name, safe='')}/log"
        )
        chunks: list[bytes] = []
        used = 0
        async with self._client.stream("GET", path) as log_response:
            self._require_success(log_response, "failed to read sandbox logs")
            async for chunk in log_response.aiter_bytes():
                used += len(chunk)
                if used > limit:
                    raise ControllerFailure("sandbox_output_limit", "sandbox output limit exceeded")
                chunks.append(chunk)
        return b"".join(chunks).decode("utf-8", errors="replace")

    async def _delete_job(self, job_name: str, *, ignore_errors: bool = False) -> None:
        path = f"{self._jobs_path()}/{quote(job_name, safe='')}"
        try:
            response = await self._client.request(
                "DELETE",
                path,
                json={
                    "apiVersion": "v1",
                    "kind": "DeleteOptions",
                    "propagationPolicy": "Background",
                    "gracePeriodSeconds": 0,
                },
            )
        except httpx.HTTPError:
            if ignore_errors:
                return
            raise
        if response.status_code not in {200, 202, 404} and not ignore_errors:
            self._require_success(response, "failed to delete sandbox job")

    def _jobs_path(self) -> str:
        namespace = quote(self.config.namespace, safe="")
        return f"/apis/batch/v1/namespaces/{namespace}/jobs"

    @staticmethod
    def _require_success(response: httpx.Response, message: str) -> None:
        if response.status_code >= 400:
            raise ControllerFailure("sandbox_process_error", message)

    @staticmethod
    def _limit_error(field: str) -> None:
        raise ControllerFailure(
            "sandbox_resource_limit",
            f"requested {field} exceeds the controller limit",
            422,
        )

    @staticmethod
    def _policy_error(field: str) -> None:
        raise ControllerFailure(
            "sandbox_policy_error",
            f"requested {field} violates controller policy",
            422,
        )


def build_job_manifest(config: ControllerConfig, request: ExecutionRequest) -> dict[str, Any]:
    """Build the only Job shape the controller is allowed to submit."""

    job_name = _job_name(request.execution_id)
    container: dict[str, Any] = {
        "name": "sandbox",
        "image": request.image,
        "imagePullPolicy": "IfNotPresent",
        "env": _environment(config, request),
        "securityContext": {
            "allowPrivilegeEscalation": False,
            "privileged": False,
            "readOnlyRootFilesystem": True,
            "runAsNonRoot": True,
            "capabilities": {"drop": ["ALL"]},
            "seccompProfile": {"type": "RuntimeDefault"},
        },
        "resources": {
            "requests": {
                "cpu": config.cpu_limit,
                "memory": _quantity(request.limits.memory_bytes or config.max_memory_bytes),
                "ephemeral-storage": _quantity(request.limits.max_workspace_bytes),
            },
            "limits": {
                "cpu": config.cpu_limit,
                "memory": _quantity(request.limits.memory_bytes or config.max_memory_bytes),
                "ephemeral-storage": _quantity(request.limits.max_workspace_bytes),
            },
        },
        "volumeMounts": [
            {"name": "workspace", "mountPath": "/workspace"},
            {"name": "tmp", "mountPath": "/tmp"},
        ],
        "workingDir": "/workspace",
    }
    if request.command is not None:
        container["command"] = [request.command]
    if request.arguments:
        container["args"] = request.arguments

    active_deadline = max(1, math.ceil(request.limits.timeout_seconds))
    labels = {
        "app.kubernetes.io/name": "open-workflow-agent-sandbox",
        "openworkflow.agent/execution": _execution_label(request.execution_id),
    }
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": job_name, "namespace": config.namespace, "labels": labels},
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": active_deadline,
            "ttlSecondsAfterFinished": config.ttl_seconds_after_finished,
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "restartPolicy": "Never",
                    "serviceAccountName": config.workload_service_account,
                    "automountServiceAccountToken": False,
                    "enableServiceLinks": False,
                    "hostNetwork": False,
                    "hostPID": False,
                    "hostIPC": False,
                    "securityContext": {
                        "runAsNonRoot": True,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "containers": [container],
                    "volumes": [
                        {
                            "name": "workspace",
                            "emptyDir": {
                                "sizeLimit": _quantity(request.limits.max_workspace_bytes)
                            },
                        },
                        {
                            "name": "tmp",
                            "emptyDir": {
                                "sizeLimit": _quantity(
                                    max(1, request.limits.max_workspace_bytes // 4)
                                )
                            },
                        },
                    ],
                },
            },
        },
    }


def create_app(
    config: ControllerConfig | None = None,
    *,
    runner: ExecutionRunner | None = None,
) -> FastAPI:
    selected = config or ControllerConfig.from_environment()
    execution_runner = runner or KubernetesApiRunner(selected)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await execution_runner.shutdown()

    app = FastAPI(title="Open Workflow Agent Kubernetes Sandbox Controller", lifespan=lifespan)

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready() -> JSONResponse:
        healthy = await execution_runner.ready()
        return JSONResponse(
            status_code=200 if healthy else 503,
            content={"status": "ok" if healthy else "unavailable"},
        )

    @app.post("/v1/executions")
    async def execute(request: ExecutionRequest) -> JSONResponse:
        try:
            result = await execution_runner.execute(request)
            return JSONResponse(
                status_code=200,
                content={
                    "exit_code": result.exit_code,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "duration": result.duration,
                },
            )
        except ControllerFailure as exc:
            return _failure(exc)

    @app.delete("/v1/executions/{execution_id}")
    async def cancel(execution_id: str) -> JSONResponse:
        try:
            await execution_runner.cancel(execution_id)
            return JSONResponse(status_code=204, content=None)
        except ControllerFailure as exc:
            return _failure(exc)

    return app


def _environment(config: ControllerConfig, request: ExecutionRequest) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for name, value in request.environment.items():
        if isinstance(value, SecretReference):
            assert config.secret_name is not None
            values.append(
                {
                    "name": name,
                    "valueFrom": {
                        "secretKeyRef": {
                            "name": config.secret_name,
                            "key": value.secret_ref,
                            "optional": False,
                        }
                    },
                }
            )
        else:
            values.append({"name": name, "value": value})
    return values


def _quantity(value: int) -> str:
    return str(value)


def _execution_label(execution_id: str) -> str:
    return hashlib.sha256(execution_id.encode("utf-8")).hexdigest()[:32]


def _job_name(execution_id: str) -> str:
    return f"owa-{hashlib.sha256(execution_id.encode('utf-8')).hexdigest()[:20]}"


def _csv(name: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, "").split(",") if item.strip()]


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _failure(exc: ControllerFailure) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


def create_default_app() -> FastAPI:
    return create_app()
