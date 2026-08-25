"""Runtime datasource resolution and isolated storage connections."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunsplit

from .errors import ConfigurationError

DatasourceKind = Literal["sqlite", "postgresql"]


@dataclass(slots=True)
class StorageConnection:
    """Small DB-API compatibility wrapper used by common persistence services."""

    raw: Any
    kind: DatasourceKind

    @property
    def is_postgresql(self) -> bool:
        return self.kind == "postgresql"

    def execute(self, statement: str, parameters: tuple[Any, ...] = ()) -> Any:
        if self.is_postgresql:
            statement = statement.replace("?", "%s")
        return self.raw.execute(statement, parameters)

    def commit(self) -> None:
        self.raw.commit()

    def close(self) -> None:
        self.raw.close()


def datasource_kind(datasource: str | Path | None) -> DatasourceKind:
    """Return the supported backend kind for a configured datasource."""

    if datasource is None or str(datasource) in {":memory:", ""}:
        return "sqlite"
    scheme = urlparse(str(datasource)).scheme.lower()
    if scheme in {"", "sqlite"}:
        return "sqlite"
    if scheme in {"postgres", "postgresql"}:
        return "postgresql"
    raise ConfigurationError(
        "configured persistence datasource is unsupported",
        details={"scheme": scheme, "supported": ["sqlite", "postgresql"]},
    )


def is_postgresql_datasource(datasource: str | Path | None) -> bool:
    return datasource_kind(datasource) == "postgresql"


def resolve_datasource(datasource: str | None) -> str | None:
    """Normalize a configured SQLite or PostgreSQL datasource."""

    if not datasource:
        return None
    if datasource == ":memory:":
        return datasource
    parsed = urlparse(datasource)
    kind = datasource_kind(datasource)
    if kind == "postgresql":
        if parsed.scheme == "postgres":
            return urlunsplit(
                ("postgresql", parsed.netloc, parsed.path, parsed.query, parsed.fragment)
            )
        return datasource
    if parsed.scheme == "sqlite":
        raw_path = unquote(parsed.path)
        if os.name != "nt" and raw_path.startswith("//"):
            # A rooted path interpolated into sqlite:/// becomes sqlite:////
            # and urlparse retains both leading separators on POSIX.
            raw_path = raw_path[1:]
        if os.name == "nt" and raw_path.startswith("/") and len(raw_path) > 2:
            if raw_path[2] == ":":
                raw_path = raw_path[1:]
        path = Path(raw_path or parsed.netloc)
        if not path:
            raise ConfigurationError("sqlite datasource must include a database path")
        return str(path)
    return datasource


def namespaced_datasource(
    datasource: str,
    namespace: str,
    *,
    driver: str | None = None,
) -> str:
    """Add a PostgreSQL search path for engine-owned durable state."""

    if not is_postgresql_datasource(datasource):
        return datasource
    parsed = urlparse(datasource)
    scheme = "postgresql" if parsed.scheme == "postgres" else parsed.scheme
    if driver and scheme == "postgresql":
        scheme = f"postgresql+{driver}"
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["options"] = f"-csearch_path={namespace},public"
    return urlunsplit((scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def open_storage(database: str | Path, namespace: str) -> StorageConnection:
    """Open a namespaced SQLite or PostgreSQL storage connection."""

    value = str(database)
    kind = datasource_kind(value)
    if kind == "sqlite":
        path = resolve_datasource(value) or value
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        return StorageConnection(sqlite3.connect(path, check_same_thread=False), "sqlite")

    try:
        import psycopg
        from psycopg import sql
    except ImportError as exc:
        raise ConfigurationError(
            "PostgreSQL persistence requires the optional 'postgres' dependency",
            details={"datasource": "postgresql", "extra": "postgres"},
        ) from exc
    try:
        connection = psycopg.connect(value, autocommit=True)
        connection.execute(
            sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(namespace))
        )
        connection.execute(
            sql.SQL("SET search_path TO {}, public").format(sql.Identifier(namespace))
        )
        return StorageConnection(connection, "postgresql")
    except Exception as exc:
        raise ConfigurationError(
            "unable to connect to configured PostgreSQL datasource",
            details={"scheme": "postgresql", "namespace": namespace},
        ) from exc


def ensure_storage_namespace(database: str, namespace: str) -> None:
    """Create an engine-owned PostgreSQL namespace before native initialization."""

    if not is_postgresql_datasource(database):
        return
    connection = open_storage(database, namespace)
    connection.close()
