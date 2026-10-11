"""Native LangGraph task execution and legacy Functional API helpers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from open_workflow_agent.errors import (
    InvocationStateConflict,
    InvocationStateError,
    OwaError,
    WorkflowExecutionError,
)
from open_workflow_agent.expressions import validate_expression_data
from open_workflow_agent.ownership import (
    NativeWriteFence,
    install_sqlite_native_fence,
    run_native_storage_write,
    translate_native_fence_error,
)
from open_workflow_agent.storage import is_postgresql_datasource
from open_workflow_agent.workflow import WorkflowExecutor, WorkflowPlan

NATIVE_SCOPE_STEP_LIMIT = 1000000

# None is a native resume signal. A private bytes value preserves JSON null
# without changing existing non-null checkpoint inputs or colliding with JSON.
_NULL_INPUT = b"owa/null-input/v1"

try:
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.func import entrypoint

    LANGGRAPH_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without optional dependency
    InMemorySaver = None  # type: ignore[assignment,misc]
    entrypoint = None  # type: ignore[assignment]
    LANGGRAPH_AVAILABLE = False

try:
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
except ImportError:  # pragma: no cover - optional SQLite extra
    AsyncSqliteSaver = None  # type: ignore[assignment,misc]

try:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
except ImportError:  # pragma: no cover - optional PostgreSQL extra
    AsyncPostgresSaver = None  # type: ignore[assignment,misc]


if AsyncSqliteSaver is not None:

    class _FencedAsyncSqliteSaver(AsyncSqliteSaver):  # type: ignore[misc]
        async def _write(self, method: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
            await self.setup()
            async with self.lock:

                async def write() -> Any:
                    # The shared SDK connection is protected by the parent
                    # saver lock. The SDK operation gets a loop-local lock;
                    # its original transaction and serialization stay intact.
                    worker = AsyncSqliteSaver(self.conn, serde=self.serde)
                    worker.is_setup = True
                    try:
                        return await getattr(worker, method)(*args, **kwargs)
                    except BaseException as exc:
                        await self.conn.rollback()
                        translate_native_fence_error(exc)
                        raise

                return await run_native_storage_write(write)

        async def aput(self, *args: Any, **kwargs: Any) -> Any:
            return await self._write("aput", args, kwargs)

        async def aput_writes(self, *args: Any, **kwargs: Any) -> None:
            await self._write("aput_writes", args, kwargs)

        async def adelete_thread(self, *args: Any, **kwargs: Any) -> None:
            await self._write("adelete_thread", args, kwargs)


if AsyncPostgresSaver is not None:

    class _FencedAsyncPostgresSaver(AsyncPostgresSaver):  # type: ignore[misc]
        _execution_fence: NativeWriteFence | None = None
        _native_write_database: str | None = None

        async def _write(self, method: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
            if self._native_write_database is None:
                return await getattr(super(), method)(*args, **kwargs)
            database = self._native_write_database
            async with self.lock:

                async def write() -> Any:
                    async with _FencedAsyncPostgresSaver.from_conn_string(database) as worker:
                        worker._execution_fence = self._execution_fence
                        return await getattr(worker, method)(*args, **kwargs)

                return await run_native_storage_write(write)

        async def aput(self, *args: Any, **kwargs: Any) -> Any:
            return await self._write("aput", args, kwargs)

        async def aput_writes(self, *args: Any, **kwargs: Any) -> None:
            await self._write("aput_writes", args, kwargs)

        async def adelete_thread(self, *args: Any, **kwargs: Any) -> None:
            await self._write("adelete_thread", args, kwargs)

        @asynccontextmanager
        async def _cursor(self, *, pipeline: bool = False) -> AsyncIterator[Any]:
            # Native mutation methods request pipeline=True. Retain the SDK's
            # cursor lock and SQL, while committing this batch in one guarded
            # transaction instead of committing individual pipeline statements.
            async with super()._cursor(pipeline=False) as cursor:
                if self._execution_fence is None or not pipeline:
                    yield cursor
                    return
                async with cursor.connection.transaction():
                    statement, values = self._execution_fence.postgresql_guard()
                    await cursor.execute(statement.replace("?", "%s"), values)
                    if await cursor.fetchone() is None:
                        raise InvocationStateConflict(
                            "invocation execution ownership has expired or changed"
                        )
                    yield cursor


class LangGraphFunctionalAdapter:
    """Execute native task scopes; preserve direct Functional API callers."""

    def __init__(self, database_path: str | None = None) -> None:
        self.database_path = database_path
        self.checkpointer = InMemorySaver() if LANGGRAPH_AVAILABLE and InMemorySaver else None
        self._prepared_database_path: str | None = None
        self._preparation_lock = asyncio.Lock()

    @property
    def task_nodes_available(self) -> bool:
        if not LANGGRAPH_AVAILABLE:
            return False
        if not self.database_path:
            return True
        if is_postgresql_datasource(self.database_path):
            return AsyncPostgresSaver is not None
        return AsyncSqliteSaver is not None

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
        if not self.task_nodes_available:
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
        _execution_fence: NativeWriteFence | None = None,
    ) -> Any:
        """Run each derived task as a native node with private scope checkpoints."""
        if not self.task_nodes_available:
            return await executor.execute(plan, input_data, metadata=metadata)
        from .graph import LangGraphScopeExecutor

        await self.prepare_storage()

        async def run(saver: Any) -> Any:
            scopes = LangGraphScopeExecutor(
                plan,
                saver,
                invocation_id,
                resume=resume,
                effect_journal=getattr(executor.services, "effect_journal", None),
            )
            raw_input = await scopes.original_input(input_data) if resume else input_data
            try:
                return await executor.execute(
                    plan,
                    raw_input,
                    metadata={**metadata, "_effect_receipt_observer": scopes.record_effect_receipt},
                    scope_executor=scopes,
                )
            except OwaError:
                raise
            except Exception:
                raise WorkflowExecutionError("native task execution failed") from None

        return await self._with_saver(run, _execution_fence=_execution_fence)

    async def admit_resume(
        self,
        plan: WorkflowPlan,
        invocation_id: str,
        workflow_input: Any,
        *,
        effect_journal: Any = None,
    ) -> None:
        from .graph import LangGraphScopeExecutor

        async def validate(saver: Any) -> None:
            scopes = LangGraphScopeExecutor(
                plan, saver, invocation_id, resume=True, effect_journal=effect_journal
            )
            await scopes.original_input(workflow_input)

        try:
            await self.prepare_storage()
            await self._with_saver(validate)
        except OwaError:
            raise
        except Exception:
            raise InvocationStateError("native execution state could not be recovered") from None

    async def _with_saver(
        self,
        run: Callable[[Any], Awaitable[Any]],
        *,
        _execution_fence: NativeWriteFence | None = None,
    ) -> Any:
        if self.database_path and is_postgresql_datasource(self.database_path):
            if AsyncPostgresSaver is None:
                raise RuntimeError(
                    "PostgreSQL LangGraph persistence requires langgraph-checkpoint-postgres"
                )
            async with _FencedAsyncPostgresSaver.from_conn_string(self.database_path) as saver:
                await saver.setup()
                saver._execution_fence = _execution_fence
                if _execution_fence is not None:
                    saver._native_write_database = self.database_path
                return await run(saver)
        if self.database_path and AsyncSqliteSaver is not None:
            async with _FencedAsyncSqliteSaver.from_conn_string(self.database_path) as sqlite_saver:
                await run_native_storage_write(sqlite_saver.setup)
                if _execution_fence is not None:
                    await install_sqlite_native_fence(
                        sqlite_saver.conn,
                        _execution_fence,
                        native_database=self.database_path,
                        tables=("checkpoints", "writes"),
                    )
                try:
                    return await run(sqlite_saver)
                except BaseException as exc:
                    await sqlite_saver.conn.rollback()
                    translate_native_fence_error(exc)
                    raise
        return await run(self.checkpointer)

    async def resume(
        self,
        runner: Callable[[Any], Awaitable[Any]],
        resume_input: Any,
        *,
        thread_id: str,
    ) -> Any:
        if not self.task_nodes_available:
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
