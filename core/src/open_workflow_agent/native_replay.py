"""Engine-neutral replay admission boundary; native plans and checkpoints stay private."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from .config import NativeStateReplayConfig
    from .execution_authority import ExecutionAuthority
    from .persistence import ExecutionHandle


class NativeReplayController(Protocol):
    def verify(self, configuration: NativeStateReplayConfig | None) -> None: ...

    async def admit(
        self,
        handle: ExecutionHandle,
        authority: ExecutionAuthority,
        *,
        request_id: str,
        reference: str,
        acknowledge_reexecution: bool,
    ) -> dict[str, Any]:
        """Admit and bind a replay; the new invocation then continues in the background."""
        ...
