"""Fenced SDK transactions with atomic values, vectors and native markers."""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib import import_module
from typing import Any

from langgraph.store.base import PutOp
from open_workflow_agent.errors import InvocationStateConflict, OwaError, ToolError
from open_workflow_agent.memory_capacity import TABLE as NAMESPACE_TABLE
from open_workflow_agent.memory_mutations import (
    MAX_MUTATIONS,
    TABLE,
    MemoryMutation,
    MemoryMutationMarker,
    mutation_error,
)
from open_workflow_agent.ownership import (
    NativeWriteFence,
    install_sqlite_native_fence,
    run_native_storage_write,
    translate_native_fence_error,
)
from open_workflow_agent.persistence import ExecutionHandle, InvocationStore
from open_workflow_agent.storage import is_postgresql_datasource

from .native_memory_capacity import (
    NAMESPACE_DDL,
    check_capacity,
    expired_deletes,
    guard_namespace,
    verify_cleanup,
)
from .native_memory_embeddings import FrozenMemoryEmbedding, index_config


async def prepare_mutation_storage(store: Any, *, postgresql: bool) -> None:
    """Deployment setup only; no workflow data or ownership token is stored."""
    ddl = (
        f"CREATE TABLE IF NOT EXISTS {TABLE} (identity TEXT PRIMARY KEY, "
        "invocation_id TEXT NOT NULL,fingerprint TEXT NOT NULL,request_digest TEXT NOT NULL)"
    )
    if postgresql:
        async with store._cursor(pipeline=False) as cursor:
            async with cursor.connection.transaction():
                await cursor.execute("SELECT pg_advisory_xact_lock(%s,%s)", (0x4F5741, 0x4D573031))
                await cursor.execute(ddl)
                await cursor.execute(NAMESPACE_DDL)
                await cursor.execute(
                    "CREATE INDEX IF NOT EXISTS owa_memory_mutations_invocation_v1 "
                    f"ON {TABLE}(invocation_id)"
                )
    else:
        async with store.lock:
            try:
                await store.conn.execute(ddl)
                await store.conn.execute(NAMESPACE_DDL)
                await store.conn.execute(
                    "CREATE INDEX IF NOT EXISTS owa_memory_mutations_invocation_v1 "
                    f"ON {TABLE}(invocation_id)"
                )
                await store.conn.commit()
            except BaseException:
                await store.conn.rollback()
                raise


async def _apply_in_transaction(
    store: Any, cursor: Any, request: MemoryMutation, *, postgresql: bool
) -> MemoryMutationMarker:
    marker = MemoryMutationMarker.for_request(request)
    placeholder = "%s" if postgresql else "?"
    # The SQLite UPDATE fires the connection-local ownership trigger even on a
    # repeated operation. A no-row delete is protected by the marker INSERT.
    if not postgresql:
        await cursor.execute(
            f"UPDATE {TABLE} SET request_digest=request_digest WHERE identity=?", (marker.identity,)
        )
    size = "octet_length" if postgresql else "length"
    await cursor.execute(
        "SELECT "
        + ",".join(
            f"CASE WHEN {size}({column})<={bound} THEN {column} ELSE NULL END AS {column}"
            for column, bound in (
                ("identity", 64),
                ("invocation_id", 256),
                ("fingerprint", 256),
                ("request_digest", 64),
            )
        )
        + f" FROM {TABLE} WHERE identity={placeholder}",
        (marker.identity,),
    )
    row = await cursor.fetchone()
    if row is not None:
        values = (
            tuple(
                row[column]
                for column in ("identity", "invocation_id", "fingerprint", "request_digest")
            )
            if postgresql
            else tuple(row)
        )
        if values != (
            marker.identity,
            marker.invocation_id,
            marker.fingerprint,
            marker.request_digest,
        ):
            raise mutation_error("request_changed")
        return marker
    await cursor.execute(
        f"SELECT COUNT(*) AS count FROM {TABLE} WHERE invocation_id={placeholder}",
        (marker.invocation_id,),
    )
    row = await cursor.fetchone()
    if (row["count"] if postgresql else row[0]) >= MAX_MUTATIONS:
        raise mutation_error("mutation_limit")
    await cursor.execute(
        f"INSERT INTO {TABLE} VALUES ({','.join([placeholder] * 4)})",
        (marker.identity, marker.invocation_id, marker.fingerprint, marker.request_digest),
    )
    namespace = ("owa_native_memory_v1", request.namespace.rsplit(":", 1)[1], request.collection)
    prefix = await guard_namespace(cursor, request, namespace, postgresql=postgresql)
    if request.kind == "sweep":
        assert request.namespace_limits is not None
        operations = await expired_deletes(
            cursor, prefix, namespace, request.namespace_limits, postgresql=postgresql
        )
    else:
        operations = [
            PutOp(
                namespace,
                request.key,
                None if request.value_json is None else json.loads(request.value_json),
                index=None if request.semantic is not None and request.kind == "put" else False,
                ttl=None,
            )
        ]
    # Call the pinned SDK's original batch implementation/SQL, bypassing its
    # batching queue so this one operation uses our already owned transaction.
    store._mutation_cursor = cursor
    try:
        if operations:
            await store.abatch(operations)
    finally:
        store._mutation_cursor = None
    if request.kind == "sweep":
        await verify_cleanup(cursor, prefix, operations, postgresql=postgresql)
    if request.kind == "put" and request.retention is not None:
        await _set_record_expiry(cursor, namespace, request, postgresql=postgresql)
    if request.kind == "put" and request.namespace_limits is not None:
        await check_capacity(cursor, prefix, request.namespace_limits, postgresql=postgresql)
    if not postgresql:
        # Recheck lease time at the end, including a delete of an absent item.
        await cursor.execute(
            f"UPDATE {TABLE} SET request_digest=request_digest WHERE identity=?", (marker.identity,)
        )
    return marker


async def _set_record_expiry(
    cursor: Any, namespace: tuple[str, ...], request: MemoryMutation, *, postgresql: bool
) -> None:
    assert request.retention is not None
    if postgresql:
        from langgraph.store.postgres.base import _namespace_to_text

        await cursor.execute(
            "UPDATE store SET expires_at=clock_timestamp()+(%s*interval '1 second'),"
            "ttl_minutes=NULL WHERE prefix=%s AND key=%s",
            (request.retention.ttl_seconds, _namespace_to_text(namespace), request.key),
        )
    else:
        from langgraph.store.sqlite.base import _namespace_to_text

        await cursor.execute(
            "UPDATE store SET expires_at=strftime('%Y-%m-%d %H:%M:%f','now',?),"
            "ttl_minutes=NULL WHERE prefix=? AND key=?",
            (
                f"+{request.retention.ttl_seconds} seconds",
                _namespace_to_text(namespace),
                request.key,
            ),
        )

    if cursor.rowcount != 1:
        raise mutation_error("expiry_update_unavailable")


async def apply_native_mutation(
    database: str,
    request: MemoryMutation,
    fence: NativeWriteFence,
    *,
    invocations: InvocationStore,
    handle: ExecutionHandle,
    vector: list[float] | None = None,
) -> MemoryMutationMarker:
    """Trusted, preauthorized internal primitive; never register as a tool.

    Use a worker-owned SDK connection and drain cancellation before releasing
    ownership. A marker and SDK mutation commit together in the native DB;
    its caller coordinates common intent/completion separately.
    """
    if not isinstance(request, MemoryMutation) or not isinstance(fence, NativeWriteFence):
        raise mutation_error("request_invalid")
    request.verify_invocation(invocations, handle)
    if request.semantic is not None and request.kind == "put" and vector is None:
        raise mutation_error("embedding_missing")
    postgresql = is_postgresql_datasource(database)
    if (
        ("postgresql" if postgresql else "sqlite") != request.binding.backend
        or postgresql != is_postgresql_datasource(fence.database)
        or fence.lease.invocation_id != request.parent.invocation_id
        or fence.lease.workflow_fingerprint != request.parent.workflow_fingerprint
        or fence.lease != handle._execution_lease
    ):
        raise InvocationStateConflict("native memory mutation ownership is inconsistent")

    async def write() -> MemoryMutationMarker:
        module = import_module(
            "langgraph.store.postgres.aio" if postgresql else "langgraph.store.sqlite.aio"
        )
        base = module.AsyncPostgresStore if postgresql else module.AsyncSqliteStore

        class AtomicStore(base):  # type: ignore[misc,valid-type]
            _mutation_cursor: Any = None

            @asynccontextmanager
            async def _cursor(self, **kwargs: Any) -> AsyncIterator[Any]:
                if self._mutation_cursor is not None:
                    yield self._mutation_cursor
                else:
                    async with super()._cursor(**kwargs) as cursor:
                        yield cursor

        connection_string = database
        if postgresql:
            from psycopg.conninfo import make_conninfo

            connection_string = make_conninfo(database, connect_timeout=5)
        options = {}
        if request.semantic is not None:
            text = (
                request.semantic.text(json.loads(request.value_json))
                if request.value_json is not None
                else ""
            )
            options["index"] = index_config(
                request.semantic, FrozenMemoryEmbedding(text, vector or ()), postgresql=postgresql
            )
        async with AtomicStore.from_conn_string(connection_string, **options) as store:
            if postgresql:
                async with store._cursor(pipeline=False) as cursor:
                    async with cursor.connection.transaction():
                        await cursor.execute("SET LOCAL statement_timeout=5000")
                        await cursor.execute("SET LOCAL lock_timeout=5000")
                        # Serialize this invocation's quota and repeat checks.
                        await cursor.execute(
                            "SELECT pg_advisory_xact_lock(%s,%s)",
                            (
                                0x4D573032,
                                int(
                                    hashlib.sha256(
                                        request.parent.invocation_id.encode()
                                    ).hexdigest()[:8],
                                    16,
                                )
                                % (2**31),
                            ),
                        )
                        statement, values = fence.postgresql_guard()
                        await cursor.execute(statement.replace("?", "%s"), values)
                        if await cursor.fetchone() is None:
                            raise InvocationStateConflict(
                                "native memory mutation ownership was lost"
                            )
                        marker = await _apply_in_transaction(
                            store, cursor, request, postgresql=True
                        )
                        await cursor.execute(statement.replace("?", "%s"), values)
                        if await cursor.fetchone() is None:
                            raise InvocationStateConflict(
                                "native memory mutation ownership was lost"
                            )
                        return marker
            if request.semantic is not None:
                await store.conn.execute("PRAGMA foreign_keys=ON")
                async with store.conn.execute("PRAGMA foreign_keys") as check:
                    if (await check.fetchone())[0] != 1:
                        raise mutation_error("vector_cascade_unavailable")
            await store.setup()
            await install_sqlite_native_fence(
                store.conn,
                fence,
                native_database=database,
                tables=("store", TABLE, NAMESPACE_TABLE)
                + (("store_vectors",) if request.semantic is not None else ()),
            )
            async with store.lock:
                await store.conn.execute("BEGIN IMMEDIATE")
                try:
                    async with store.conn.cursor() as cursor:
                        marker = await _apply_in_transaction(
                            store, cursor, request, postgresql=False
                        )
                    await store.conn.commit()
                    return marker
                except BaseException as error:
                    await store.conn.rollback()
                    translate_native_fence_error(error)
                    raise

    try:
        return await run_native_storage_write(write)
    except OwaError:
        raise
    except Exception:
        raise ToolError(
            "native memory mutation failed", details={"reason": "store_failure"}
        ) from None
