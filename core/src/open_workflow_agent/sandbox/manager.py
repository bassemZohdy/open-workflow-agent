"""SandboxManager: backend selection stays deployment-controlled."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

from ..errors import SandboxPolicyError
from .contract import (
    SandboxBackend,
    SandboxExecutionRequest,
    SandboxExecutionResult,
    SandboxStdioRequest,
    SandboxStdioSession,
)
from .job_backend import SandboxJobBackend, SandboxJobLaunch, SandboxJobObservation
from .jobs import SandboxJobBackendBinding, SandboxJobProfile


class SandboxManager:
    def __init__(
        self, backend: SandboxBackend, *, job_backend: SandboxJobBackend | None = None
    ) -> None:
        self.backend = backend
        self._job_backend = job_backend

    def validate_job_profile(self, profile: SandboxJobProfile) -> None:
        backend = self._job_backend
        selected = self.backend.capabilities()
        if (
            backend is None
            or backend.profile() != profile
            or not selected.get("enabled", False)
            or selected.get("backend") != profile.backend
        ):
            raise SandboxPolicyError("sandbox job deployment profile is unavailable or changed")

    def validate_job_profile_from_admission(self, admission: dict[str, Any]) -> None:
        self.validate_job_profile(SandboxJobProfile(**admission["backend_profile"]))

    def _jobs(self, launch: SandboxJobLaunch) -> SandboxJobBackend:
        self.validate_job_profile(launch.profile)
        if self._job_backend is None:
            raise SandboxPolicyError("sandbox job backend is unavailable")
        return self._job_backend

    async def submit_job(self, launch: SandboxJobLaunch) -> SandboxJobObservation:
        return await self._jobs(launch).submit(launch)

    async def inspect_job(
        self, launch: SandboxJobLaunch, binding: SandboxJobBackendBinding | None
    ) -> SandboxJobObservation:
        return await self._jobs(launch).inspect(launch, binding)

    async def cancel_job(
        self,
        launch: SandboxJobLaunch,
        binding: SandboxJobBackendBinding,
        *,
        generation: int,
        reason: Literal["cancel", "deadline"],
    ) -> SandboxJobObservation:
        return await self._jobs(launch).cancel(
            launch, binding, generation=generation, reason=reason
        )

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
        value = dict(self.backend.capabilities())
        value["awaitedReturn"] = {
            "enabled": bool(value.get("enabled", False)),
            "default": "stdout",
            "modes": ["stdout", "stderr", "code", "all", "none"],
            "processResultFields": ["code", "stdout", "stderr"],
        }
        value["backgroundExecution"] = False
        if self._job_backend is not None:
            from .internal_jobs import InternalSandboxJobBackend

            backend = self._job_backend
            if (
                isinstance(backend, InternalSandboxJobBackend)
                and backend.config.background.enabled
                and backend.store.connection.kind == "sqlite"
                and value.get("enabled") is True
                and value.get("backend") == "internal"
            ):
                try:
                    self.validate_job_profile(backend.profile())
                except SandboxPolicyError:
                    pass
                else:
                    value["backgroundExecution"] = True
                    value["backgroundProfile"] = {
                        "profile": "owa/internal-background-job/v1",
                        "backend": "internal",
                        "storage": "embedded-sqlite",
                        "kinds": ["script", "shell"],
                        "lifetime": "independent",
                        "returns": "transformed-task-input",
                        "environment": "secret-references",
                        "timeoutSeconds": backend.config.timeout_seconds,
                        "survivesRuntimeStop": True,
                        "automaticAmbiguousRelaunch": False,
                        "supervisorLoss": "uncertain-no-adoption",
                        "supervisorLivenessRequiredForWallTime": True,
                        "hardIsolation": False,
                    }
        return value
