from __future__ import annotations

import open_workflow_agent.storage as storage
import pytest
from open_workflow_agent.errors import ConfigurationError
from open_workflow_agent.storage import (
    StorageConnection,
    datasource_kind,
    ensure_storage_namespace,
    is_postgresql_datasource,
    namespaced_datasource,
    open_storage,
    resolve_datasource,
)


def test_storage_datasource_normalization_and_namespaces(tmp_path):
    assert datasource_kind(None) == "sqlite"
    assert datasource_kind(":memory:") == "sqlite"
    assert datasource_kind("sqlite:///tmp/test.db") == "sqlite"
    assert datasource_kind("postgres://user@db.example/app") == "postgresql"
    assert is_postgresql_datasource("postgresql://db.example/app") is True
    assert resolve_datasource(":memory:") == ":memory:"
    assert resolve_datasource("sqlite:///tmp/test.db").endswith(
        "tmp\\test.db"
    ) or resolve_datasource("sqlite:///tmp/test.db").endswith("tmp/test.db")
    assert resolve_datasource("postgres://db.example/app") == "postgresql://db.example/app"
    assert resolve_datasource(None) is None
    assert resolve_datasource("") is None
    assert namespaced_datasource("sqlite:///tmp/test.db", "owa") == "sqlite:///tmp/test.db"
    namespaced = namespaced_datasource(
        "postgres://user:pass@db.example/app?sslmode=require", "owa_runtime", driver="asyncpg"
    )
    assert namespaced.startswith("postgresql+asyncpg://")
    assert "search_path" in namespaced
    with pytest.raises(ConfigurationError, match="unsupported"):
        datasource_kind("mysql://db.example/app")


def test_storage_connection_adapts_postgres_placeholders():
    class Raw:
        def __init__(self):
            self.statements = []
            self.committed = False
            self.closed = False

        def execute(self, statement, parameters):
            self.statements.append((statement, parameters))
            return "result"

        def commit(self):
            self.committed = True

        def close(self):
            self.closed = True

    raw = Raw()
    connection = StorageConnection(raw, "postgresql")
    assert connection.is_postgresql is True
    assert connection.execute("SELECT ?", (1,)) == "result"
    connection.commit()
    connection.close()
    assert raw.statements == [("SELECT %s", (1,))]
    assert raw.committed and raw.closed


def test_open_sqlite_storage_and_namespace_helper(tmp_path):
    database = tmp_path / "nested" / "runtime.sqlite3"
    connection = open_storage(database, "owa_runtime")
    try:
        connection.execute("CREATE TABLE sample (value TEXT)")
        connection.commit()
    finally:
        connection.close()
    ensure_storage_namespace(str(database), "owa_runtime")


def test_storage_handles_windows_sqlite_urls_and_missing_postgres_dependency(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(storage.os, "name", "nt")
    assert storage.resolve_datasource("sqlite:///C:/owa/runtime.sqlite3").replace("\\", "/") == (
        "C:/owa/runtime.sqlite3"
    )
    monkeypatch.setattr(storage.os, "name", "posix")
    assert storage.resolve_datasource("sqlite:////tmp/test.db").endswith(
        "tmp/test.db"
    ) or storage.resolve_datasource("sqlite:////tmp/test.db").endswith("tmp\\test.db")

    def missing_import(_name: str):
        raise ImportError("psycopg missing")

    monkeypatch.setattr(storage, "import_module", missing_import)
    with pytest.raises(ConfigurationError, match="optional 'postgres'"):
        storage.open_storage("postgresql://db.example/app", "owa")


def test_open_storage_initializes_postgres_namespace_with_driver_stubs(monkeypatch):
    class Raw:
        def __init__(self):
            self.statements = []
            self.closed = False

        def execute(self, statement, parameters=()):
            self.statements.append((statement, parameters))

        def close(self):
            self.closed = True

    raw = Raw()

    class Psycopg:
        @staticmethod
        def connect(value, autocommit):
            assert value.startswith("postgresql://")
            assert autocommit is True
            return raw

    class Sql:
        class Statement:
            def __init__(self, value):
                self.value = value

            def format(self, *values):
                return self.value.format(*values)

        @staticmethod
        def SQL(value):
            return Sql.Statement(value)

        @staticmethod
        def Identifier(value):
            return value

    monkeypatch.setattr(
        storage,
        "import_module",
        lambda name: Psycopg if name == "psycopg" else Sql,
    )
    connection = storage.open_storage("postgresql://db.example/app", "owa")
    assert connection.is_postgresql is True
    assert len(raw.statements) == 2
    connection.close()
    assert raw.closed is True
    storage.ensure_storage_namespace("postgresql://db.example/app", "owa-again")

    class FailingPsycopg:
        @staticmethod
        def connect(_value, autocommit):
            del autocommit
            raise RuntimeError("connection failed")

    monkeypatch.setattr(
        storage,
        "import_module",
        lambda name: FailingPsycopg if name == "psycopg" else Sql,
    )
    with pytest.raises(ConfigurationError, match="unable to connect"):
        storage.open_storage("postgresql://db.example/app", "owa")
