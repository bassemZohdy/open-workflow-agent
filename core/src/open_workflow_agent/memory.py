"""Long-term agent memory, separate from workflow checkpointing."""

from __future__ import annotations

import json
import time
from typing import Any

from .storage import StorageConnection, open_storage


class MemoryService:
    def __init__(self, database: str | None = None) -> None:
        self.database = database
        self._items: list[dict[str, Any]] = []
        self._next_id = 1
        self._connection: StorageConnection | None = None
        if database:
            self._connection = open_storage(database, "owa_memory")
            identifier_type = "BIGSERIAL" if self._connection.is_postgresql else "INTEGER"
            self._connection.execute(
                f"""CREATE TABLE IF NOT EXISTS memory (
                    id {identifier_type} PRIMARY KEY,
                    text TEXT NOT NULL,
                    metadata TEXT,
                    created REAL NOT NULL,
                    namespace TEXT NOT NULL DEFAULT ''
                )"""
            )
            if self._connection.is_postgresql:
                self._connection.execute(
                    "ALTER TABLE memory ADD COLUMN IF NOT EXISTS namespace TEXT NOT NULL DEFAULT ''"
                )
            elif "namespace" not in {
                row[1] for row in self._connection.execute("PRAGMA table_info(memory)").fetchall()
            }:
                self._connection.execute(
                    "ALTER TABLE memory ADD COLUMN namespace TEXT NOT NULL DEFAULT ''"
                )
            self._connection.commit()

    @staticmethod
    def _namespace(namespace: str | None) -> str:
        if namespace is None:
            return ""
        if not isinstance(namespace, str) or not namespace or len(namespace.encode()) > 512:
            raise ValueError("memory namespace must be an original bounded principal binding")
        return namespace

    def add(
        self, text: str, metadata: dict[str, Any] | None = None, *, namespace: str | None = None
    ) -> int:
        bound = self._namespace(namespace)
        metadata = metadata or {}
        if self._connection is None:
            identifier = self._next_id
            self._next_id += 1
            self._items.append(
                {
                    "id": identifier,
                    "text": text,
                    "metadata": metadata,
                    "created": time.time(),
                    "namespace": bound,
                }
            )
            return identifier
        if self._connection.is_postgresql:
            cursor = self._connection.execute(
                "INSERT INTO memory(text, metadata, created, namespace) "
                "VALUES (?, ?, ?, ?) RETURNING id",
                (text, json.dumps(metadata), time.time(), bound),
            )
            identifier = cursor.fetchone()[0]
            self._connection.commit()
            return int(identifier)
        cursor = self._connection.execute(
            "INSERT INTO memory(text, metadata, created, namespace) VALUES (?, ?, ?, ?)",
            (text, json.dumps(metadata), time.time(), bound),
        )
        self._connection.commit()
        return int(cursor.lastrowid or 0)

    def search(
        self, query: str, limit: int = 10, *, namespace: str | None = None
    ) -> list[dict[str, Any]]:
        bound = self._namespace(namespace)
        terms = set(query.lower().split())
        if self._connection is None:
            rows = [
                {key: value for key, value in row.items() if key != "namespace"}
                for row in self._items
                if row["namespace"] == bound
            ]
        else:
            rows = [
                {
                    "id": row[0],
                    "text": row[1],
                    "metadata": json.loads(row[2] or "{}"),
                    "created": row[3],
                }
                for row in self._connection.execute(
                    "SELECT id, text, metadata, created FROM memory WHERE namespace=?", (bound,)
                )
            ]
        ranked = sorted(
            rows,
            key=lambda row: sum(term in row["text"].lower() for term in terms),
            reverse=True,
        )
        return [
            row for row in ranked if not terms or any(term in row["text"].lower() for term in terms)
        ][:limit]

    def delete(self, memory_id: int, *, namespace: str | None = None) -> bool:
        bound = self._namespace(namespace)
        if self._connection is None:
            before = len(self._items)
            self._items[:] = [
                item
                for item in self._items
                if item["id"] != memory_id or item["namespace"] != bound
            ]
            return len(self._items) != before
        cursor = self._connection.execute(
            "DELETE FROM memory WHERE id = ? AND namespace=?", (memory_id, bound)
        )
        self._connection.commit()
        return int(cursor.rowcount) > 0

    def close(self) -> None:
        if self._connection:
            self._connection.close()
