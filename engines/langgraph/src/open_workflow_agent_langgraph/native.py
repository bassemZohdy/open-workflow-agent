"""Optional LangGraph Functional API bridge with native checkpoint storage."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

try:
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from langgraph.func import entrypoint

    LANGGRAPH_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without optional dependency
    InMemorySaver = None  # type: ignore[assignment,misc]
    AsyncSqliteSaver = None  # type: ignore[assignment,misc]
    entrypoint = None  # type: ignore[assignment]
    LANGGRAPH_AVAILABLE = False


class LangGraphFunctionalAdapter:
    """Compile the common runner as a native Functional API entrypoint."""

    def __init__(self, database_path: str | None = None) -> None:
        self.database_path = database_path
        self.checkpointer = InMemorySaver() if LANGGRAPH_AVAILABLE and InMemorySaver else None

    def compile(
        self,
        runner: Callable[[Any], Awaitable[Any]],
        *,
        checkpointer: Any = None,
    ) -> Any:
        if not LANGGRAPH_AVAILABLE or entrypoint is None:
            return runner
        saver = checkpointer if checkpointer is not None else self.checkpointer

        @entrypoint(checkpointer=saver)
        async def workflow(input_data: Any) -> Any:
            return await runner(input_data)

        return workflow

    async def invoke(
        self,
        runner: Callable[[Any], Awaitable[Any]],
        input_data: Any,
        *,
        thread_id: str,
    ) -> Any:
        if not LANGGRAPH_AVAILABLE:
            return await runner(input_data)
        config = {"configurable": {"thread_id": thread_id}}
        if self.database_path and AsyncSqliteSaver is not None:
            async with AsyncSqliteSaver.from_conn_string(self.database_path) as saver:
                await saver.setup()
                graph = self.compile(runner, checkpointer=saver)
                return await graph.ainvoke(input_data, config=config)
        graph = self.compile(runner)
        return await graph.ainvoke(input_data, config=config)

    async def resume(
        self,
        runner: Callable[[Any], Awaitable[Any]],
        resume_input: Any,
        *,
        thread_id: str,
    ) -> Any:
        if not LANGGRAPH_AVAILABLE:
            return await runner(resume_input)
        from langgraph.types import Command

        config = {"configurable": {"thread_id": thread_id}}
        if self.database_path and AsyncSqliteSaver is not None:
            async with AsyncSqliteSaver.from_conn_string(self.database_path) as saver:
                await saver.setup()
                graph = self.compile(runner, checkpointer=saver)
                return await graph.ainvoke(Command(resume=resume_input), config=config)
        graph = self.compile(runner)
        return await graph.ainvoke(Command(resume=resume_input), config=config)
