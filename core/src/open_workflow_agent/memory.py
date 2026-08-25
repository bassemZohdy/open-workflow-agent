"""Long-term agent memory, separate from workflow checkpointing."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


class MemoryService:
    def __init__(self, database: str | None = None) -> None:
        self.database = database
        self._items: list[dict[str, Any]] = []
        self._connection: sqlite3.Connection | None = None
        if database:
            Path(database).parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(database, check_same_thread=False)
            self._connection.execute(
                """CREATE TABLE IF NOT EXISTS memory (
                    id INTEGER PRIMARY KEY,
                    text TEXT NOT NULL,
                    metadata TEXT,
                    created REAL NOT NULL
                )"""
            )
            self._connection.commit()

    def add(self, text: str, metadata: dict[str, Any] | None = None) -> int:
        metadata = metadata or {}
        if self._connection is None:
            self._items.append({"text": text, "metadata": metadata, "created": time.time()})
            return len(self._items)
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

    def delete(self, memory_id: int) -> None:
        if self._connection is None:
            index = memory_id - 1
            if 0 <= index < len(self._items):
                self._items.pop(index)
            return
        self._connection.execute("DELETE FROM memory WHERE id = ?", (memory_id,))
        self._connection.commit()

    def close(self) -> None:
        if self._connection:
            self._connection.close()
