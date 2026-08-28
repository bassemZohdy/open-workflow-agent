"""SandboxManager: backend selection stays deployment-controlled."""

from __future__ import annotations

from typing import Any

from .contract import SandboxBackend, SandboxExecutionRequest, SandboxExecutionResult


class SandboxManager:
    def __init__(self, backend: SandboxBackend) -> None:
        self.backend = backend

    async def execute(self, request: SandboxExecutionRequest) -> SandboxExecutionResult:
        return await self.backend.execute(request)

    async def cancel(self, execution_id: str) -> None:
        await self.backend.cancel(execution_id)

    async def shutdown(self) -> None:
        await self.backend.shutdown()

    def capabilities(self) -> dict[str, Any]:
        return self.backend.capabilities()
