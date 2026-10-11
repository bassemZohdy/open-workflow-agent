"""Native ADK 2.x dynamic-node invocation for the portable plan."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import aclosing, asynccontextmanager, closing
from importlib import import_module
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunsplit

from open_workflow_agent.errors import InvocationStateConflict, InvocationStateError
from open_workflow_agent.expressions import validate_expression_data
from open_workflow_agent.ownership import (
    NativeWriteFence,
    install_sqlite_native_fence,
    run_native_storage_write,
    translate_native_fence_error,
)
from open_workflow_agent.storage import is_postgresql_datasource

try:
    from google.adk.agents.context import Context
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.adk.sessions.sqlite_session_service import SqliteSessionService
    from google.adk.workflow import FunctionNode
    from google.genai import types

    ADK_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised in the core-only environment
    Context = None  # type: ignore[assignment]
    Runner = None  # type: ignore[assignment]
    InMemorySessionService = None  # type: ignore[assignment]
    SqliteSessionService = None  # type: ignore[assignment]
    FunctionNode = None  # type: ignore[assignment]
    types = None  # type: ignore[assignment]
    ADK_AVAILABLE = False

try:
    from google.adk.sessions import DatabaseSessionService as _DatabaseSessionService
except ImportError:  # pragma: no cover - optional ADK db extra
    _DatabaseSessionService = None  # type: ignore[assignment]


# ADK's dynamic FunctionNode is deliberately replayed after a restart. The
# common executor derives side-effect operation ids from the invocation and
# canonical task reference, so replay is safe only when the external effect
# honors the propagated idempotency key.
ADK_RESUME_STRATEGY = "replay_with_stable_operation_ids"


def _native_input_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate native input field")
        result[key] = value
    return result


if SqliteSessionService is not None:

    class _ClosingSqliteSessionService(SqliteSessionService):  # type: ignore[misc]
        """Bound the pinned SDK migration probe's synchronous connection lifetime.

        The upstream probe uses sqlite3's transaction context manager, which
        does not close its connection. On Windows that retains a file handle
        until garbage collection and prevents deterministic storage cleanup.
        All session/checkpoint operations remain native ADK behavior.
        """

        _execution_fence: NativeWriteFence | None = None

        async def create_session(self, *args: Any, **kwargs: Any) -> Any:
            original = super().create_session
            if self._execution_fence is None:
                return await original(*args, **kwargs)
            return await run_native_storage_write(lambda: original(*args, **kwargs))

        async def append_event(self, *args: Any, **kwargs: Any) -> Any:
            original = super().append_event
            if self._execution_fence is None:
                return await original(*args, **kwargs)
            return await run_native_storage_write(lambda: original(*args, **kwargs))

        async def delete_session(self, *args: Any, **kwargs: Any) -> Any:
            original = super().delete_session
            if self._execution_fence is None:
                return await original(*args, **kwargs)
            return await run_native_storage_write(lambda: original(*args, **kwargs))

        @asynccontextmanager
        async def _get_db_connection(self) -> AsyncIterator[Any]:
            async with super()._get_db_connection() as db:
                if self._execution_fence is not None:
                    await install_sqlite_native_fence(
                        db,
                        self._execution_fence,
                        native_database=self._db_path,
                        tables=("app_states", "user_states", "sessions", "events"),
                    )
                try:
                    yield db
                except BaseException as exc:
                    await db.rollback()
                    translate_native_fence_error(exc)
                    raise

        def _is_migration_needed(self) -> bool:
            if not os.path.exists(self._db_path):
                return False
            try:
                with closing(
                    sqlite3.connect(self._db_connect_path, uri=self._db_connect_uri)
                ) as db:
                    exists = db.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'"
                    ).fetchone()
                    if not exists:
                        return False
                    columns = [row[1] for row in db.execute("PRAGMA table_info(events)").fetchall()]
                    return "event_data" not in columns
            except sqlite3.Error as exc:
                raise RuntimeError("unable to inspect ADK session schema") from exc


if _DatabaseSessionService is not None:

    class _FencedDatabaseSessionService(_DatabaseSessionService):  # type: ignore[misc]
        _execution_fence: NativeWriteFence | None = None
        _native_write_factory: Callable[[], Any] | None = None

        async def _write(self, method: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
            if self._native_write_factory is None:
                return await getattr(super(), method)(*args, **kwargs)
            factory = self._native_write_factory

            async def write() -> Any:
                worker = factory()
                worker._execution_fence = self._execution_fence
                try:
                    return await getattr(worker, method)(*args, **kwargs)
                finally:
                    await worker.close()

            return await run_native_storage_write(write)

        async def create_session(self, *args: Any, **kwargs: Any) -> Any:
            return await self._write("create_session", args, kwargs)

        async def append_event(self, *args: Any, **kwargs: Any) -> Any:
            return await self._write("append_event", args, kwargs)

        async def delete_session(self, *args: Any, **kwargs: Any) -> Any:
            return await self._write("delete_session", args, kwargs)

        @asynccontextmanager
        async def _rollback_on_exception_session(
            self, *, read_only: bool = False
        ) -> AsyncIterator[Any]:
            async with super()._rollback_on_exception_session(read_only=read_only) as session:
                if self._execution_fence is not None and not read_only:
                    statement, values = self._execution_fence.postgresql_guard()
                    parameters = {}
                    for index, value in enumerate(values):
                        name = f"owa_owner_{index}"
                        statement = statement.replace("?", f":{name}", 1)
                        parameters[name] = value
                    row = await session.execute(
                        import_module("sqlalchemy").text(statement), parameters
                    )
                    if row.first() is None:
                        raise InvocationStateConflict(
                            "invocation execution ownership has expired or changed"
                        )
                yield session


class NativeAdkRunner:
    """Execute a plan through an ADK dynamic FunctionNode child."""

    resume_strategy = ADK_RESUME_STRATEGY
    rerun_on_resume = True

    def __init__(self) -> None:
        self.available = ADK_AVAILABLE

    def _session_service(self, database_path: str | None) -> Any:
        if database_path and is_postgresql_datasource(database_path):
            if _DatabaseSessionService is None:
                raise RuntimeError("PostgreSQL ADK persistence requires google-adk[db] and asyncpg")
            parsed = urlparse(database_path)
            query = dict(parse_qsl(parsed.query, keep_blank_values=True))
            query.pop("options", None)
            db_url = urlunsplit(
                (
                    "postgresql+asyncpg",
                    parsed.netloc,
                    parsed.path,
                    urlencode(query),
                    parsed.fragment,
                )
            )
            return _FencedDatabaseSessionService(
                db_url, connect_args={"server_settings": {"search_path": "owa_adk,public"}}
            )
        if database_path and SqliteSessionService is not None:
            return _ClosingSqliteSessionService(database_path)
        if InMemorySessionService is None:
            raise RuntimeError("google-adk 2.x is not installed")
        return InMemorySessionService()

    @staticmethod
    async def _close_sessions(sessions: Any) -> None:
        try:
            close = getattr(sessions, "close", None)
            if close is not None:
                await close()
        except Exception:
            raise InvocationStateError("native execution state could not be released") from None

    @staticmethod
    def _validate_resume_session(session: Any, invocation_id: str, workflow_input: Any) -> None:
        try:
            if session is None:
                raise ValueError("session is absent")
            validate_expression_data(workflow_input)
            expected = json.dumps(workflow_input, sort_keys=True, allow_nan=False)
            found_input = False
            for event in session.events:
                if event.invocation_id == invocation_id and event.author == "user":
                    parts = event.content.parts if event.content else None
                    if parts is None or len(parts) != 1 or not isinstance(parts[0].text, str):
                        raise ValueError("original input is absent")
                    saved = json.loads(parts[0].text, object_pairs_hook=_native_input_object)
                    validate_expression_data(saved)
                    if json.dumps(saved, sort_keys=True, allow_nan=False) != expected:
                        raise ValueError("original input disagrees")
                    # The SDK may retain matching user messages on replay.
                    # Every message for this invocation must agree; the first
                    # matching input cannot authorize contradictory later ones.
                    found_input = True
            if not found_input:
                raise ValueError("invocation is absent")
        except Exception:
            raise InvocationStateError(
                "native execution state is unavailable or inconsistent; recovery is required"
            ) from None

    async def admit_resume(
        self,
        workflow_input: Any,
        *,
        session_id: str,
        user_id: str | None,
        invocation_id: str,
        database_path: str | None = None,
    ) -> None:
        sessions = None
        try:
            sessions = self._session_service(database_path)
            session = await sessions.get_session(
                app_name="open-workflow-agent", user_id=user_id or "owa-user", session_id=session_id
            )
            self._validate_resume_session(session, invocation_id, workflow_input)
        except InvocationStateError:
            raise
        except Exception:
            raise InvocationStateError("native execution state could not be recovered") from None
        finally:
            if sessions is not None:
                await self._close_sessions(sessions)

    async def invoke(
        self,
        runner: Callable[[Any], Awaitable[Any]],
        input_data: Any,
        *,
        session_id: str,
        user_id: str | None,
        database_path: str | None = None,
        invocation_id: str | None = None,
        _resume: bool = False,
        _execution_fence: NativeWriteFence | None = None,
    ) -> Any:
        if (
            not ADK_AVAILABLE
            or Runner is None
            or InMemorySessionService is None
            or FunctionNode is None
            or Context is None
            or types is None
        ):
            raise RuntimeError("google-adk 2.x is not installed")
        app_name = "open-workflow-agent"
        user = user_id or "owa-user"
        sessions = self._session_service(database_path)
        postgresql = bool(database_path and is_postgresql_datasource(database_path))
        if not postgresql:
            sessions._execution_fence = _execution_fence
        try:
            existing = await sessions.get_session(
                app_name=app_name,
                user_id=user,
                session_id=session_id,
            )
            if postgresql:
                # Initial reads may prepare the SDK's schema metadata. Do not
                # hold the common ownership row on the main loop during that
                # schema-only transaction. Actual mutations use SDK workers.
                sessions._execution_fence = _execution_fence
                if _execution_fence is not None:
                    sessions._native_write_factory = lambda: self._session_service(database_path)
            if _resume:
                self._validate_resume_session(existing, invocation_id or "", input_data)
            if existing is None:
                await sessions.create_session(
                    app_name=app_name,
                    user_id=user,
                    session_id=session_id,
                    state={},
                )
        except BaseException:
            await self._close_sessions(sessions)
            raise

        result_value: Any = None

        async def root_node(ctx: Context) -> Any:
            # A session can contain several invocations. Session-wide state is
            # not an input binding for this native invocation.
            node_input = input_data

            async def dynamic_node(node_input: Any) -> Any:
                nonlocal result_value
                result_value = await runner(node_input)
                return result_value

            return await ctx.run_node(
                dynamic_node,
                node_input,
                use_as_output=True,
                run_id="owa-plan",
            )

        root = FunctionNode(
            func=root_node,
            name="owa_root",
            rerun_on_resume=self.rerun_on_resume,
        )
        framework_runner = Runner(
            app_name=app_name,
            node=root,
            session_service=sessions,
        )
        message = types.Content(
            role="user",
            parts=[types.Part(text=json.dumps(input_data, ensure_ascii=False, default=str))],
        )
        output = None
        try:
            async with aclosing(
                framework_runner.run_async(
                    user_id=user,
                    session_id=session_id,
                    invocation_id=invocation_id,
                    new_message=message,
                    yield_user_message=False,
                )
            ) as events:
                async for event in events:
                    if getattr(event, "output", None) is not None:
                        output = event.output
        finally:
            try:
                await framework_runner.close()
            finally:
                await self._close_sessions(sessions)
        return output if output is not None else result_value

    async def resume(
        self,
        runner: Callable[[Any], Awaitable[Any]],
        resume_input: Any,
        *,
        session_id: str,
        user_id: str | None,
        invocation_id: str,
        database_path: str | None = None,
        _execution_fence: NativeWriteFence | None = None,
    ) -> Any:
        """Resume an ADK invocation using its persisted native invocation id."""
        return await self.invoke(
            runner,
            resume_input,
            session_id=session_id,
            user_id=user_id,
            invocation_id=invocation_id,
            database_path=database_path,
            _resume=True,
            _execution_fence=_execution_fence,
        )
