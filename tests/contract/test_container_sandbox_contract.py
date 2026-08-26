from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig, SandboxConfig
from open_workflow_agent.sandbox import (
    SandboxExecutionRequest,
    SandboxExecutionResult,
    SandboxManager,
    compile_sandbox_workflow,
)
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent_adk import AdkWorkflowEngine
from open_workflow_agent_langgraph import LangGraphWorkflowEngine

_IMAGE = "registry.example/worker@sha256:" + ("a" * 64)


class FakeContainerBackend:
    def __init__(self) -> None:
        self.requests: list[SandboxExecutionRequest] = []
        self.cancelled: list[str] = []

    async def execute(self, request: SandboxExecutionRequest) -> SandboxExecutionResult:
        self.requests.append(request)
        return SandboxExecutionResult(
            execution_id=request.execution_id,
            exit_code=0,
            stdout="container-ok\n",
            stderr="",
            duration=0.01,
        )

    async def cancel(self, execution_id: str) -> None:
        self.cancelled.append(execution_id)

    async def shutdown(self) -> None:
        return None

    def capabilities(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "backend": "docker",
            "container": {
                "enabled": True,
                "imagePolicy": "exact_digest_allowlist",
                "ports": False,
                "volumes": False,
            },
            "script": {"enabled": False, "runtimes": [], "externalSource": False},
            "shell": {"enabled": False},
            "cancellation": True,
            "resourceLimits": {
                "timeout": True,
                "outputBytes": True,
                "workspaceQuota": True,
                "memory": True,
                "processCount": True,
            },
            "filesystemIsolation": "isolated_root",
            "networkIsolation": "denied",
            "hardIsolation": True,
            "controllerTransport": "test",
        }


def _workflow() -> dict[str, object]:
    return {
        "document": {
            "dsl": "1.0.3",
            "namespace": "contract",
            "name": "container-sandbox",
            "version": "1.0.0",
        },
        "do": [
            {
                "execute": {
                    "run": {
                        "container": {
                            "image": _IMAGE,
                            "command": "python",
                            "arguments": ["-c", "print('container-ok')"],
                            "environment": {"MODE": "contract"},
                        }
                    }
                }
            }
        ],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("engine_name", "engine_type"),
    [("adk", AdkWorkflowEngine), ("langgraph", LangGraphWorkflowEngine)],
)
async def test_container_sandbox_has_cross_engine_output_and_lifecycle_parity(
    tmp_path: Path,
    engine_name: str,
    engine_type: type[AdkWorkflowEngine] | type[LangGraphWorkflowEngine],
) -> None:
    sandbox = SandboxConfig(
        enabled=True,
        backend="docker",
        docker={"allowed_images": [_IMAGE]},
    )
    services = RuntimeServices(
        RuntimeConfig(sandbox=sandbox),
        model=FakeModel({"response": "ok"}),
        database_root=tmp_path / engine_name,
    )
    backend = FakeContainerBackend()
    services.sandbox = SandboxManager(backend)
    engine = engine_type()
    await engine.initialize(services)
    try:
        plan = compile_sandbox_workflow(_workflow(), sandbox=sandbox)
        handle = services.invocations.create(
            engine=engine_name,
            session_id=None,
            user_id=None,
            workflow_name=plan.name,
            workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
        )
        result = await engine.invoke(plan, handle, {})

        assert result.status == "completed"
        assert result.output == {
            "exitCode": 0,
            "stdout": "container-ok\n",
            "stderr": "",
        }
        assert len(backend.requests) == 1
        request = backend.requests[0]
        assert request.kind == "container"
        assert request.image == _IMAGE
        assert request.command == "python"
        assert request.arguments == ("-c", "print('container-ok')")
        assert request.environment == (("MODE", "contract"),)
        assert request.invocation_id == handle.invocation_id
        assert request.task_reference == "/do/0/execute"

        sandbox_events = [
            event
            for event in services.events.events
            if event.event_type.startswith("SandboxExecution")
        ]
        assert [event.event_type for event in sandbox_events] == [
            "SandboxExecutionStarted",
            "SandboxExecutionCompleted",
        ]
        assert sandbox_events[0].execution_id == sandbox_events[1].execution_id
        assert sandbox_events[0].invocation_id == handle.invocation_id
        assert sandbox_events[1].duration == 0.01
    finally:
        await engine.shutdown()
        services.close()


def test_internal_and_docker_capabilities_remain_explicitly_distinct() -> None:
    internal = SandboxConfig(enabled=True)
    docker = SandboxConfig(
        enabled=True,
        backend="docker",
        docker={"allowed_images": [_IMAGE]},
    )

    internal_services = RuntimeServices(internal_config := RuntimeConfig(sandbox=internal))
    docker_services = RuntimeServices(docker_config := RuntimeConfig(sandbox=docker))
    try:
        internal_capabilities = internal_services.sandbox.capabilities()
        docker_capabilities = docker_services.sandbox.capabilities()
        assert internal_config.sandbox.backend == "internal"
        assert docker_config.sandbox.backend == "docker"
        assert internal_capabilities["container"]["enabled"] is False
        assert internal_capabilities["hardIsolation"] is False
        assert docker_capabilities["container"]["enabled"] is True
        assert docker_capabilities["hardIsolation"] is True
        assert docker_capabilities["networkIsolation"] == "denied"
    finally:
        internal_services.close()
        docker_services.close()
