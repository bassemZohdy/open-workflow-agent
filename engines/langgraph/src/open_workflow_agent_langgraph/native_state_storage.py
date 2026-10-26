"""Read-only, bounded access to the pinned SDK's existing checkpoint format."""

from __future__ import annotations

import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib import import_module
from pathlib import Path
from time import monotonic
from typing import Any
from uuid import UUID

from open_workflow_agent.config import NativeStateConfig
from open_workflow_agent.native_state import NativeStateUnavailable
from open_workflow_agent.storage import is_postgresql_datasource

from . import native as native_module

_ROWS = 2048
_TYPES = {"msgpack", "json", "null", "bytes", "bytearray", "empty"}


def _id(value: Any, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or len(value) != 36 or str(UUID(value)) != value:
        raise ValueError("checkpoint identity format is invalid")
    return value


async def _rows(saver: Any, sql: str, parameters: tuple[Any, ...], postgres: bool) -> list[Any]:
    if postgres:
        async with saver.conn.cursor() as cursor:
            await cursor.execute(sql.replace("?", "%s"), parameters)
            return list(await cursor.fetchall())
    async with saver.conn.execute(sql, parameters) as cursor:
        return list(await cursor.fetchall())


def _values(row: Any) -> tuple[Any, ...]:
    return tuple(row.values()) if isinstance(row, dict) else tuple(row)


@asynccontextmanager
async def readonly_saver(path: str, bounds: NativeStateConfig) -> AsyncIterator[tuple[Any, bool]]:
    postgres_saver = getattr(native_module, "AsyncPostgresSaver", None)
    sqlite_saver = getattr(native_module, "AsyncSqliteSaver", None)
    if is_postgresql_datasource(path):
        if postgres_saver is None:
            raise NativeStateUnavailable("native state storage is unavailable")
        conninfo = import_module("psycopg.conninfo")
        path = conninfo.make_conninfo(
            path, connect_timeout=max(1, math.ceil(bounds.timeout_seconds))
        )
        async with postgres_saver.from_conn_string(path) as saver:
            async with saver.conn.transaction():
                await saver.conn.execute(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                )
                timeout = str(max(1, math.ceil(bounds.timeout_seconds * 1000)))
                await saver.conn.execute(
                    "SELECT set_config('statement_timeout', %s, true)", (timeout,)
                )
                await saver.conn.execute("SELECT set_config('lock_timeout', %s, true)", (timeout,))
                rows = await _rows(
                    saver,
                    "SELECT table_name FROM information_schema.tables WHERE "
                    "table_schema=current_schema() AND table_type='BASE TABLE' "
                    "AND table_name IN ('checkpoints','checkpoint_blobs','checkpoint_writes')",
                    (),
                    True,
                )
                tables = {_values(row)[0] for row in rows}
                if not {"checkpoints", "checkpoint_blobs", "checkpoint_writes"} <= tables:
                    raise NativeStateUnavailable("native state storage is absent or incomplete")
                yield saver, True
        return
    if sqlite_saver is None or path == ":memory:" or not Path(path).is_file():
        raise NativeStateUnavailable("native state storage is unavailable")
    aiosqlite = import_module("aiosqlite")
    async with aiosqlite.connect(
        Path(path).resolve().as_uri() + "?mode=ro", uri=True
    ) as connection:
        await connection.execute("PRAGMA query_only=ON")
        await connection.execute("PRAGMA busy_timeout=1000")
        deadline = monotonic() + bounds.timeout_seconds
        await connection.set_progress_handler(lambda: int(monotonic() >= deadline), 1000)
        await connection.execute("BEGIN")
        saver = sqlite_saver(connection)
        rows = await _rows(
            saver,
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('checkpoints','writes')",
            (),
            False,
        )
        if not {"checkpoints", "writes"} <= {_values(row)[0] for row in rows}:
            raise NativeStateUnavailable("native state storage is absent or incomplete")
        saver.is_setup = True  # Existing format only: the read path never calls SDK setup DDL.
        try:
            yield saver, False
        finally:
            await connection.rollback()


async def checkpoint_index(
    saver: Any, thread: str, bounds: NativeStateConfig, postgres: bool
) -> tuple[list[tuple[str, str | None]], bool]:
    rows = await _rows(
        saver,
        "SELECT CASE WHEN length(checkpoint_id)<=128 THEN checkpoint_id ELSE NULL END AS id, "
        "CASE WHEN parent_checkpoint_id IS NULL OR length(parent_checkpoint_id)<=128 "
        "THEN parent_checkpoint_id ELSE 'invalid' END AS parent "
        "FROM checkpoints WHERE thread_id=? AND checkpoint_ns='' "
        "ORDER BY checkpoints.checkpoint_id DESC LIMIT ?",
        (thread, bounds.max_history + 1),
        postgres,
    )
    result = []
    for row in rows[: bounds.max_history]:
        key, parent = _values(row)
        checked = _id(key)
        if checked is None:
            raise ValueError("checkpoint identity is missing")
        result.append((checked, _id(parent, nullable=True)))
    return result, len(rows) > bounds.max_history


def _guard_queries(thread: str, key: str, postgres: bool) -> list[tuple[str, tuple[Any, ...]]]:
    parameters = (thread, key)
    if postgres:
        header = (
            "SELECT octet_length(checkpoint::text) AS checkpoint_bytes, "
            "octet_length(metadata::text) AS metadata_bytes, "
            "CASE WHEN checkpoint->>'v'='4' AND jsonb_typeof(checkpoint->'v')='number' "
            "THEN 1 ELSE 0 END AS supported_version "
            "FROM checkpoints WHERE thread_id=? AND checkpoint_ns='' AND checkpoint_id=?",
            parameters,
        )
    else:
        header = (
            "SELECT length(checkpoint), length(CAST(metadata AS BLOB)), "
            "CASE WHEN length(type)<=32 THEN type ELSE NULL END FROM checkpoints "
            "WHERE thread_id=? AND checkpoint_ns='' AND checkpoint_id=?",
            parameters,
        )
    queries: list[tuple[str, tuple[Any, ...]]] = [header]
    if postgres:
        queries.append(
            (
                "SELECT CASE WHEN length(bl.type)<=32 THEN bl.type ELSE NULL END, "
                "COALESCE(octet_length(bl.blob),0) FROM checkpoints c, "
                "jsonb_each_text(c.checkpoint->'channel_versions') ch JOIN checkpoint_blobs bl "
                "ON bl.channel=ch.key AND bl.version=ch.value WHERE c.thread_id=? "
                "AND c.checkpoint_ns='' AND c.checkpoint_id=? AND bl.thread_id=c.thread_id "
                "AND bl.checkpoint_ns=c.checkpoint_ns LIMIT ?",
                (*parameters, _ROWS + 1),
            )
        )
    queries.append(
        (
            "SELECT CASE WHEN length(type)<=32 THEN type ELSE NULL END, "
            + (
                "octet_length(blob)+octet_length(task_id)+octet_length(task_path)"
                "+octet_length(channel) FROM checkpoint_writes "
                if postgres
                else "length(value)+length(CAST(task_id AS BLOB))+length(CAST(task_path AS BLOB))"
                "+length(CAST(channel AS BLOB)) FROM writes "
            )
            + "WHERE thread_id=? AND checkpoint_ns='' AND checkpoint_id=? LIMIT ?",
            (*parameters, _ROWS + 1),
        )
    )
    return queries


def _guard_header(headers: list[Any], budget: int, postgres: bool) -> int:
    if len(headers) != 1:
        raise ValueError("checkpoint disappeared")
    header = _values(headers[0])
    if postgres and header[2] != 1:
        raise ValueError("native checkpoint version is unsupported")
    if not postgres and header[2] not in _TYPES:
        raise ValueError("checkpoint serializer is unsupported")
    size = sum(value or 0 for value in header[:2])
    if type(size) is not int or not 0 < size <= budget:
        raise ValueError("checkpoint exceeds decoding budget")
    return size


def _guard_array(size: int, rows: list[Any], budget: int) -> int:
    if len(rows) > _ROWS:
        raise ValueError("native checkpoint array exceeds its bound")
    for row in rows:
        kind, amount = _values(row)
        if kind not in _TYPES or type(amount) is not int or amount < 0:
            raise ValueError("native checkpoint encoding is unsupported")
        size += amount
        if size > budget:
            raise ValueError("checkpoint exceeds decoding budget")
    return size


async def guard_checkpoint(saver: Any, thread: str, key: str, budget: int, postgres: bool) -> int:
    """Bound every serialized SDK value and pending-write array before its codec runs."""
    queries = _guard_queries(thread, key, postgres)
    size = _guard_header(await _rows(saver, *queries[0], postgres), budget, postgres)
    for query, parameters in queries[1:]:
        size = _guard_array(size, await _rows(saver, query, parameters, postgres), budget)
    return size


def guard_checkpoint_sync(
    connection: Any, thread: str, key: str, budget: int, postgres: bool
) -> int:
    queries = _guard_queries(thread, key, postgres)

    def rows(query: str, parameters: tuple[Any, ...]) -> list[Any]:
        cursor = connection.execute(query.replace("?", "%s") if postgres else query, parameters)
        try:
            return list(cursor.fetchall())
        finally:
            cursor.close()

    size = _guard_header(rows(*queries[0]), budget, postgres)
    for query, parameters in queries[1:]:
        size = _guard_array(size, rows(query, parameters), budget)
    return size
