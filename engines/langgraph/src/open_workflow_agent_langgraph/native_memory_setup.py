"""Cross-process setup exclusion without replacing the pinned SDK migrations."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib import import_module
from typing import Any


@asynccontextmanager
async def memory_setup_lock(store: Any, path: str, *, postgresql: bool) -> AsyncIterator[None]:
    if postgresql:
        # setup() includes CREATE INDEX CONCURRENTLY, so a transaction lock
        # cannot wrap it. This factory owns a single, unpooled connection;
        # closing that connection also releases the session lock after failure.
        parameters = (0x4D535031,)
        while True:
            cursor = await store.conn.execute(
                "SELECT pg_try_advisory_lock(%s,hashtext(current_schema())) AS acquired", parameters
            )
            row = await cursor.fetchone()
            if row["acquired"]:
                break
            # A blocking lock query retains a snapshot that can deadlock the
            # SDK's CREATE INDEX CONCURRENTLY. Short probes release snapshots.
            await asyncio.sleep(0.01)
        try:
            yield
        finally:
            await store.conn.execute(
                "SELECT pg_advisory_unlock(%s,hashtext(current_schema()))", parameters
            )
    elif path == ":memory:":
        yield  # Each in-memory connection owns a different database.
    else:
        aiosqlite = import_module("aiosqlite")

        # SDK executescript() commits its connection. A separate SQLite lock
        # database preserves exclusion across those commits and across workers.
        # The file persists; no stale lock survives connection/process exit.
        async with aiosqlite.connect(path + ".setup.sqlite3", timeout=0) as connection:
            while True:
                try:
                    await connection.execute("BEGIN IMMEDIATE")
                    break
                except sqlite3.OperationalError as error:
                    if error.sqlite_errorcode not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                        raise
                    # The provider's outer setup deadline bounds this wait.
                    await asyncio.sleep(0.01)
            try:
                yield
            finally:
                await connection.rollback()
