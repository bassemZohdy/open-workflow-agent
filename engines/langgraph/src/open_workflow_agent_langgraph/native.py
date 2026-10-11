"""Optional LangGraph Functional API bridge with native checkpoint storage."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from open_workflow_agent.errors import OwaError, WorkflowExecutionError
from open_workflow_agent.expressions import validate_expression_data
from open_workflow_agent.storage import is_postgresql_datasource
from open_workflow_agent.workflow import WorkflowExecutor, WorkflowPlan

NATIVE_SCOPE_STEP_LIMIT = 1000000

# None is a native resume signal. A private bytes value preserves JSON null
# without changing existing non-null checkpoint inputs or colliding with JSON.
_NULL_INPUT = b"owa/null-input/v1"

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

try:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
except ImportError:  # pragma: no cover - optional PostgreSQL extra
    AsyncPostgresSaver = None  # type: ignore[assignment,misc]


class LangGraphFunctionalAdapter:
    """Compile the common runner as a native Functional API entrypoint."""

    def __init__(self, database_path: str | None = None) -> None:
        self.database_path = database_path
        self.checkpointer = InMemorySaver() if LANGGRAPH_AVAILABLE and InMemorySaver else None
        self._prepared_database_path: str | None = None
        self._preparation_lock = asyncio.Lock()

    async def prepare_storage(self) -> None:
        """Let the native saver initialize WAL before concurrent first writers."""
        path = self.database_path
        if not LANGGRAPH_AVAILABLE or not path or is_postgresql_datasource(path):
            return
        if AsyncSqliteSaver is None:
            return
        async with self._preparation_lock:
            if self._prepared_database_path == path:
                return
            async with AsyncSqliteSaver.from_conn_string(path) as saver:
                await saver.setup()
            self._prepared_database_path = path

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
            if isinstance(input_data, bytes) and input_data == _NULL_INPUT:
                input_data = None
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
        if isinstance(input_data, bytes) and input_data == _NULL_INPUT:
            validate_expression_data(input_data)
        if input_data is None:
            input_data = _NULL_INPUT
        await self.prepare_storage()
        config = {"configurable": {"thread_id": thread_id}}
        if self.database_path and is_postgresql_datasource(self.database_path):
            if AsyncPostgresSaver is None:
                raise RuntimeError(
                    "PostgreSQL LangGraph persistence requires langgraph-checkpoint-postgres"
                )
            async with AsyncPostgresSaver.from_conn_string(self.database_path) as saver:
                await saver.setup()
                graph = self.compile(runner, checkpointer=saver)
                return await graph.ainvoke(input_data, config=config)
        if self.database_path and AsyncSqliteSaver is not None:
            async with AsyncSqliteSaver.from_conn_string(self.database_path) as saver:
                await saver.setup()
                graph = self.compile(runner, checkpointer=saver)
                return await graph.ainvoke(input_data, config=config)
        graph = self.compile(runner)
        return await graph.ainvoke(input_data, config=config)

    async def execute_plan(
        self,
        executor: WorkflowExecutor,
        plan: WorkflowPlan,
        input_data: Any,
        *,
        metadata: dict[str, Any],
        invocation_id: str,
        resume: bool = False,
    ) -> Any:
        """Run each derived task as a native node with private scope checkpoints."""
        if not LANGGRAPH_AVAILABLE:
            return await executor.execute(plan, input_data, metadata=metadata)
        from .graph import LangGraphScopeExecutor

        await self.prepare_storage()

        async def run(saver: Any) -> Any:
            scopes = LangGraphScopeExecutor(plan, saver, invocation_id, resume=resume)
            raw_input = await scopes.original_input() if resume else input_data
            try:
                return await executor.execute(
                    plan, raw_input, metadata=metadata, scope_executor=scopes
                )
            except OwaError:
                raise
            except Exception:
                raise WorkflowExecutionError("native task execution failed") from None

        if self.database_path and is_postgresql_datasource(self.database_path):
            if AsyncPostgresSaver is None:
                raise RuntimeError(
                    "PostgreSQL LangGraph persistence requires langgraph-checkpoint-postgres"
                )
            async with AsyncPostgresSaver.from_conn_string(self.database_path) as saver:
                await saver.setup()
                return await run(saver)
        if self.database_path and AsyncSqliteSaver is not None:
            async with AsyncSqliteSaver.from_conn_string(self.database_path) as sqlite_saver:
                await sqlite_saver.setup()
                return await run(sqlite_saver)
        return await run(self.checkpointer)

    async def resume(
        self,
        runner: Callable[[Any], Awaitable[Any]],
        resume_input: Any,
        *,
        thread_id: str,
    ) -> Any:
        if not LANGGRAPH_AVAILABLE:
            return await runner(resume_input)
        await self.prepare_storage()
        from langgraph.types import Command

        config = {"configurable": {"thread_id": thread_id}}
        if self.database_path and is_postgresql_datasource(self.database_path):
            if AsyncPostgresSaver is None:
                raise RuntimeError(
                    "PostgreSQL LangGraph persistence requires langgraph-checkpoint-postgres"
                )
            async with AsyncPostgresSaver.from_conn_string(self.database_path) as saver:
                await saver.setup()
                graph = self.compile(runner, checkpointer=saver)
                return await graph.ainvoke(Command(resume=resume_input), config=config)
        if self.database_path and AsyncSqliteSaver is not None:
            async with AsyncSqliteSaver.from_conn_string(self.database_path) as saver:
                await saver.setup()
                graph = self.compile(runner, checkpointer=saver)
                return await graph.ainvoke(Command(resume=resume_input), config=config)
        graph = self.compile(runner)
        return await graph.ainvoke(Command(resume=resume_input), config=config)
