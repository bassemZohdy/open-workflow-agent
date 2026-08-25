"""Optional LangGraph Functional API bridge."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

try:
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.func import entrypoint, task

    LANGGRAPH_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without optional dependency
    InMemorySaver = None  # type: ignore[assignment,misc]
    entrypoint = None  # type: ignore[assignment]
    task = None  # type: ignore[assignment]
    LANGGRAPH_AVAILABLE = False


class LangGraphFunctionalAdapter:
    """Build a native entrypoint while keeping data semantics in core."""

    def __init__(self) -> None:
        self.checkpointer = InMemorySaver() if LANGGRAPH_AVAILABLE and InMemorySaver else None

    def compile(self, runner: Callable[[Any], Awaitable[Any]]) -> Any:
        if not LANGGRAPH_AVAILABLE or entrypoint is None:
            return runner

        @entrypoint(checkpointer=self.checkpointer)
        async def workflow(input_data: Any) -> Any:
            return await runner(input_data)

        return workflow
