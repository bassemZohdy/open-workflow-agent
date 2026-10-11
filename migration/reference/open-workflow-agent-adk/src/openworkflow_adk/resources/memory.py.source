"""Small ADK memory-service adapters for local development and tests."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from google.adk.memory import BaseMemoryService
from google.adk.memory.base_memory_service import MemoryEntry, SearchMemoryResponse
from google.genai import types

from openworkflow_adk.models import MemoryConfig

_SQL_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_sql_identifier(value: str) -> str:
    """Validate the configured PostgreSQL table name before SQLAlchemy uses it."""
    if not _SQL_IDENTIFIER.fullmatch(value):
        raise ValueError(
            "postgres memory namespace must be a simple SQL identifier "
            "(letters, digits, and underscores)"
        )
    return value


class InMemoryMemoryService(BaseMemoryService):
    """A process-local memory service with deterministic token matching."""

    def __init__(self) -> None:
        self.entries: dict[tuple[str, str], list[MemoryEntry]] = {}

    async def add_session_to_memory(self, session: Any) -> None:
        events = getattr(session, "events", [])
        entries = [MemoryEntry(content=event.content) for event in events if event.content]
        await self.add_memory(
            app_name=session.app_name,
            user_id=session.user_id,
            memories=entries,
        )

    async def add_memory(
        self,
        *,
        app_name: str,
        user_id: str,
        memories: list[MemoryEntry],
        custom_metadata: dict[str, Any] | None = None,
    ) -> None:
        bucket = self.entries.setdefault((app_name, user_id), [])
        for entry in memories:
            if custom_metadata:
                entry = entry.model_copy(
                    update={"custom_metadata": {**entry.custom_metadata, **custom_metadata}}
                )
            bucket.append(entry)

    async def search_memory(
        self, *, app_name: str, user_id: str, query: str
    ) -> SearchMemoryResponse:
        tokens = set(query.lower().split())
        matches = [
            entry
            for entry in self.entries.get((app_name, user_id), [])
            if tokens.intersection(_content_text(entry.content).lower().split())
        ]
        return SearchMemoryResponse(memories=matches)


class FileMemoryService(InMemoryMemoryService):
    """A JSON-backed memory service suitable for a single local host."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.entries = {}
        if self.path.exists():
            data = json.loads(self.path.read_text())
            self.entries = {
                tuple(key.split("\x1f", 1)): [MemoryEntry.model_validate(item) for item in values]
                for key, values in data.items()
            }

    async def add_memory(self, **kwargs: Any) -> None:
        await super().add_memory(**kwargs)
        self.path.write_text(
            json.dumps(
                {
                    "\x1f".join(key): [entry.model_dump(mode="json") for entry in values]
                    for key, values in self.entries.items()
                }
            )
        )


class RedisMemoryService(InMemoryMemoryService):
    """Redis-backed memory service using JSON entries and token matching."""

    def __init__(self, url: str, namespace: str = "workflow-memory") -> None:
        import redis.asyncio as redis

        self.redis = redis.from_url(url, decode_responses=True)
        self.namespace = namespace

    def _key(self, app_name: str, user_id: str) -> str:
        return f"{self.namespace}:{app_name}:{user_id}"

    async def add_memory(
        self,
        *,
        app_name: str,
        user_id: str,
        memories: list[MemoryEntry],
        custom_metadata: dict[str, Any] | None = None,
    ) -> None:
        values = []
        for entry in memories:
            if custom_metadata:
                entry = entry.model_copy(
                    update={"custom_metadata": {**entry.custom_metadata, **custom_metadata}}
                )
            values.append(json.dumps(entry.model_dump(mode="json")))
        if values:
            await self.redis.rpush(self._key(app_name, user_id), *values)

    async def search_memory(
        self, *, app_name: str, user_id: str, query: str
    ) -> SearchMemoryResponse:
        values = await self.redis.lrange(self._key(app_name, user_id), 0, -1)
        tokens = set(query.lower().split())
        matches = []
        for value in values:
            entry = MemoryEntry.model_validate(json.loads(value))
            if tokens.intersection(_content_text(entry.content).lower().split()):
                matches.append(entry)
        return SearchMemoryResponse(memories=matches)

    async def close(self) -> None:
        await self.redis.aclose()


class PostgresMemoryService(InMemoryMemoryService):
    """SQLAlchemy-backed memory service for PostgreSQL or compatible databases."""

    def __init__(self, url: str, namespace: str = "workflow_memory") -> None:
        from sqlalchemy import Column, Integer, MetaData, String, Table, Text
        from sqlalchemy.ext.asyncio import create_async_engine

        namespace = _validate_sql_identifier(namespace)
        self.engine = create_async_engine(url)
        self.table = Table(
            namespace,
            MetaData(),
            Column("id", Integer, primary_key=True),
            Column("app_name", String, nullable=False),
            Column("user_id", String, nullable=False),
            Column("entry", Text, nullable=False),
        )
        self._initialized = False

    async def _ensure_table(self) -> None:
        if self._initialized:
            return
        async with self.engine.begin() as connection:
            await connection.run_sync(self.table.metadata.create_all)
        self._initialized = True

    async def add_memory(
        self,
        *,
        app_name: str,
        user_id: str,
        memories: list[MemoryEntry],
        custom_metadata: dict[str, Any] | None = None,
    ) -> None:
        from sqlalchemy import insert

        await self._ensure_table()
        values = []
        for entry in memories:
            if custom_metadata:
                entry = entry.model_copy(
                    update={"custom_metadata": {**entry.custom_metadata, **custom_metadata}}
                )
            values.append(
                {
                    "app_name": app_name,
                    "user_id": user_id,
                    "entry": json.dumps(entry.model_dump(mode="json")),
                }
            )
        if values:
            async with self.engine.begin() as connection:
                await connection.execute(insert(self.table), values)

    async def search_memory(
        self, *, app_name: str, user_id: str, query: str
    ) -> SearchMemoryResponse:
        from sqlalchemy import select

        await self._ensure_table()
        async with self.engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        select(self.table.c.entry).where(
                            self.table.c.app_name == app_name, self.table.c.user_id == user_id
                        )
                    )
                )
                .scalars()
                .all()
            )
        tokens = set(query.lower().split())
        matches = []
        for value in rows:
            entry = MemoryEntry.model_validate(json.loads(value))
            if tokens.intersection(_content_text(entry.content).lower().split()):
                matches.append(entry)
        return SearchMemoryResponse(memories=matches)

    async def close(self) -> None:
        await self.engine.dispose()


def _content_text(content: types.Content) -> str:
    return " ".join(part.text or "" for part in content.parts or [])


def create_memory_service(config: MemoryConfig) -> BaseMemoryService:
    """Create a supported local memory adapter from typed configuration."""
    if config.type == "in-memory":
        return InMemoryMemoryService()
    if config.type == "file":
        if not config.connection:
            raise ValueError("file memory requires a connection path")
        return FileMemoryService(config.connection)
    if config.type == "vertex":
        from google.adk.memory.vertex_ai_memory_bank_service import VertexAiMemoryBankService

        return VertexAiMemoryBankService(
            project=config.extra.get("project"),
            location=config.extra.get("location"),
            agent_engine_id=config.extra.get("agent_engine_id"),
        )
    if config.type == "redis":
        if not config.connection:
            raise ValueError("redis memory requires a connection URL")
        return RedisMemoryService(config.connection, config.namespace or "workflow-memory")
    if config.type == "postgres":
        if not config.connection:
            raise ValueError("postgres memory requires a connection URL")
        return PostgresMemoryService(config.connection, config.namespace or "workflow_memory")
    raise NotImplementedError(f"memory backend {config.type!r} is not available locally")
