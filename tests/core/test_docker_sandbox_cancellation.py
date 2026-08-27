from __future__ import annotations

import asyncio

import httpx
import pytest
from open_workflow_agent.config import SandboxConfig
from open_workflow_agent.docker_sandbox import DockerSandboxBackend
from open_workflow_agent.sandbox import SandboxExecutionRequest

_IMAGE = "registry.example/worker@sha256:" + ("a" * 64)


class BlockingControllerTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled: list[str] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            self.started.set()
            await asyncio.Event().wait()
            raise AssertionError("blocking controller POST should be cancelled")
        if request.method == "DELETE":
            self.cancelled.append(request.url.path)
            return httpx.Response(200, json={"status": "cancelled"})
        raise AssertionError(f"unexpected controller method: {request.method}")


@pytest.mark.asyncio
async def test_cancelled_backend_request_explicitly_cancels_controller_execution() -> None:
    transport = BlockingControllerTransport()
    config = SandboxConfig(
        enabled=True,
        backend="docker",
        docker={"allowed_images": [_IMAGE]},
    )
    backend = DockerSandboxBackend(config, transport=transport)
    task = asyncio.create_task(
        backend.execute(
            SandboxExecutionRequest(
                execution_id="cancel-me",
                kind="container",
                image=_IMAGE,
            )
        )
    )
    await asyncio.wait_for(transport.started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await backend.shutdown()

    assert transport.cancelled == ["/v1/executions/cancel-me"]
