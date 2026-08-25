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
                    created REAL NOT NULL
                )"""
            )
            self._connection.commit()

    def add(self, text: str, metadata: dict[str, Any] | None = None) -> int:
        metadata = metadata or {}
        if self._connection is None:
            identifier = self._next_id
            self._next_id += 1
            self._items.append(
                {"id": identifier, "text": text, "metadata": metadata, "created": time.time()}
            )
            return identifier
        if self._connection.is_postgresql:
            cursor = self._connection.execute(
                "INSERT INTO memory(text, metadata, created) VALUES (?, ?, ?) RETURNING id",
                (text, json.dumps(metadata), time.time()),
            )
            identifier = cursor.fetchone()[0]
            self._connection.commit()
            return int(identifier)
        cursor = self._connection.execute(
            "INSERT INTO memory(text, metadata, created) VALUES (?, ?, ?)",
            (text, json.dumps(metadata), time.time()),
        )
        self._connection.commit()
        return int(cursor.lastrowid or 0)

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        terms = set(query.lower().split())
        if self._connection is None:
            rows = self._items
        else:
            rows = [
                {
                    "id": row[0],
                    "text": row[1],
                    "metadata": json.loads(row[2] or "{}"),
                    "created": row[3],
                }
                for row in self._connection.execute(
                    "SELECT id, text, metadata, created FROM memory"
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

    def delete(self, memory_id: int) -> bool:
        if self._connection is None:
            before = len(self._items)
            self._items[:] = [item for item in self._items if item["id"] != memory_id]
            return len(self._items) != before
        cursor = self._connection.execute("DELETE FROM memory WHERE id = ?", (memory_id,))
        self._connection.commit()
        return int(cursor.rowcount) > 0

    def close(self) -> None:
        if self._connection:
            self._connection.close()
