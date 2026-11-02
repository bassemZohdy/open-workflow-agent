"""Engine-neutral admission boundary for attributed state branches."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from .config import NativeStateBranchConfig
    from .execution_authority import ExecutionAuthority
    from .persistence import ExecutionHandle


class NativeBranchController(Protocol):
    def verify(self, configuration: NativeStateBranchConfig | None) -> None: ...

    async def admit(
        self,
        handle: ExecutionHandle,
        authority: ExecutionAuthority,
        *,
        request_id: str,
        reference: str,
        task_reference: str,
        values: dict[str, Any],
        acknowledge_reexecution: bool,
    ) -> dict[str, Any]: ...
