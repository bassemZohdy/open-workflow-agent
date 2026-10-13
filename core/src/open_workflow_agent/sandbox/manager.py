"""SandboxManager: backend selection stays deployment-controlled."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from ..errors import SandboxPolicyError
from .contract import (
    SandboxBackend,
    SandboxExecutionRequest,
    SandboxExecutionResult,
    SandboxStdioRequest,
    SandboxStdioSession,
)


class SandboxManager:
    def __init__(self, backend: SandboxBackend) -> None:
        self.backend = backend

    async def execute(self, request: SandboxExecutionRequest) -> SandboxExecutionResult:
        return await self.backend.execute(request)

    @asynccontextmanager
    async def open_stdio(self, request: SandboxStdioRequest) -> AsyncIterator[SandboxStdioSession]:
        from .backends.internal import InternalSandboxBackend

        if not isinstance(self.backend, InternalSandboxBackend):
            raise SandboxPolicyError("selected sandbox backend does not support MCP stdio")
        async with self.backend.open_stdio(request) as session:
            yield session

    async def cancel(self, execution_id: str) -> None:
        await self.backend.cancel(execution_id)

    async def shutdown(self) -> None:
        await self.backend.shutdown()

    def capabilities(self) -> dict[str, Any]:
        return self.backend.capabilities()
