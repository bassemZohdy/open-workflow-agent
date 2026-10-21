"""SQLite native mutations retain the ownership lock through their SDK commit."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest
from open_workflow_agent.errors import InvocationStateConflict, InvocationStateError
from open_workflow_agent.ownership import (
    ExecutionOwnershipStore,
    install_sqlite_native_fence,
    run_native_storage_write,
    translate_native_fence_error,
)
from open_workflow_agent.persistence import InvocationStore


class _Cursor:
    def __init__(self, cursor: sqlite3.Cursor) -> None:
        self.cursor = cursor

    async def fetchone(self) -> Any:
        return self.cursor.fetchone()

    async def close(self) -> None:
        self.cursor.close()


class _Connection:
    """Async-shaped access to a real SQLite connection; no simulated SQL."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    async def execute(self, statement: str, parameters: tuple[Any, ...] = ()) -> _Cursor:
        return _Cursor(self.connection.execute(statement, parameters))

    async def commit(self) -> None:
        self.connection.commit()

    async def rollback(self) -> None:
        self.connection.rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize("same_database", [False, True])
@pytest.mark.parametrize("mutation", ["INSERT", "UPDATE", "DELETE"])
async def test_native_mutation_locks_takeover_through_commit_and_rejects_stale_owner(
    tmp_path: Path, same_database: bool, mutation: str
) -> None:
    common_path = tmp_path / "runtime.sqlite3"
    native_path = common_path if same_database else tmp_path / "native.sqlite3"
    invocations = InvocationStore(common_path)
    ownership = ExecutionOwnershipStore(common_path)
    native = sqlite3.connect(native_path)
    concurrent = sqlite3.connect(common_path, timeout=0)
    try:
        ownership.connection.execute("PRAGMA journal_mode=WAL")
        native.execute("PRAGMA journal_mode=WAL")
        native.execute("CREATE TABLE checkpoints (id TEXT PRIMARY KEY, value TEXT)")
        native.execute("INSERT INTO checkpoints VALUES ('original', 'original')")
        native.execute("INSERT INTO checkpoints VALUES ('remaining', 'remaining')")
        native.commit()
        handle = invocations.create(
            engine="test",
            session_id=None,
            user_id=None,
            workflow_name="native-fence",
            workflow_version="1.0.0",
            workflow_fingerprint="definition",
        )
        lease = ownership.acquire(handle)
        await install_sqlite_native_fence(
            _Connection(native),
            ownership.native_fence(lease),
            native_database=str(native_path),
            tables=("checkpoints",),
        )
        sql = {
            "INSERT": "INSERT INTO checkpoints VALUES ('first', 'first')",
            "UPDATE": "UPDATE checkpoints SET value='first' WHERE id='original'",
            "DELETE": "DELETE FROM checkpoints WHERE id='original'",
        }[mutation]
        native.execute(sql)
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            concurrent.execute(
                "UPDATE invocation_ownership SET expires_at=1 WHERE invocation_id=?",
                (handle.invocation_id,),
            )
        concurrent.rollback()
        native.commit()
        accepted = native.execute("SELECT * FROM checkpoints ORDER BY id").fetchall()
        concurrent.execute(
            "UPDATE invocation_ownership SET expires_at=1 WHERE invocation_id=?",
            (handle.invocation_id,),
        )
        concurrent.commit()
        successor = ownership.acquire(handle)
        assert successor.generation == lease.generation + 1
        stale_sql = {
            "INSERT": "INSERT INTO checkpoints VALUES ('stale', 'stale')",
            "UPDATE": "UPDATE checkpoints SET value='stale' WHERE id='original'",
            "DELETE": "DELETE FROM checkpoints WHERE id='remaining'",
        }[mutation]
        with pytest.raises(sqlite3.IntegrityError) as failure:
            native.execute(stale_sql)
        with pytest.raises(InvocationStateConflict):
            translate_native_fence_error(failure.value)
        native.rollback()
        assert native.execute("SELECT * FROM checkpoints ORDER BY id").fetchall() == accepted
        ownership.verify(successor)
        assert not native.execute(
            "SELECT name FROM main.sqlite_master WHERE name LIKE 'owa_fence_%'"
        ).fetchall()
    finally:
        native.close()
        concurrent.close()
        ownership.close()
        invocations.close()


@pytest.mark.asyncio
async def test_native_fence_rejects_shadow_ownership_table_without_changing_either_database(
    tmp_path: Path,
) -> None:
    common_path, native_path = tmp_path / "runtime.sqlite3", tmp_path / "native.sqlite3"
    invocations = InvocationStore(common_path)
    ownership = ExecutionOwnershipStore(common_path)
    native = sqlite3.connect(native_path)
    try:
        handle = invocations.create(
            engine="test",
            session_id=None,
            user_id=None,
            workflow_name="native-fence",
            workflow_version="1.0.0",
            workflow_fingerprint="definition",
        )
        lease = ownership.acquire(handle)
        native.execute("CREATE TABLE invocation_ownership (value TEXT)")
        native.execute("CREATE TABLE checkpoints (value TEXT)")
        native.commit()
        with pytest.raises(InvocationStateError, match="inconsistent"):
            await install_sqlite_native_fence(
                _Connection(native),
                ownership.native_fence(lease),
                native_database=str(native_path),
                tables=("checkpoints",),
            )
        ownership.verify(lease)
        assert not native.execute("SELECT * FROM checkpoints").fetchall()
    finally:
        native.close()
        ownership.close()
        invocations.close()


def test_native_fence_does_not_translate_unrelated_storage_errors() -> None:
    translate_native_fence_error(sqlite3.IntegrityError("unrelated constraint"))
    translate_native_fence_error(RuntimeError("OWA_EXECUTION_OWNERSHIP_LOST"))


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_cancel_drains_native_storage_worker_before_releasing_connections(
    tmp_path: Path, failure: bool
) -> None:
    path = tmp_path / "native.sqlite3"
    entered, finish, closed = asyncio.Event(), threading.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    running = None

    async def write() -> int:
        connection = sqlite3.connect(path)
        try:
            connection.execute("CREATE TABLE writes (value INTEGER)")
            connection.execute("INSERT INTO writes VALUES (1)")
            loop.call_soon_threadsafe(entered.set)
            await asyncio.to_thread(finish.wait)
            if failure:
                connection.rollback()
                raise RuntimeError("storage transaction failed")
            connection.commit()
            return 1
        finally:
            connection.close()
            closed.set()

    try:
        running = asyncio.create_task(run_native_storage_write(write))
        await asyncio.wait_for(entered.wait(), 10)
        running.cancel()
        await asyncio.sleep(0)
        assert not running.done() and not closed.is_set()
        running.cancel()  # Repeated shutdown/caller cancellation must still drain.
        await asyncio.sleep(0)
        assert not running.done() and not closed.is_set()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(running, 10)
        assert closed.is_set()
        connection = sqlite3.connect(path)
        try:
            assert connection.execute("SELECT value FROM writes").fetchall() == (
                [] if failure else [(1,)]
            )
        finally:
            connection.close()
        path.rename(tmp_path / "closed.sqlite3")
    finally:
        finish.set()
        if running is not None:
            await asyncio.gather(running, return_exceptions=True)
