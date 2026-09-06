from __future__ import annotations

import json

import httpx
import pytest
from open_workflow_agent.config import SandboxConfig
from open_workflow_agent.errors import (
    SandboxPolicyError,
    SandboxProcessError,
    SandboxTimeoutError,
    UnsupportedWorkflowFeature,
)
from open_workflow_agent.sandbox import (
    SandboxExecutionRequest,
    SandboxSecretReference,
    validate_sandbox_capabilities,
)
from open_workflow_agent.sandbox.backends.controller import (
    cancel_controller_execution,
    controller_error,
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


def test_controller_error_maps_known_and_unknown_error_codes() -> None:
    from open_workflow_agent.errors import (
        SandboxOutputLimitError,
        SandboxResourceLimitError,
        SandboxTimeoutError,
    )

    assert isinstance(
        controller_error(
            httpx.Response(422, json={"error": {"code": "sandbox_timeout"}}),
            backend_label="Docker",
        ),
        SandboxTimeoutError,
    )
    assert isinstance(
        controller_error(
            httpx.Response(422, json={"error": {"code": "sandbox_output_limit"}}),
            backend_label="Docker",
        ),
        SandboxOutputLimitError,
    )
    assert isinstance(
        controller_error(
            httpx.Response(422, json={"error": {"code": "sandbox_resource_limit"}}),
            backend_label="Docker",
        ),
        SandboxResourceLimitError,
    )
    assert isinstance(
        controller_error(httpx.Response(500, text="not-json"), backend_label="Docker"),
        SandboxProcessError,
    )


@pytest.mark.asyncio
async def test_controller_cancellation_accepts_idempotent_statuses_and_maps_failures():
    for status in (200, 202, 204, 404):
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _request, status=status: httpx.Response(status)),
            base_url="http://controller",
        )
        await cancel_controller_execution(
            client, backend_label="Docker", execution_id="execution-1"
        )
        await client.aclose()

    failing = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(500, json={"error": {"code": "sandbox_process_error"}})
        ),
        base_url="http://controller",
    )
    with pytest.raises(SandboxProcessError):
        await cancel_controller_execution(failing, backend_label="Docker", execution_id="bad")
    await failing.aclose()


@pytest.mark.asyncio
async def test_docker_backend_maps_timeout_http_and_malformed_responses():
    invalid = DockerSandboxBackend(
        _config(), transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={}))
    )
    with pytest.raises(SandboxProcessError, match="invalid response"):
        await invalid.execute(
            SandboxExecutionRequest(execution_id="invalid", kind="container", image=_IMAGE)
        )
    await invalid.shutdown()

    def timeout(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("controller timeout")

    timed = DockerSandboxBackend(_config(), transport=httpx.MockTransport(timeout))
    with pytest.raises(SandboxTimeoutError):
        await timed.execute(
            SandboxExecutionRequest(execution_id="timeout", kind="container", image=_IMAGE)
        )
    await timed.shutdown()

    unavailable = DockerSandboxBackend(
        _config(),
        transport=httpx.MockTransport(
            lambda _request: (_ for _ in ()).throw(httpx.ConnectError("down"))
        ),
    )
    with pytest.raises(SandboxProcessError, match="unavailable"):
        await unavailable.execute(
            SandboxExecutionRequest(execution_id="unavailable", kind="container", image=_IMAGE)
        )
    await unavailable.shutdown()


@pytest.mark.asyncio
async def test_docker_backend_rejects_disabled_wrong_kind_and_oversized_requests():
    disabled = DockerSandboxBackend(
        SandboxConfig(enabled=False, backend="docker", docker={"allowed_images": [_IMAGE]}),
        transport=httpx.MockTransport(lambda _request: httpx.Response(500)),
    )
    with pytest.raises(SandboxPolicyError, match="disabled"):
        await disabled.execute(
            SandboxExecutionRequest(execution_id="disabled", kind="container", image=_IMAGE)
        )
    await disabled.shutdown()

    wrong_kind = DockerSandboxBackend(
        _config(), transport=httpx.MockTransport(lambda _request: httpx.Response(500))
    )
    with pytest.raises(SandboxPolicyError, match="only run.container"):
        await wrong_kind.execute(SandboxExecutionRequest(execution_id="wrong", kind="script"))
    await wrong_kind.shutdown()

    tiny = SandboxConfig(
        enabled=True,
        backend="docker",
        max_input_bytes=1,
        docker={"allowed_images": [_IMAGE]},
    )
    oversized = DockerSandboxBackend(
        tiny, transport=httpx.MockTransport(lambda _request: httpx.Response(500))
    )
    with pytest.raises(SandboxPolicyError, match="input limit"):
        await oversized.execute(
            SandboxExecutionRequest(execution_id="oversized", kind="container", image=_IMAGE)
        )
    await oversized.shutdown()


@pytest.mark.asyncio
async def test_docker_backend_cleanup_and_environment_edges(monkeypatch):
    backend = DockerSandboxBackend(
        _config(), transport=httpx.MockTransport(lambda _: httpx.Response(204))
    )
    assert await backend.cancel("not-active") is None
    assert backend._resolve_environment(  # noqa: SLF001 - backend contract edge
        SandboxExecutionRequest(
            execution_id="plain", kind="container", image=_IMAGE, environment=(("SAFE", "value"),)
        )
    ) == {"SAFE": "value"}
    with pytest.raises(SandboxPolicyError, match="not deployment-approved"):
        backend._resolve_environment(  # noqa: SLF001
            SandboxExecutionRequest(
                execution_id="bad-secret",
                kind="container",
                image=_IMAGE,
                environment=(("TOKEN", SandboxSecretReference("NOT_APPROVED")),),
            )
        )
    monkeypatch.delenv("SANDBOX_TEST_SECRET", raising=False)
    with pytest.raises(SandboxPolicyError, match="unavailable"):
        backend._resolve_environment(  # noqa: SLF001
            SandboxExecutionRequest(
                execution_id="missing-secret",
                kind="container",
                image=_IMAGE,
                environment=(("TOKEN", SandboxSecretReference("SANDBOX_TEST_SECRET")),),
            )
        )
    backend._active.add("active")  # noqa: SLF001
    await backend.cancel("active")
    backend._active.add("shutdown")  # noqa: SLF001
    await backend.shutdown()
