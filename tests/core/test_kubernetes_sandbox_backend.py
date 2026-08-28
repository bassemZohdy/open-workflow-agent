from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from open_workflow_agent.config import SandboxConfig
from open_workflow_agent.errors import SandboxPolicyError, SandboxTimeoutError
from open_workflow_agent.kubernetes_sandbox import KubernetesSandboxBackend
from open_workflow_agent.sandbox import SandboxExecutionRequest, SandboxSecretReference
from pydantic import ValidationError

_IMAGE = "registry.example/worker@sha256:" + ("a" * 64)


def _config(**overrides: object) -> SandboxConfig:
    kubernetes: dict[str, object] = {
        "allowed_images": [_IMAGE],
        "network_policy_enforced": True,
        "process_limit_enforced": True,
        "secret_name": "owa-sandbox-secrets",
        "secret_keys": ["API_TOKEN"],
    }
    kubernetes.update(overrides)
    return SandboxConfig(enabled=True, backend="kubernetes", kubernetes=kubernetes)


def _request(**overrides: object) -> SandboxExecutionRequest:
    value: dict[str, object] = {
        "execution_id": "execution-1",
        "kind": "container",
        "image": _IMAGE,
        "command": "python",
        "arguments": ("-c", "print('ok')"),
        "environment": (
            ("MODE", "test"),
            ("TOKEN", SandboxSecretReference("API_TOKEN")),
        ),
    }
    value.update(overrides)
    return SandboxExecutionRequest(**value)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_backend_sends_only_bounded_controller_contract() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "127.0.0.1"
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"exit_code": 0, "stdout": "ok\n", "stderr": "", "duration": 0.2},
        )

    backend = KubernetesSandboxBackend(_config(), transport=httpx.MockTransport(handler))
    try:
        result = await backend.execute(_request())
    finally:
        await backend.shutdown()

    assert result.stdout == "ok\n"
    assert captured["image"] == _IMAGE
    assert captured["environment"] == {
        "MODE": "test",
        "TOKEN": {"secret_ref": "API_TOKEN"},
    }
    isolation = captured["isolation"]
    assert isinstance(isolation, dict)
    assert isolation["network"] == "denied"
    assert isolation["network_policy_enforced"] is True
    assert isolation["read_only_root"] is True
    assert isolation["run_as_non_root"] is True
    assert isolation["drop_all_capabilities"] is True
    assert isolation["allow_privilege_escalation"] is False
    assert isolation["host_mounts"] is False
    assert isolation["host_network"] is False
    assert isolation["host_pid"] is False
    assert isolation["host_ipc"] is False
    assert isolation["automount_service_account_token"] is False
    assert "namespace" not in captured
    assert "service_account" not in captured


def test_enabled_backend_fails_closed_without_network_policy_attestation() -> None:
    with pytest.raises(ValidationError, match="network_policy_enforced"):
        SandboxConfig(
            enabled=True,
            backend="kubernetes",
            process_count=None,
            kubernetes={
                "allowed_images": [_IMAGE],
                "network_policy_enforced": False,
            },
        )


def test_enabled_backend_fails_closed_without_process_limit_attestation() -> None:
    with pytest.raises(ValidationError, match="process_limit_enforced"):
        SandboxConfig(
            enabled=True,
            backend="kubernetes",
            kubernetes={
                "allowed_images": [_IMAGE],
                "network_policy_enforced": True,
                "process_limit_enforced": False,
            },
        )


def test_controller_endpoint_must_be_loopback() -> None:
    with pytest.raises(ValidationError, match="loopback"):
        SandboxConfig(
            kubernetes={
                "controller_url": "https://controller.example.test:8090",
            }
        )


def test_digest_allowlist_is_enforced() -> None:
    with pytest.raises(ValidationError, match="immutable sha256"):
        SandboxConfig(kubernetes={"allowed_images": ["registry.example/worker:latest"]})


@pytest.mark.asyncio
async def test_backend_rejects_stdin_and_unapproved_secret() -> None:
    backend = KubernetesSandboxBackend(
        _config(),
        transport=httpx.MockTransport(lambda _request: httpx.Response(500)),
    )
    try:
        with pytest.raises(SandboxPolicyError, match="stdin"):
            await backend.execute(_request(stdin="secret input"))
        with pytest.raises(SandboxPolicyError, match="secret reference"):
            await backend.execute(
                _request(environment=(("TOKEN", SandboxSecretReference("OTHER_TOKEN")),))
            )
    finally:
        await backend.shutdown()


@pytest.mark.asyncio
async def test_timeout_requests_controller_cleanup() -> None:
    paths: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        paths.append((request.method, request.url.path))
        if request.method == "POST":
            raise httpx.ReadTimeout("timeout", request=request)
        return httpx.Response(204)

    backend = KubernetesSandboxBackend(_config(), transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(SandboxTimeoutError):
            await backend.execute(_request())
    finally:
        await backend.shutdown()

    assert paths == [
        ("POST", "/v1/executions"),
        ("DELETE", "/v1/executions/execution-1"),
    ]


@pytest.mark.asyncio
async def test_execution_posts_json_content_type() -> None:
    """The real controller is FastAPI: it parses the body only as JSON."""

    seen: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["content_type"] = request.headers.get("content-type", "")
        return httpx.Response(
            200, json={"exit_code": 0, "stdout": "", "stderr": "", "duration": 0.1}
        )

    backend = KubernetesSandboxBackend(_config(), transport=httpx.MockTransport(handler))
    try:
        await backend.execute(_request())
    finally:
        await backend.shutdown()

    assert seen["content_type"] == "application/json"


@pytest.mark.asyncio
async def test_explicit_cancellation_reaches_controller() -> None:
    posted = asyncio.Event()
    released = asyncio.Event()
    paths: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        paths.append((request.method, request.url.path))
        if request.method == "POST":
            posted.set()
            await released.wait()
            return httpx.Response(
                200,
                json={"exit_code": 0, "stdout": "", "stderr": "", "duration": 0.1},
            )
        released.set()
        return httpx.Response(204)

    backend = KubernetesSandboxBackend(_config(), transport=httpx.MockTransport(handler))
    invocation = asyncio.create_task(backend.execute(_request()))
    try:
        await asyncio.wait_for(posted.wait(), timeout=1)
        await backend.cancel("execution-1")
        await asyncio.wait_for(invocation, timeout=1)
    finally:
        await backend.shutdown()

    assert ("DELETE", "/v1/executions/execution-1") in paths


def test_capabilities_do_not_expose_native_identifiers() -> None:
    backend = KubernetesSandboxBackend(
        _config(platform="openshift"),
        transport=httpx.MockTransport(lambda _request: httpx.Response(200)),
    )
    capabilities = backend.capabilities()
    assert capabilities["backend"] == "kubernetes"
    assert capabilities["platform"] == "openshift"
    assert capabilities["container"]["enabled"] is True
    assert capabilities["container"]["imagePolicy"] == "exact_digest_allowlist"
    assert capabilities["networkIsolation"] == "denied"
    assert capabilities["hardIsolation"] is True
    assert capabilities["nativeIdentifiersExposed"] is False
