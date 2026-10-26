"""Engine-neutral management boundary; native plans and checkpoints stay private."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from .config import NativeStateUpdateConfig
    from .execution_authority import ExecutionAuthority
    from .persistence import ExecutionHandle
    from .state_update_resolution import StateUpdateResolution
    from .workflow import WorkflowPlan


class NativeStateController(Protocol):
    def verify(self, configuration: NativeStateUpdateConfig | None) -> None: ...

    def bind(self, plan: WorkflowPlan) -> None: ...

    async def update(
        self,
        handle: ExecutionHandle,
        authority: ExecutionAuthority,
        *,
        request_id: str,
        base_reference: str,
        task_reference: str,
        values: dict[str, Any],
    ) -> dict[str, Any]: ...

    async def inspect(
        self, handle: ExecutionHandle, authority: ExecutionAuthority
    ) -> dict[str, Any]: ...

    async def resolve(
        self,
        handle: ExecutionHandle,
        authority: ExecutionAuthority,
        *,
        operation_id: str,
        request_digest: str,
        decision: StateUpdateResolution,
    ) -> dict[str, Any]: ...
