"""Native readers must release locks while the common loop waits for a writer."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from open_workflow_agent.errors import WorkflowExecutionError
from open_workflow_agent.ownership import ExecutionOwnershipStore
from open_workflow_agent.persistence import InvocationStore
from open_workflow_agent_adk.native import NativeAdkRunner


@pytest.mark.asyncio
@pytest.mark.parametrize("native_reader_worker", [False, True])
async def test_sdk_reader_releases_lock_during_synchronous_common_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native_reader_worker: bool
) -> None:
    if not NativeAdkRunner().available:
        pytest.skip("independent native ADK environment is required")
    from google.adk.events import Event
    from google.adk.sessions.sqlite_session_service import SqliteSessionService
    from google.genai import types

    common = tmp_path / "common.sqlite3"
    native = tmp_path / "native.sqlite3"
    store = InvocationStore(common)
    ownership = ExecutionOwnershipStore(common)
    handle = store.create(
        engine="adk",
        session_id=None,
        user_id=None,
        workflow_name="read-drain",
        workflow_version="1.0.0",
        workflow_fingerprint="read-drain",
    )
    lease = ownership.acquire(handle)
    handle._execution_lease = lease
    store.connection.execute("PRAGMA busy_timeout=300")
    sessions = NativeAdkRunner()._session_service(str(native))
    session = await sessions.create_session(
        app_name="open-workflow-agent", user_id="owa-user", session_id=handle.session_id
    )
    sessions._execution_fence = ownership.native_fence(lease)
    reader_started = threading.Event()
    writer_ready = threading.Event()
    actual_get = SqliteSessionService.get_session
    original_connection = sessions._get_db_connection

    @asynccontextmanager
    async def connection() -> Any:
        async with original_connection() as db:
            commit = db.commit

            async def ready_commit() -> None:
                writer_ready.set()
                await commit()

            db.commit = ready_commit
            yield db

    async def held_reader(self: Any, **kwargs: Any) -> Any:
        reader = sqlite3.connect(native)
        try:
            reader.execute("BEGIN")
            reader.execute("SELECT * FROM sessions").fetchone()
            reader_started.set()
            async with asyncio.timeout(5):
                while not writer_ready.is_set():
                    await asyncio.sleep(0.005)
            await asyncio.sleep(0.03)
            reader.rollback()
        finally:
            reader.close()
        return await actual_get(self, **kwargs)

    monkeypatch.setattr(sessions, "_get_db_connection", connection)
    monkeypatch.setattr(SqliteSessionService, "get_session", held_reader)
    if not native_reader_worker:
        # Faithful before-fix SDK call on the main loop, with the same real
        # SQLite read and fenced native transaction as the corrected case.
        monkeypatch.setattr(
            sessions, "get_session", lambda **kwargs: held_reader(sessions, **kwargs)
        )
    getter = asyncio.create_task(
        sessions.get_session(
            app_name="open-workflow-agent", user_id="owa-user", session_id=handle.session_id
        )
    )
    writer: asyncio.Task[Any] | None = None
    try:
        assert await asyncio.to_thread(reader_started.wait, 5)
        writer = asyncio.create_task(
            sessions.append_event(
                session=session,
                event=Event(
                    invocation_id="native-read-drain",
                    author="assistant",
                    content=types.Content(role="model", parts=[types.Part(text="saved")]),
                ),
            )
        )
        assert await asyncio.to_thread(writer_ready.wait, 5)
        before = handle._persisted_payload
        if native_reader_worker:
            store.update(handle, status="waiting")
            assert handle.status == "waiting"
        else:
            with pytest.raises(WorkflowExecutionError, match="could not be stored"):
                store.update(handle, status="waiting")
            assert handle._persisted_payload == before and handle.status == "running"
        assert await asyncio.wait_for(getter, 5) is not None
        await asyncio.wait_for(writer, 5)
    finally:
        getter.cancel()
        tasks = [getter]
        if writer is not None:
            writer.cancel()
            tasks.append(writer)
        await asyncio.gather(*tasks, return_exceptions=True)
        await NativeAdkRunner._close_sessions(sessions)
        ownership.close()
        store.close()
