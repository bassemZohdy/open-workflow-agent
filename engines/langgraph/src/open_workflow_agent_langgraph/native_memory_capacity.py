"""Fenced namespace admission and bounded SDK cleanup inside native transactions."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from langgraph.store.base import PutOp
from open_workflow_agent.memory_capacity import TABLE, MemoryNamespaceBounds
from open_workflow_agent.memory_mutations import MemoryMutation, mutation_error

NAMESPACE_DDL = (
    f"CREATE TABLE IF NOT EXISTS {TABLE} (prefix TEXT PRIMARY KEY,policy_digest TEXT NOT NULL)"
)


def native_prefix(namespace: tuple[str, ...], *, postgresql: bool) -> str:
    if postgresql:
        from langgraph.store.postgres.base import _namespace_to_text
    else:
        from langgraph.store.sqlite.base import _namespace_to_text
    return str(_namespace_to_text(namespace))


async def guard_namespace(
    cursor: Any, request: MemoryMutation, namespace: tuple[str, ...], *, postgresql: bool
) -> str:
    prefix = native_prefix(namespace, postgresql=postgresql)
    placeholder = "%s" if postgresql else "?"
    if postgresql:
        # Every new mutation, including unbounded callers, takes this lock.
        await cursor.execute(
            "SELECT pg_advisory_xact_lock(%s,%s)",
            (0x4D4E3031, int(hashlib.sha256(prefix.encode()).hexdigest()[:8], 16) % (2**31)),
        )
    await cursor.execute(
        f"SELECT CASE WHEN length(policy_digest)=64 THEN policy_digest "
        f"ELSE NULL END AS policy_digest FROM {TABLE} WHERE prefix={placeholder}",
        (prefix,),
    )
    row = await cursor.fetchone()
    limits = request.namespace_limits
    if row is None:
        if limits is None:
            return prefix
        # Do not adopt unindexed legacy data or recreate a lost namespace seal.
        await cursor.execute(f"SELECT 1 FROM store WHERE prefix={placeholder} LIMIT 1", (prefix,))
        if await cursor.fetchone() is not None:
            raise mutation_error("namespace_unmanaged")
        await cursor.execute(
            f"INSERT INTO {TABLE}(prefix,policy_digest) VALUES ({placeholder},{placeholder})",
            (prefix, limits.digest),
        )
        await cursor.execute(
            f"SELECT policy_digest FROM {TABLE} WHERE prefix={placeholder}", (prefix,)
        )
        row = await cursor.fetchone()
    digest = None if row is None else row["policy_digest"] if postgresql else row[0]
    if limits is None or digest != limits.digest:
        raise mutation_error("namespace_policy_changed")
    return prefix


async def check_capacity(
    cursor: Any, prefix: str, limits: MemoryNamespaceBounds, *, postgresql: bool
) -> None:
    placeholder = "%s" if postgresql else "?"
    size = "octet_length(value::text)" if postgresql else "length(CAST(value AS BLOB))"
    await cursor.execute(
        "SELECT COUNT(*) AS records,COALESCE(SUM(value_bytes),0) AS value_bytes,"
        "COALESCE(MAX(CASE WHEN value_bytes IS NULL THEN 1 ELSE 0 END),0) AS invalid "
        f"FROM (SELECT {size} AS value_bytes FROM store WHERE prefix={placeholder} "
        f"ORDER BY key LIMIT {placeholder}) AS bounded_namespace_values",
        (prefix, limits.max_records + 1),
    )
    row = await cursor.fetchone()
    records, value_bytes, invalid = (
        (row["records"], row["value_bytes"], row["invalid"]) if postgresql else tuple(row)
    )
    if invalid or records > limits.max_records or value_bytes > limits.max_value_bytes:
        raise mutation_error("namespace_capacity_exceeded")


async def expired_deletes(
    cursor: Any,
    prefix: str,
    namespace: tuple[str, ...],
    limits: MemoryNamespaceBounds,
    *,
    postgresql: bool,
) -> list[PutOp]:
    placeholder = "%s" if postgresql else "?"
    expired = (
        "expires_at<=clock_timestamp()" if postgresql else "julianday(expires_at)<=julianday('now')"
    )
    order = "expires_at" if postgresql else "julianday(expires_at)"
    await cursor.execute(
        "SELECT CASE WHEN length(key)<=128 THEN key ELSE NULL END AS key "
        f"FROM store WHERE prefix={placeholder} AND expires_at IS NOT NULL AND {expired} "
        f"ORDER BY {order},key LIMIT {placeholder}",
        (prefix, limits.cleanup_limit),
    )
    rows = await cursor.fetchall()
    keys = [row["key"] if postgresql else row[0] for row in rows]
    if any(
        type(key) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", key)
        for key in keys
    ):
        raise mutation_error("namespace_record_invalid")
    return [PutOp(namespace, key, None, index=False, ttl=None) for key in keys]
