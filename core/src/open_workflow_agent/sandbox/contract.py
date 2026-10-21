"""Abstract sandbox contract: request/result types, backend protocol, reserved names.

This is the lean interface layer. The reusable implementations (internal
process machinery, restricted-controller clients) live beside it as shared
utilities in `backends/`, and engines only ever consume them through the
public surface re-exported from `open_workflow_agent.sandbox`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

SandboxKind = Literal["script", "shell", "container"]
SandboxStatus = Literal["completed"]

RESERVED_ENVIRONMENT = frozenset({"PATH", "HOME", "TMPDIR", "TEMP", "TMP"})
_RESERVED_ENVIRONMENT_CASEFOLD = frozenset(name.casefold() for name in RESERVED_ENVIRONMENT)


def is_reserved_environment_name(name: str) -> bool:
    """Check runtime isolation names portably across case-sensitive and insensitive hosts."""
    return name.casefold() in _RESERVED_ENVIRONMENT_CASEFOLD


@dataclass(frozen=True, slots=True)
class SandboxSecretReference:
    """Deployment-owned environment variable reference, never a serialized secret value."""

    name: str


@dataclass(frozen=True, slots=True)
class SandboxExecutionRequest:
    execution_id: str
    kind: SandboxKind
    command: str | None = None
    arguments: tuple[str, ...] = ()
    stdin: str | None = None
    environment: tuple[tuple[str, str | SandboxSecretReference], ...] = ()
    script_language: str | None = None
    script_code: str | None = None
    image: str | None = None
    invocation_id: str | None = None
    task_reference: str | None = None


@dataclass(frozen=True, slots=True)
class SandboxExecutionResult:
    execution_id: str
    exit_code: int
    stdout: str
    stderr: str
    duration: float
    status: SandboxStatus = "completed"

    def as_output(self) -> dict[str, Any]:
        return {
            "code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


@dataclass(frozen=True, slots=True)
class SandboxStdioRequest:
    execution_id: str
    command: str
    arguments: tuple[str, ...] = ()
    environment: tuple[tuple[str, str], ...] | None = None
    timeout_seconds: float = 30.0


class SandboxStdioSession(Protocol):
    async def send_frame(
        self, value: bytes, *, on_dispatch: Callable[[], None] | None = None
    ) -> None: ...

    async def receive_frame(self) -> bytes: ...

    async def aclose(self) -> None: ...


class SandboxBackend(Protocol):
    async def execute(self, request: SandboxExecutionRequest) -> SandboxExecutionResult: ...

    async def cancel(self, execution_id: str) -> None: ...

    async def shutdown(self) -> None: ...

    def capabilities(self) -> dict[str, Any]: ...
