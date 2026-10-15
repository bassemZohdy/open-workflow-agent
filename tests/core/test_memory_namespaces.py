"""Principal namespace rows cannot be read/deleted through another namespace."""

from __future__ import annotations

import os
import sqlite3
from uuid import uuid4

import pytest
from open_workflow_agent.memory import MemoryService


@pytest.fixture(params=["memory", "sqlite", "postgresql"])
def memory_service(request, tmp_path):
    if request.param == "postgresql":
        datasource = os.getenv("OWA_TEST_POSTGRES_URL")
        if not datasource:
            pytest.skip("actual PostgreSQL datasource required")
    else:
        datasource = str(tmp_path / "memory.sqlite3") if request.param == "sqlite" else None
    service = MemoryService(datasource)
    if request.param != "memory":
        assert service._connection.kind == request.param
    yield service
    service.close()


def test_same_business_labels_do_not_join_principal_namespaces(memory_service):
    marker = uuid4().hex
    a, b = "principal-a:" + marker, "principal-b:" + marker
    first = memory_service.add("same-correlation " + marker, {"namespace": b}, namespace=a)
    second = memory_service.add("same-correlation " + marker, {"namespace": a}, namespace=b)
    try:
        own_a = memory_service.search(marker, namespace=a)
        own_b = memory_service.search(marker, namespace=b)
        assert [row["id"] for row in own_a] == [first]
        assert [row["id"] for row in own_b] == [second]
        assert "namespace" not in own_a[0] and "namespace" not in own_b[0]
        assert own_a[0]["metadata"] == {"namespace": b}
        assert own_b[0]["metadata"] == {"namespace": a}
        assert memory_service.search(marker) == []
        assert memory_service.delete(first, namespace=b) is False
        assert memory_service.delete(first) is False
        assert [row["id"] for row in memory_service.search(marker, namespace=a)] == [first]
    finally:
        memory_service.delete(first, namespace=a)
        memory_service.delete(second, namespace=b)


def test_legacy_rows_are_not_adopted_by_authenticated_namespace(memory_service):
    marker = uuid4().hex
    legacy = memory_service.add(marker, {"user_id": "caller-label"})
    protected = memory_service.add(marker, {"user_id": "caller-label"}, namespace=marker)
    try:
        assert [row["id"] for row in memory_service.search(marker)] == [legacy]
        assert [row["id"] for row in memory_service.search(marker, namespace=marker)] == [protected]
        assert memory_service.delete(legacy, namespace=marker) is False
        assert memory_service.delete(protected) is False
    finally:
        memory_service.delete(legacy)
        memory_service.delete(protected, namespace=marker)


def test_sqlite_legacy_table_migration_keeps_existing_rows_in_legacy_namespace(tmp_path):
    path = tmp_path / "old-memory.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE memory(id INTEGER PRIMARY KEY,text TEXT,metadata TEXT,created REAL)"
        )
        connection.execute("INSERT INTO memory VALUES(7,'original','{}',1)")
    service = MemoryService(str(path))
    try:
        assert [row["id"] for row in service.search("original")] == [7]
        assert service.search("original", namespace="principal") == []
        assert service.delete(7, namespace="principal") is False
        assert service.search("original")[0]["id"] == 7
    finally:
        service.close()


@pytest.mark.parametrize(
    "namespace", ["", 1, True, "x" * 513], ids=["empty", "integer", "bool", "oversize"]
)
def test_memory_rejects_unbounded_or_nontext_namespace(namespace):
    service = MemoryService()
    try:
        with pytest.raises(ValueError):
            service.add("forbidden", namespace=namespace)
        assert service.search("forbidden") == []
    finally:
        service.close()
