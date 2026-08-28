from __future__ import annotations

import json

import httpx
import pytest
from open_workflow_agent.config import SandboxConfig
from open_workflow_agent.errors import SandboxPolicyError, UnsupportedWorkflowFeature
from open_workflow_agent.sandbox import (
    SandboxExecutionRequest,
    SandboxSecretReference,
    validate_sandbox_capabilities,
)
from open_workflow_agent.sandbox.backends.docker import DockerSandboxBackend
from pydantic import ValidationError

_IMAGE = "registry.example/worker@sha256:" + ("a" * 64)


def _workflow(container: dict[str, object]) -> dict[str, object]:
    return {
        "document": {
            "dsl": "1.0.3",
            "namespace": "docker-sandbox-test",
            "name": "docker-sandbox-test",
            "version": "1.0.0",
        },
        "do": [{"execute": {"run": {"container": container}}}],
    }


def _config() -> SandboxConfig:
    return SandboxConfig(
        enabled=True,
        backend="docker",
        secret_environment=["SANDBOX_TEST_SECRET"],
        docker={"allowed_images": [_IMAGE]},
    )


def test_docker_policy_requires_approved_digest_images_and_non_root_user() -> None:
    with pytest.raises(ValidationError, match="deployment-approved image"):
        SandboxConfig(enabled=True, backend="docker")
    with pytest.raises(ValidationError, match="immutable sha256 digests"):
        SandboxConfig(
            enabled=True,
            backend="docker",
            docker={"allowed_images": ["registry.example/worker:latest"]},
        )
    with pytest.raises(ValidationError, match="non-root numeric"):
        SandboxConfig(
            enabled=True,
            backend="docker",
            docker={"allowed_images": [_IMAGE], "run_as_user": "0:0"},
        )


def test_run_container_is_enabled_only_for_safe_docker_policy() -> None:
    validate_sandbox_capabilities(
        _workflow(
            {
                "image": _IMAGE,
                "command": "python",
                "arguments": ["-c", "print('ok')"],
            }
        ),
        sandbox=_config(),
    )

    with pytest.raises(UnsupportedWorkflowFeature, match="container sandbox backend"):
        validate_sandbox_capabilities(
            _workflow({"image": _IMAGE}),
            sandbox=SandboxConfig(enabled=True),
        )
    with pytest.raises(UnsupportedWorkflowFeature, match="not deployment-approved"):
        validate_sandbox_capabilities(
            _workflow({"image": "registry.example/other@sha256:" + ("b" * 64)}),
            sandbox=_config(),
        )
    with pytest.raises(UnsupportedWorkflowFeature, match="port mappings"):
        validate_sandbox_capabilities(
            _workflow({"image": _IMAGE, "ports": {"8080": "8080"}}),
            sandbox=_config(),
        )
    with pytest.raises(UnsupportedWorkflowFeature, match="host volume"):
        validate_sandbox_capabilities(
            _workflow({"image": _IMAGE, "volumes": {"/host": "/container"}}),
            sandbox=_config(),
        )


@pytest.mark.asyncio
async def test_docker_backend_sends_only_bounded_controller_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SANDBOX_TEST_SECRET", "expected-secret")
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/executions"
        assert request.headers["content-type"] == "application/json"
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "exit_code": 0,
                "stdout": "ok\n",
                "stderr": "",
                "duration": 0.01,
            },
        )

    backend = DockerSandboxBackend(_config(), transport=httpx.MockTransport(handler))
    result = await backend.execute(
        SandboxExecutionRequest(
            execution_id="docker-success",
            kind="container",
            image=_IMAGE,
            command="python",
            arguments=("-c", "print('ok')"),
            environment=(("TOKEN", SandboxSecretReference("SANDBOX_TEST_SECRET")),),
        )
    )
    await backend.shutdown()

    assert result.stdout == "ok\n"
    assert captured["image"] == _IMAGE
    assert captured["environment"] == {"TOKEN": "expected-secret"}
    isolation = captured["isolation"]
    assert isinstance(isolation, dict)
    assert isolation == {
        "run_as_user": "65532:65532",
        "network": "denied",
        "read_only_root": True,
        "drop_all_capabilities": True,
        "no_new_privileges": True,
        "host_mounts": False,
        "host_network": False,
    }
    capabilities = backend.capabilities()
    assert capabilities["backend"] == "docker"
    assert capabilities["container"]["enabled"] is True
    assert capabilities["container"]["ports"] is False
    assert capabilities["container"]["volumes"] is False
    assert capabilities["hardIsolation"] is True
    assert capabilities["networkIsolation"] == "denied"
    assert capabilities["controllerTransport"] == "unix_socket"
    assert "/var/run/docker.sock" not in json.dumps(capabilities)


@pytest.mark.asyncio
async def test_docker_backend_rejects_unapproved_image_before_controller_call() -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500)

    backend = DockerSandboxBackend(_config(), transport=httpx.MockTransport(handler))
    with pytest.raises(SandboxPolicyError, match="not deployment-approved"):
        await backend.execute(
            SandboxExecutionRequest(
                execution_id="docker-rejected",
                kind="container",
                image="registry.example/other@sha256:" + ("b" * 64),
            )
        )
    await backend.shutdown()
    assert called is False


@pytest.mark.asyncio
async def test_controller_error_does_not_echo_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "controller-secret-that-must-not-be-in-errors"
    monkeypatch.setenv("SANDBOX_TEST_SECRET", secret)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={
                "error": {
                    "code": "sandbox_policy_error",
                    "message": f"untrusted controller detail {secret}",
                }
            },
        )

    backend = DockerSandboxBackend(_config(), transport=httpx.MockTransport(handler))
    with pytest.raises(SandboxPolicyError) as error:
        await backend.execute(
            SandboxExecutionRequest(
                execution_id="docker-error",
                kind="container",
                image=_IMAGE,
                environment=(("TOKEN", SandboxSecretReference("SANDBOX_TEST_SECRET")),),
            )
        )
    await backend.shutdown()
    assert secret not in str(error.value)
    assert secret not in repr(error.value.details)
