from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

_CONTROLLER_SRC = Path(__file__).parents[2] / "kubernetes-sandbox-controller" / "src"
sys.path.insert(0, str(_CONTROLLER_SRC))

from open_workflow_agent_kubernetes_sandbox_controller.app import (  # noqa: E402
    ControllerConfig,
    ControllerFailure,
    ExecutionRequest,
    ExecutionResult,
    KubernetesApiRunner,
    build_job_manifest,
    create_app,
)

_IMAGE = "registry.example/worker@sha256:" + ("a" * 64)


def _config(**overrides: object) -> ControllerConfig:
    value: dict[str, object] = {
        "allowed_images": [_IMAGE],
        "network_policy_enforced": True,
        "process_limit_enforced": True,
        "secret_name": "owa-sandbox-secrets",
        "secret_keys": ["API_TOKEN"],
        "poll_interval_seconds": 0.001,
    }
    value.update(overrides)
    return ControllerConfig(**value)


def _request(**overrides: object) -> ExecutionRequest:
    value: dict[str, Any] = {
        "execution_id": "execution-1",
        "image": _IMAGE,
        "command": "python",
        "arguments": ["-c", "print('ok')"],
        "environment": {
            "MODE": "test",
            "TOKEN": {"secret_ref": "API_TOKEN"},
        },
        "limits": {
            "timeout_seconds": 5,
            "max_output_bytes": 1024,
            "max_workspace_bytes": 4096,
            "memory_bytes": 1024 * 1024,
            "process_count": 16,
        },
        "isolation": {
            "network": "denied",
            "network_policy_enforced": True,
            "process_limit_enforced": True,
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
    value.update(overrides)
    return ExecutionRequest.model_validate(value)


def test_job_manifest_is_restricted_and_arbitrary_uid_compatible() -> None:
    manifest = build_job_manifest(_config(platform="openshift"), _request())
    spec = manifest["spec"]
    pod = spec["template"]["spec"]
    container = pod["containers"][0]

    assert manifest["kind"] == "Job"
    assert spec["backoffLimit"] == 0
    assert spec["activeDeadlineSeconds"] == 5
    assert spec["ttlSecondsAfterFinished"] == 60
    assert pod["serviceAccountName"] == "owa-sandbox-workload"
    assert pod["automountServiceAccountToken"] is False
    assert pod["enableServiceLinks"] is False
    assert pod["hostNetwork"] is False
    assert pod["hostPID"] is False
    assert pod["hostIPC"] is False
    assert pod["securityContext"] == {
        "runAsNonRoot": True,
        "seccompProfile": {"type": "RuntimeDefault"},
    }
    assert "runAsUser" not in pod["securityContext"]
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert container["securityContext"]["privileged"] is False
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["runAsNonRoot"] is True
    assert container["securityContext"]["capabilities"] == {"drop": ["ALL"]}
    assert "runAsUser" not in container["securityContext"]
    assert all("hostPath" not in volume for volume in pod["volumes"])
    assert all("sizeLimit" in volume["emptyDir"] for volume in pod["volumes"])
    assert container["resources"]["limits"]["memory"] == str(1024 * 1024)
    assert container["resources"]["limits"]["ephemeral-storage"] == "4096"
    token = next(item for item in container["env"] if item["name"] == "TOKEN")
    assert token["valueFrom"]["secretKeyRef"] == {
        "name": "owa-sandbox-secrets",
        "key": "API_TOKEN",
        "optional": False,
    }


def test_controller_rejects_mutable_images() -> None:
    with pytest.raises(ValidationError, match="immutable sha256"):
        ControllerConfig(allowed_images=["registry.example/worker:latest"])


def test_controller_rejects_policy_downgrade() -> None:
    runner = KubernetesApiRunner(
        _config(),
        token="test",
        transport=httpx.MockTransport(lambda _request: httpx.Response(200)),
    )
    request = _request(isolation={**_request().isolation.model_dump(), "read_only_root": False})
    with pytest.raises(ControllerFailure, match="read_only_root"):
        runner._validate_request(request)


@pytest.mark.asyncio
async def test_api_runner_creates_waits_reads_logs_and_deletes() -> None:
    methods: list[tuple[str, str]] = []
    created: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        methods.append((request.method, request.url.path))
        if request.method == "POST":
            created.update(__import__("json").loads(request.content))
            return httpx.Response(201, json={"metadata": {"name": "hidden"}})
        if request.method == "GET" and request.url.path.endswith("/jobs/owa-4cba87b64224d4ac0f0e"):
            return httpx.Response(200, json={"status": {"succeeded": 1}})
        if request.method == "GET" and request.url.path.endswith("/pods"):
            return httpx.Response(200, json={"items": [{"metadata": {"name": "pod-native-id"}}]})
        if request.method == "GET" and request.url.path.endswith("/log"):
            return httpx.Response(200, content=b"ok\n")
        if request.method == "DELETE":
            return httpx.Response(200, json={})
        return httpx.Response(404)

    runner = KubernetesApiRunner(_config(), token="test", transport=httpx.MockTransport(handler))
    try:
        result = await runner.execute(_request())
    finally:
        await runner.shutdown()

    assert result.exit_code == 0
    assert result.stdout == "ok\n"
    assert created["kind"] == "Job"
    assert created["metadata"]["namespace"] == "owa-sandbox"
    assert methods[0][0] == "POST"
    assert methods[-1][0] == "DELETE"


@pytest.mark.asyncio
async def test_controller_cancel_is_idempotent_and_uses_derived_name() -> None:
    paths: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(404)

    runner = KubernetesApiRunner(_config(), token="test", transport=httpx.MockTransport(handler))
    try:
        await runner.cancel("execution-1")
    finally:
        await runner.shutdown()

    assert len(paths) == 1
    assert paths[0].startswith("/apis/batch/v1/namespaces/owa-sandbox/jobs/owa-")


class FakeRunner:
    def __init__(self) -> None:
        self.cancelled: list[str] = []
        self.closed = False

    async def ready(self) -> bool:
        return True

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        assert request.execution_id == "execution-1"
        return ExecutionResult(0, "ok\n", "", 0.01)

    async def cancel(self, execution_id: str) -> None:
        self.cancelled.append(execution_id)

    async def shutdown(self) -> None:
        self.closed = True


def test_http_api_exposes_only_common_execution_contract() -> None:
    runner = FakeRunner()
    app = create_app(_config(), runner=runner)
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 200
        response = client.post("/v1/executions", json=_request().model_dump(mode="json"))
        assert response.status_code == 200
        assert response.json() == {
            "exit_code": 0,
            "stdout": "ok\n",
            "stderr": "",
            "duration": 0.01,
        }
        cancelled = client.delete("/v1/executions/execution-1")
        assert cancelled.status_code == 204
        assert runner.cancelled == ["execution-1"]
    assert runner.closed is True
