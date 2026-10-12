"""Native ADK 2.x dynamic-node invocation for the portable plan."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import aclosing, asynccontextmanager, closing
from dataclasses import asdict, dataclass
from importlib import import_module
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunsplit

from open_workflow_agent.effects import (
    EffectIdentity,
    EffectJournal,
    EffectReceipt,
    ReceiptDependency,
)
from open_workflow_agent.errors import InvocationStateConflict, InvocationStateError
from open_workflow_agent.expressions import validate_expression_data
from open_workflow_agent.ownership import (
    NativeWriteFence,
    install_sqlite_native_fence,
    run_native_storage_write,
    translate_native_fence_error,
)
from open_workflow_agent.storage import is_postgresql_datasource
from open_workflow_agent.timers import TimerSuspension
from open_workflow_agent.timing import root_clock_identity

try:
    from google.adk.agents.context import Context
    from google.adk.events.request_input import RequestInput
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.adk.sessions.sqlite_session_service import SqliteSessionService
    from google.adk.workflow import FunctionNode
    from google.genai import types

    ADK_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised in the core-only environment
    Context = None  # type: ignore[assignment]
    RequestInput = None  # type: ignore[assignment]
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

_RECEIPT_AUTHOR = "owa_receipt_dependencies"


def _timer_wake_matches(suspension: TimerSuspension, wake: Any) -> bool:
    return bool(
        isinstance(wake, dict)
        and set(wake) == {"timer_key", "due_at", "reason"}
        and wake["timer_key"] == suspension.timer_key
        and type(wake["due_at"]) in {int, float}
        and wake["due_at"] == suspension.due_at
        and isinstance(wake["reason"], str)
        and wake["reason"] in {"timer_due", "resume_requested"}
    )


def _timer_requests(
    session: Any, invocation_id: str, context: AdkRecoveryContext | None = None
) -> dict[str, TimerSuspension]:
    """Validate native request/response history before re-entering the runner."""
    pending: dict[str, TimerSuspension] = {}
    for event in session.events:
        if event.invocation_id != invocation_id:
            continue
        for part in event.content.parts if event.content and event.content.parts else ():
            call = part.function_call
            if call is not None and call.name == "adk_request_input":
                suspension = TimerSuspension.from_value((call.args or {}).get("payload"))
                identity = suspension.identity
                if (
                    call.id != "owa-timer:" + suspension.timer_key
                    or identity.invocation_id
                    != (context.invocation_id if context is not None else invocation_id)
                    or call.id in pending
                ):
                    raise InvocationStateError("native timer request identity is inconsistent")
                if context is not None:
                    armed = context.journal.get(identity)
                    if (
                        identity.workflow_fingerprint != context.workflow_fingerprint
                        or identity.task_reference not in context.task_references
                        or armed is None
                        or armed.status != "completed"
                        or not isinstance(armed.output, dict)
                        or armed.output.get("due_at") != suspension.due_at
                    ):
                        raise InvocationStateError("native timer arming evidence is inconsistent")
                pending[call.id] = suspension
            response = part.function_response
            if response is not None:
                requested = pending.get(response.id or "")
                if (
                    response.name != "adk_request_input"
                    or requested is None
                    or not _timer_wake_matches(requested, response.response)
                ):
                    raise InvocationStateError("native timer response is inconsistent")
                del pending[response.id]
    return pending


@dataclass(frozen=True, slots=True)
class AdkRecoveryContext:
    """Private common identity and receipt service; the SDK owns saved events."""

    invocation_id: str
    workflow_fingerprint: str
    task_references: frozenset[str]
    journal: EffectJournal
    metadata: dict[str, Any]
    require_waiting: bool = False
    uses_timing: bool = False


class _ReceiptEvents:
    def __init__(self, context: AdkRecoveryContext) -> None:
        self.context = context
        self.sequence = 0
        self.dependencies: dict[str, ReceiptDependency] = {}
        self.lock = asyncio.Lock()

    def restore(self, session: Any, invocation_id: str) -> None:
        frames = [
            event.custom_metadata
            for event in session.events
            if event.invocation_id == invocation_id and event.author == _RECEIPT_AUTHOR
        ]
        if not frames:
            raise ValueError("native receipt events are absent")
        waiting = False
        for frame in sorted(frames, key=lambda item: item["sequence"]):
            if not isinstance(frame, dict) or set(frame) != {
                "version",
                "invocation_id",
                "workflow_fingerprint",
                "sequence",
                "phase",
                "receipts",
            }:
                raise ValueError("native receipt event fields are invalid")
            if (
                type(frame["version"]) is not int
                or frame["version"] != 1
                or type(frame["sequence"]) is not int
                or frame["sequence"] != self.sequence + 1
                or frame["invocation_id"] != self.context.invocation_id
                or frame["workflow_fingerprint"] != self.context.workflow_fingerprint
                or frame["phase"] not in {"running", "receipt", "waiting"}
                or not isinstance(frame["receipts"], list)
            ):
                raise ValueError("native receipt event identity is invalid")
            dependencies: dict[str, ReceiptDependency] = {}
            for raw in frame["receipts"]:
                dependency = ReceiptDependency.from_value(raw)
                identity = dependency.identity
                if (
                    identity.invocation_id != self.context.invocation_id
                    or identity.workflow_fingerprint != self.context.workflow_fingerprint
                    or (
                        identity.task_reference not in self.context.task_references
                        and not (
                            self.context.uses_timing
                            and identity
                            == root_clock_identity(
                                self.context.invocation_id, self.context.workflow_fingerprint
                            )
                        )
                    )
                    or identity.key in dependencies
                ):
                    raise ValueError("native receipt dependency identity is invalid")
                dependencies[identity.key] = dependency
            if any(dependencies.get(key) != value for key, value in self.dependencies.items()):
                raise ValueError("native receipt dependency history disagrees")
            self.dependencies = dependencies
            self.sequence = frame["sequence"]
            if frame["phase"] == "running":
                waiting = False
            elif frame["phase"] == "waiting":
                waiting = True
        # An independent fork branch may finish an action after a sibling
        # enters a durable wait. Its later receipt preserves that wait.
        if self.context.require_waiting and not waiting:
            raise ValueError("native waiting event is absent")
        for dependency in self.dependencies.values():
            dependency.verify(self.context.journal)

    async def publish(
        self,
        sessions: Any,
        session: Any,
        invocation_id: str,
        phase: str,
        identity: EffectIdentity | None = None,
        receipt: EffectReceipt | None = None,
    ) -> None:
        async with self.lock:
            if identity is not None and receipt is not None:
                dependency = ReceiptDependency.capture(identity, receipt)
                existing = self.dependencies.get(identity.key)
                if existing is not None and existing != dependency:
                    raise InvocationStateError("native receipt dependency changed")
                self.dependencies[identity.key] = dependency
            frame = {
                "version": 1,
                "invocation_id": self.context.invocation_id,
                "workflow_fingerprint": self.context.workflow_fingerprint,
                "sequence": self.sequence + 1,
                "phase": phase,
                "receipts": [asdict(self.dependencies[key]) for key in sorted(self.dependencies)],
            }
            event = import_module("google.adk.events").Event(
                invocation_id=invocation_id,
                author=_RECEIPT_AUTHOR,
                custom_metadata=frame,
            )
            await sessions.append_event(session=session, event=event)
            self.sequence += 1


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

        async def get_session(self, *args: Any, **kwargs: Any) -> Any:
            # A native read can hold a SQLite reader lock across awaits. Drain
            # it independently of synchronous common-store writes on the main
            # loop, so a fenced native commit can obtain its final write lock.
            original = super().get_session
            return await run_native_storage_write(lambda: original(*args, **kwargs))

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
    def _validate_resume_session(
        session: Any,
        invocation_id: str,
        workflow_input: Any,
        context: AdkRecoveryContext | None = None,
    ) -> None:
        try:
            if session is None:
                raise ValueError("session is absent")
            validate_expression_data(workflow_input)
            expected = json.dumps(workflow_input, sort_keys=True, allow_nan=False)
            _timer_requests(session, invocation_id, context)
            found_input = False
            for event in session.events:
                if event.invocation_id == invocation_id and event.author == "user":
                    parts = event.content.parts if event.content else None
                    if parts is not None and len(parts) == 1 and parts[0].function_response:
                        # Only matched adapter-owned timer responses passed the
                        # complete history check above; original text still binds input.
                        continue
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
        _recovery_context: AdkRecoveryContext | None = None,
    ) -> None:
        sessions = None
        try:
            sessions = self._session_service(database_path)
            session = await sessions.get_session(
                app_name="open-workflow-agent", user_id=user_id or "owa-user", session_id=session_id
            )
            self._validate_resume_session(session, invocation_id, workflow_input, _recovery_context)
            if _recovery_context is not None:
                _ReceiptEvents(_recovery_context).restore(session, invocation_id)
                _timer_requests(session, invocation_id, _recovery_context)
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
        _recovery_context: AdkRecoveryContext | None = None,
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
        receipt_events = _ReceiptEvents(_recovery_context) if _recovery_context else None
        pending_timers: dict[str, TimerSuspension] = {}
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
                self._validate_resume_session(
                    existing, invocation_id or "", input_data, _recovery_context
                )
                if receipt_events is not None:
                    receipt_events.restore(existing, invocation_id or "")
                pending_timers = _timer_requests(existing, invocation_id or "", _recovery_context)
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
        suspended: TimerSuspension | None = None
        wake = _recovery_context.metadata.get("_timer_wake") if _recovery_context else None
        wakes = _recovery_context.metadata.get("_timer_wakes") if _recovery_context else None
        wake_id: str | None = None
        if wakes is not None:
            try:
                validate_expression_data(wakes)
            except Exception:
                await self._close_sessions(sessions)
                raise InvocationStateError("native timer wake map is inconsistent") from None
            if not isinstance(wakes, dict) or any(
                not isinstance(key, str)
                or not isinstance(value, dict)
                or set(value) != {"timer_key", "due_at", "reason"}
                or value["timer_key"] != key
                or type(value["due_at"]) not in {int, float}
                or value["due_at"] <= 0
                or not isinstance(value["reason"], str)
                or value["reason"] not in {"timer_due", "resume_requested"}
                for key, value in wakes.items()
            ):
                await self._close_sessions(sessions)
                raise InvocationStateError("native timer wake map is inconsistent")
        if pending_timers:
            if wakes is not None:
                matches = []
                for key, timer in pending_timers.items():
                    selected_wake = wakes.get(timer.timer_key)
                    if selected_wake is None:
                        continue
                    if not _timer_wake_matches(timer, selected_wake):
                        await self._close_sessions(sessions)
                        raise InvocationStateError(
                            "native timer wake does not match pending request"
                        )
                    matches.append(key)
                if not matches:
                    await self._close_sessions(sessions)
                    raise next(iter(pending_timers.values()))
                if len(matches) == 1:
                    wake = wakes[pending_timers[matches[0]].timer_key]
            else:
                matches = [
                    key for key, timer in pending_timers.items() if _timer_wake_matches(timer, wake)
                ]
            if len(matches) != 1:
                await self._close_sessions(sessions)
                raise InvocationStateError("native timer wake does not match pending request")
            wake_id = matches[0]

        async def root_node(ctx: Context) -> Any:
            # A session can contain several invocations. Session-wide state is
            # not an input binding for this native invocation.
            node_input = input_data
            if receipt_events is not None and _recovery_context is not None:

                async def receipt_observer(
                    identity: EffectIdentity, receipt: EffectReceipt
                ) -> None:
                    await receipt_events.publish(
                        sessions, ctx.session, invocation_id or "", "receipt", identity, receipt
                    )

                async def waiting_observer() -> None:
                    await receipt_events.publish(
                        sessions, ctx.session, invocation_id or "", "waiting"
                    )

                _recovery_context.metadata["_effect_receipt_observer"] = receipt_observer
                _recovery_context.metadata["_waiting_checkpoint_observer"] = waiting_observer
                await receipt_events.publish(sessions, ctx.session, invocation_id or "", "running")

            async def dynamic_node(ctx: Context, node_input: Any) -> Any:
                nonlocal result_value, suspended
                if wake_id is not None and ctx.resume_inputs.get(wake_id) != wake:
                    raise InvocationStateError("native timer wake input is inconsistent")
                try:
                    result_value = await runner(node_input)
                    return result_value
                except TimerSuspension as suspension:
                    if _recovery_context is None or receipt_events is None:
                        raise InvocationStateError(
                            "native timer recovery context is absent"
                        ) from None
                    identity = suspension.identity
                    armed = _recovery_context.journal.get(identity)
                    dependency = receipt_events.dependencies.get(identity.key)
                    if (
                        identity.invocation_id != _recovery_context.invocation_id
                        or identity.workflow_fingerprint != _recovery_context.workflow_fingerprint
                        or identity.task_reference not in _recovery_context.task_references
                        or armed is None
                        or armed.status != "completed"
                        or not isinstance(armed.output, dict)
                        or armed.output.get("due_at") != suspension.due_at
                        or dependency is None
                        or dependency != ReceiptDependency.capture(identity, armed)
                    ):
                        raise InvocationStateError(
                            "native timer arming evidence is inconsistent"
                        ) from None
                    suspended = suspension
                    return RequestInput(
                        interrupt_id="owa-timer:" + suspension.timer_key,
                        payload=suspension.as_value(),
                    )

            return await ctx.run_node(
                FunctionNode(func=dynamic_node, name="owa_plan", rerun_on_resume=True),
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
        if wake_id is not None:
            message = types.Content(
                role="user",
                parts=[
                    types.Part(
                        function_response=types.FunctionResponse(
                            id=wake_id, name="adk_request_input", response=wake
                        )
                    )
                ],
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
        if suspended is not None:
            raise suspended
        return output if output is not None else result_value

    async def scheduled_state_exists(
        self,
        workflow_input: Any,
        *,
        session_id: str,
        user_id: str | None,
        invocation_id: str,
        database_path: str,
        _recovery_context: AdkRecoveryContext,
    ) -> bool:
        postgresql = is_postgresql_datasource(database_path)
        if not postgresql and not os.path.exists(database_path):
            # A read predicate must not create an SDK database merely to discover
            # that this atomically bound invocation has not been dispatched yet.
            return False
        if not postgresql:
            readonly_path = Path(database_path).resolve().as_uri() + "?mode=ro"
            try:
                with closing(sqlite3.connect(readonly_path, uri=True)) as connection:
                    tables = {
                        row[0]
                        for row in connection.execute(
                            "SELECT name FROM sqlite_master WHERE type = 'table'"
                        )
                    }
                if not tables:
                    return False
                if not {"sessions", "events", "app_states", "user_states"}.issubset(tables):
                    raise InvocationStateError("native scheduled storage schema is incomplete")
            except sqlite3.Error:
                raise InvocationStateError("native scheduled storage could not be read") from None
        sessions = self._session_service(database_path)
        if not postgresql:
            # Use the SDK's decoder/queries on a read-only connection. The
            # current schema was checked above; no setup DDL or new database.
            sessions._db_connect_path = readonly_path
            sessions._db_connect_uri = True
            sessions._schema_ready = True
        try:
            if postgresql:
                # Inspect schema through the SDK's engine without creating or
                # repairing native tables as a side effect of this predicate.
                text = import_module("sqlalchemy").text
                schema_utils = import_module("google.adk.sessions.migration._schema_check_utils")
                async with sessions.db_engine.connect() as connection:
                    connection = await connection.execution_options(postgresql_readonly=True)
                    rows = await connection.execute(
                        text(
                            "SELECT table_name FROM information_schema.tables "
                            "WHERE table_schema = 'owa_adk'"
                        )
                    )
                    tables = set(rows.scalars())
                    if not tables:
                        return False
                    if not {"sessions", "events", "app_states", "user_states"}.issubset(tables):
                        raise InvocationStateError("native scheduled storage schema is incomplete")
                    sessions._db_schema_version = await connection.run_sync(
                        schema_utils.get_db_schema_version_from_connection
                    )
                sessions._tables_created = True
            saved = await sessions.get_session(
                app_name="open-workflow-agent", user_id=user_id or "owa-user", session_id=session_id
            )
            if saved is None:
                return False
            owned = [event for event in saved.events if event.invocation_id == invocation_id]
            if not owned:
                return False
            self._validate_resume_session(saved, invocation_id, workflow_input, _recovery_context)
            if all(event.author == "user" for event in owned):
                return False
            _ReceiptEvents(_recovery_context).restore(saved, invocation_id)
            _timer_requests(saved, invocation_id, _recovery_context)
            return True
        except InvocationStateError:
            raise
        except Exception:
            raise InvocationStateError("native scheduled storage could not be read") from None
        finally:
            await self._close_sessions(sessions)

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
        _recovery_context: AdkRecoveryContext | None = None,
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
            _recovery_context=_recovery_context,
        )
