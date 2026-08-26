from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

_CONTROLLER_SRC = Path(__file__).parents[2] / "sandbox-controller" / "src"
sys.path.insert(0, str(_CONTROLLER_SRC))

from open_workflow_agent_sandbox_controller.app import (  # noqa: E402
    ControllerConfig,
    DockerCliRunner,
    ExecutionRequest,
    ExecutionResult,
    create_app,
)

_IMAGE = "registry.example/worker@sha256:" + ("a" * 64)


def _config() -> ControllerConfig:
    return ControllerConfig(
        docker_binary="/usr/local/bin/docker",
        docker_socket="/var/run/docker.sock",
        allowed_images=[_IMAGE],
    )


def _request(**overrides: object) -> ExecutionRequest:
    value: dict[str, object] = {
        "execution_id": "execution-1",
        "image": _IMAGE,
        "command": "python",
        "arguments": ["-c", "print('ok')"],
        "stdin": None,
        "environment": {"SAFE": "value"},
        "limits": {
            "timeout_seconds": 30,
            "max_output_bytes": 1024,
            "max_workspace_bytes": 4096,
            "memory_bytes": 64 * 1024 * 1024,
            "process_count": 16,
        },
        "isolation": {
            "run_as_user": "65532:65532",
            "network": "denied",
            "read_only_root": True,
            "drop_all_capabilities": True,
            "no_new_privileges": True,
            "host_mounts": False,
            "host_network": False,
        },
    }
    value.update(overrides)
    return ExecutionRequest.model_validate(value)


def test_controller_config_requires_digest_pinned_images_and_non_root_user() -> None:
    with pytest.raises(ValidationError, match="immutable sha256"):
        ControllerConfig(allowed_images=["registry.example/worker:latest"])
    with pytest.raises(ValidationError, match="non-root numeric"):
        ControllerConfig(allowed_images=[_IMAGE], run_as_user="0:0")


def test_docker_cli_argv_forces_hardened_execution_without_host_access(tmp_path: Path) -> None:
    runner = DockerCliRunner(_config())
    environment_file = tmp_path / "environment"
    environment_file.write_text("SAFE=value\n", encoding="utf-8")
    argv = runner._docker_run_argv(  # noqa: SLF001 - contract-level security assertion
        _request(),
        "owa-sbx-test",
        environment_file,
    )
    joined = " ".join(argv)

    assert argv[0] == "/usr/local/bin/docker"
    assert "run" in argv
    assert "--pull=never" in argv
    assert "--read-only" in argv
    assert "--network none" in joined
    assert "--cap-drop ALL" in joined
    assert "--security-opt no-new-privileges" in joined
    assert "--user 65532:65532" in joined
    assert "--pids-limit 16" in joined
    assert "--memory 67108864" in joined
    assert "--env-file" in argv
    assert _IMAGE in argv
    assert "--privileged" not in argv
    assert "--volume" not in argv
    assert "--mount" not in argv
    assert "--publish" not in argv
    assert "--network host" not in joined


class FakeRunner:
    def __init__(self) -> None:
        self.executions: list[ExecutionRequest] = []
        self.cancelled: list[str] = []
        self.shutdown_called = False

    async def ready(self) -> bool:
        return True

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        self.executions.append(request)
        return ExecutionResult(exit_code=0, stdout="ok\n", stderr="", duration=0.01)

    async def cancel(self, execution_id: str) -> None:
        self.cancelled.append(execution_id)

    async def shutdown(self) -> None:
        self.shutdown_called = True


def test_controller_api_exposes_only_bounded_execution_contract() -> None:
    runner = FakeRunner()
    app = create_app(config=_config(), runner=runner)
    with TestClient(app) as client:
        assert client.get("/health/ready").status_code == 200
        capabilities = client.get("/v1/capabilities").json()
        assert capabilities["pullPolicy"] == "never"
        assert capabilities["network"] == "denied"
        assert capabilities["hostMounts"] is False
        assert capabilities["hostNetwork"] is False
        assert capabilities["privileged"] is False

        response = client.post("/v1/executions", json=_request().model_dump())
        assert response.status_code == 200
        assert response.json() == {
            "exit_code": 0,
            "stdout": "ok\n",
            "stderr": "",
            "duration": 0.01,
        }
        assert len(runner.executions) == 1

        cancel = client.delete("/v1/executions/execution-1")
        assert cancel.status_code == 200
        assert runner.cancelled == ["execution-1"]
    assert runner.shutdown_called is True


def test_controller_request_schema_rejects_unknown_escape_hatches() -> None:
    payload = _request().model_dump()
    payload["mounts"] = [{"source": "/", "target": "/host"}]
    app = create_app(config=_config(), runner=FakeRunner())
    with TestClient(app) as client:
        response = client.post("/v1/executions", json=payload)
    assert response.status_code == 422
