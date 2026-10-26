"""Persist the installed semantic index identity; never silently reindex data."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from open_workflow_agent.errors import ConfigurationError
from open_workflow_agent.memory_reads import MemoryReadBinding
from open_workflow_agent.memory_semantic import MemorySemanticIndex
from open_workflow_agent.model_responses import bounded_model_json
from open_workflow_agent.storage import is_postgresql_datasource

TABLE = "owa_memory_semantic_profiles_v1"


def exact_postgres_store(base: Any) -> Any:
    class ConditionAwarePostgresStore(base):  # type: ignore[misc,valid-type]
        @property
        def VECTOR_MIGRATIONS(self) -> Any:
            migrations = super().VECTOR_MIGRATIONS
            # The pinned async SDK ignores Migration.condition; its sync
            # implementation skips the last ANN migration for kind=flat.
            # Preserve the original SQL/version numbers and the same skip.
            if (
                len(migrations) != 3
                or migrations[-1].condition is None
                or "CREATE INDEX CONCURRENTLY" not in migrations[-1].sql
                or self.index_config["ann_index_config"]["kind"] != "flat"
                or migrations[-1].condition(self)
            ):
                raise ConfigurationError("installed semantic SDK migrations are incompatible")
            return migrations[:-1]

    return ConditionAwarePostgresStore


def payload(binding: MemoryReadBinding, policy: MemorySemanticIndex) -> str:
    return bounded_model_json(
        {"binding": {"id": binding.id, "version": binding.version}, "index": policy.descriptor},
        limit=4096,
    )


async def prepare_semantic_storage(
    store: Any, binding: MemoryReadBinding, collection: str, policy: MemorySemanticIndex | None
) -> None:
    postgresql = binding.backend == "postgresql"
    async with store._cursor(
        **({"pipeline": False} if postgresql else {"transaction": False})
    ) as cursor:
        if policy is None:
            exists = await cursor.execute(
                "SELECT to_regclass(%s) AS name"
                if postgresql
                else "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (TABLE,),
            )
            row = await exists.fetchone()
            if row is None or (row["name"] if postgresql else row[0]) is None:
                return
        else:
            await cursor.execute(
                f"CREATE TABLE IF NOT EXISTS {TABLE} "
                "(collection TEXT PRIMARY KEY,payload TEXT NOT NULL)"
            )
        placeholder = "%s" if postgresql else "?"
        size = "octet_length(payload)" if postgresql else "length(CAST(payload AS BLOB))"
        await cursor.execute(
            f"SELECT CASE WHEN {size}<=4096 THEN payload ELSE NULL END AS payload "
            f"FROM {TABLE} WHERE collection={placeholder}",
            (collection,),
        )
        row = await cursor.fetchone()
        if row is not None:
            retained = row["payload"] if postgresql else row[0]
            if policy is None or retained != payload(binding, policy):
                raise ConfigurationError(
                    "native memory semantic index requires an explicit migration"
                )
        elif policy is not None:
            # A first semantic binding may not reinterpret existing unindexed
            # records. Namespace components have fixed ASCII syntax and the
            # SDK uses a period-separated prefix on both pinned backends.
            pattern = "owa#_native#_memory#_v1.%." + collection.replace("_", "#_")
            await cursor.execute(
                "SELECT 1 FROM store WHERE prefix LIKE " + placeholder + " ESCAPE '#' LIMIT 1",
                (pattern,),
            )
            if await cursor.fetchone() is not None:
                raise ConfigurationError(
                    "semantic memory activation requires a new empty collection"
                )
            await cursor.execute(
                f"INSERT INTO {TABLE} VALUES ({placeholder},{placeholder})",
                (collection, payload(binding, policy)),
            )


def verify_semantic_storage(
    database: str, binding: MemoryReadBinding, collection: str, policy: MemorySemanticIndex
) -> None:
    postgresql = is_postgresql_datasource(database)
    size = "octet_length(payload)" if postgresql else "length(CAST(payload AS BLOB))"
    statement = (
        f"SELECT CASE WHEN {size}<=4096 THEN payload ELSE NULL END FROM {TABLE} WHERE collection="
        + ("%s" if postgresql else "?")
    )
    try:
        if postgresql:
            import psycopg

            with psycopg.connect(database, autocommit=True, connect_timeout=5) as connection:
                with connection.transaction():
                    connection.execute("SET TRANSACTION READ ONLY")
                    connection.execute("SET LOCAL statement_timeout=5000")
                    row = connection.execute(statement, (collection,)).fetchone()
        else:
            with closing(
                sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
            ) as connection:
                connection.execute("PRAGMA query_only=ON")
                row = connection.execute(statement, (collection,)).fetchone()
        if row is None or row[0] != payload(binding, policy):
            raise ValueError("index identity is missing or changed")
    except Exception:
        raise ConfigurationError(
            "native memory semantic index identity is unavailable or changed"
        ) from None
