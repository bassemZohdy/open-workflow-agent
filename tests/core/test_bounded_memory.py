"""Finite common memory with actual backend quotas and execution fencing."""

from __future__ import annotations

import hashlib
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from uuid import uuid4

import pytest
from open_workflow_agent.bounded_memory import BoundedMemoryService
from open_workflow_agent.errors import InvocationStateConflict, WorkflowExecutionError
from open_workflow_agent.memory import MemoryService
from open_workflow_agent.ownership import ExecutionOwnershipStore
from open_workflow_agent.persistence import InvocationStore


@dataclass(frozen=True)
class Bounds:
    enabled: bool = True
    id: str = "memory-test"
    version: str = "1"
    max_text_bytes: int = 128
    max_metadata_bytes: int = 128
    max_query_bytes: int = 128
    max_query_terms: int = 8
    max_results: int = 10
    max_entries: int = 8
    max_namespace_bytes: int = 2048
    max_scan_rows: int = 8
    retention_seconds: float | None = None
    sweep_limit: int = 2


def _namespace(principal):
    return "owa/principal-memory/v1:" + hashlib.sha256(principal.encode()).hexdigest()


@pytest.fixture(params=["sqlite", "postgresql"])
def memory_tree(tmp_path, request):
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if request.param == "postgresql" else None
    if request.param == "postgresql" and not datasource:
        pytest.skip("actual PostgreSQL is required for the memory ownership proof")
    database = datasource or str(tmp_path / "common.sqlite3")
    opened = []
    principal = "memory-principal-" + uuid4().hex

    def make(*, owner=None, bounds=None, seconds=120):
        store = InvocationStore(database)
        ownership = ExecutionOwnershipStore(database)
        service = BoundedMemoryService(store, bounds or Bounds())
        assert service._connection is store.connection
        assert service._connection.kind == request.param
        handle = store.create(
            engine="reference",
            session_id=None,
            user_id="same-untrusted-label",
            owner_principal=owner or principal,
            workflow_name="memory-" + uuid4().hex,
            workflow_version="1",
            workflow_fingerprint=uuid4().hex,
        )
        handle._execution_lease = ownership.acquire(handle, seconds=seconds)
        opened.append((store, ownership, service, handle))
        return service, handle, ownership

    yield make
    for store, ownership, service, handle in reversed(opened):
        if handle._execution_lease:
            try:
                ownership.release(handle._execution_lease)
            except InvocationStateConflict:
                # Actual expiry, takeover and operator revocation are the
                # evidence under test; an old handle cannot release them.
                pass
        connection = store.connection
        namespace = _namespace(handle.owner_principal)
        connection.execute("DELETE FROM bounded_memory_entries WHERE namespace=?", (namespace,))
        connection.execute("DELETE FROM bounded_memory_namespaces WHERE namespace=?", (namespace,))
        connection.commit()
        service.close()
        store.close()
        ownership.close()


def _arguments(handle):
    return {"namespace": _namespace(handle.owner_principal), "execution_handle": handle}


def _snapshot(service, handle):
    connection = service._connection
    namespace = _namespace(handle.owner_principal)
    return (
        connection.execute(
            "SELECT * FROM bounded_memory_entries WHERE namespace=? ORDER BY memory_id",
            (namespace,),
        ).fetchall(),
        connection.execute(
            "SELECT * FROM bounded_memory_namespaces WHERE namespace=?", (namespace,)
        ).fetchall(),
        connection.execute(
            "SELECT * FROM invocations WHERE invocation_id=?", (handle.invocation_id,)
        ).fetchall(),
        connection.execute(
            "SELECT * FROM invocation_ownership WHERE invocation_id=?", (handle.invocation_id,)
        ).fetchall(),
    )


def test_ranking_unicode_ties_and_empty_query_preserve_original_values(memory_tree):
    service, handle, _ = memory_tree()
    arguments = _arguments(handle)
    first = service.add("π alpha", {"original": None}, **arguments)
    second = service.add("π beta", {"unicode": "π"}, **arguments)
    third = service.add("π alpha beta", {}, **arguments)
    assert [row["id"] for row in service.search("π beta", **arguments)] == [second, third, first]
    assert [row["id"] for row in service.search("", **arguments)] == [first, second, third]
    assert service.search("alpha", limit=1, **arguments)[0]["metadata"] == {"original": None}
    assert service.search("no-match", **arguments) == []


@pytest.mark.parametrize("invalid", ["text", "metadata", "query", "terms", "limit", "identifier"])
def test_invalid_payload_never_creates_namespace_or_mutates_common_rows(memory_tree, invalid):
    service, handle, _ = memory_tree()
    before = _snapshot(service, handle)
    name, payload = {
        "text": ("add_memory", {"text": "π" * 65}),
        "metadata": ("add_memory", {"text": "text", "metadata": {"huge": "x" * 129}}),
        "query": ("search_memory", {"query": "x" * 129}),
        "terms": ("search_memory", {"query": " ".join(str(index) for index in range(9))}),
        "limit": ("search_memory", {"limit": True}),
        "identifier": ("delete_memory", {"id": True}),
    }[invalid]
    with pytest.raises(WorkflowExecutionError):
        service.validate_payload(name, payload)
    assert _snapshot(service, handle) == before
    with pytest.raises(WorkflowExecutionError):
        if invalid == "text":
            service.add("π" * 65, **_arguments(handle))
        elif invalid == "metadata":
            service.add("text", {"huge": "x" * 129}, **_arguments(handle))
        elif invalid == "query":
            service.search("x" * 129, **_arguments(handle))
        elif invalid == "terms":
            service.search(" ".join(str(index) for index in range(9)), **_arguments(handle))
        elif invalid == "limit":
            service.search("", limit=True, **_arguments(handle))
        else:
            service.delete(True, **_arguments(handle))
    assert _snapshot(service, handle) == before


def test_two_principals_with_identical_labels_cannot_read_or_delete_foreign_memory(memory_tree):
    left, alice, _ = memory_tree()
    right, bob, _ = memory_tree(owner="memory-other-" + uuid4().hex)
    identifier = left.add("private original", **_arguments(alice))
    empty_before = _snapshot(right, bob)
    assert right.search("", **_arguments(bob)) == []
    assert _snapshot(right, bob) == empty_before
    assert not right.delete(identifier, **_arguments(bob))
    before = _snapshot(left, alice)
    with pytest.raises(WorkflowExecutionError):
        right.search("", namespace=_namespace(alice.owner_principal), execution_handle=bob)
    assert _snapshot(left, alice) == before
    assert left.search("", **_arguments(alice))[0]["text"] == "private original"


def test_exact_quota_race_admits_only_one_without_eviction(memory_tree):
    bounds = replace(Bounds(), max_entries=1)
    left, first, _ = memory_tree(bounds=bounds)
    right, second, _ = memory_tree(owner=first.owner_principal, bounds=bounds)
    barrier = threading.Barrier(2, timeout=5)

    def submit(service, handle, text):
        barrier.wait()
        try:
            return service.add(text, **_arguments(handle))
        except WorkflowExecutionError as exc:
            assert exc.details["reason"] == "quota_exceeded"
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(submit, service, handle, text)
            for service, handle, text in ((left, first, "first"), (right, second, "second"))
        ]
        results = [future.result(timeout=10) for future in futures]
    assert sorted(result is None for result in results) == [False, True]
    rows = left.search("", **_arguments(first))
    assert len(rows) == 1 and rows[0]["text"] in {"first", "second"}
    before = _snapshot(left, first)
    with pytest.raises(WorkflowExecutionError, match="bounded memory admission"):
        left.add("replacement forbidden", **_arguments(first))
    assert _snapshot(left, first) == before


def test_delete_releases_exact_quota_without_reusing_original_id(memory_tree):
    service, handle, _ = memory_tree(bounds=replace(Bounds(), max_entries=1))
    first = service.add("one", **_arguments(handle))
    assert service.delete(first, **_arguments(handle))
    assert not service.delete(first, **_arguments(handle))
    second = service.add("two", **_arguments(handle))
    assert second > first
    row = service._connection.execute(
        "SELECT entries,bytes FROM bounded_memory_namespaces WHERE namespace=?",
        (_namespace(handle.owner_principal),),
    ).fetchone()
    assert tuple(row) == (1, len(b"two") + 2)


def test_namespace_byte_quota_is_atomic(memory_tree):
    service, handle, _ = memory_tree(bounds=replace(Bounds(), max_namespace_bytes=8))
    service.add("ππ", **_arguments(handle))
    before = _snapshot(service, handle)
    with pytest.raises(WorkflowExecutionError):
        service.add("x", **_arguments(handle))
    assert _snapshot(service, handle) == before


def test_identical_text_is_not_used_as_a_synthetic_replay_identity(memory_tree):
    service, handle, _ = memory_tree()
    first = service.add("same text", **_arguments(handle))
    second = service.add("same text", **_arguments(handle))
    assert second > first
    assert [row["id"] for row in service.search("same", **_arguments(handle))] == [first, second]
    before = _snapshot(service, handle)
    with pytest.raises(WorkflowExecutionError) as caught:
        service.prune_due(**_arguments(handle))
    assert caught.value.details["reason"] == "retention_disabled"
    assert _snapshot(service, handle) == before


def test_finish_guard_failure_rolls_back_entry_and_quota_together(memory_tree, monkeypatch):
    service, handle, _ = memory_tree()
    before = _snapshot(service, handle)

    def lost(_handle):
        raise InvocationStateConflict("controlled post-write ownership failure")

    monkeypatch.setattr(service, "_finish_guard", lost)
    with pytest.raises(InvocationStateConflict):
        service.add("must roll back", **_arguments(handle))
    assert _snapshot(service, handle) == before


def test_actual_expiry_takeover_rejects_old_writer_and_allows_current_generation(memory_tree):
    service, handle, ownership = memory_tree(seconds=0.04)
    original = handle._execution_lease
    time.sleep(0.08)
    current = service.invocations.get(handle.invocation_id)
    assert current is not None and original is not None
    current._execution_lease = ownership.acquire(current, seconds=60)
    assert current._execution_lease.generation == original.generation + 1
    before = _snapshot(service, handle)
    try:
        with pytest.raises(InvocationStateConflict):
            service.add("stale", **_arguments(handle))
        assert _snapshot(service, handle) == before
        assert service.add("current", **_arguments(current)) == 1
    finally:
        ownership.release(current._execution_lease)


def test_operator_cancelled_execution_cannot_mutate_memory(memory_tree):
    service, handle, _ = memory_tree()
    service.invocations.cancel_authoritatively(
        handle, error={"code": "invocation_cancelled", "message": "operator", "details": {}}
    )
    before = _snapshot(service, handle)
    with pytest.raises(InvocationStateConflict):
        service.add("cancelled", **_arguments(handle))
    assert _snapshot(service, handle) == before


@pytest.mark.parametrize("corruption", ["quota", "text", "digest", "over-scan"])
def test_corrupt_or_over_limit_stored_data_fails_before_projection_or_mutation(
    memory_tree, corruption
):
    service, handle, _ = memory_tree()
    service.add("original", **_arguments(handle))
    connection = service._connection
    namespace = _namespace(handle.owner_principal)
    if corruption == "quota":
        connection.execute(
            "UPDATE bounded_memory_namespaces SET bytes=0 WHERE namespace=?", (namespace,)
        )
    elif corruption == "text":
        connection.execute(
            "UPDATE bounded_memory_entries SET text=? WHERE namespace=?", ("x" * 129, namespace)
        )
    elif corruption == "digest":
        connection.execute(
            "UPDATE bounded_memory_entries SET record_digest=? WHERE namespace=?",
            ("0" * 64, namespace),
        )
    else:
        for identifier in range(2, 11):
            connection.execute(
                """INSERT INTO bounded_memory_entries
                SELECT namespace,?,text,metadata,created,expires_at,record_digest
                FROM bounded_memory_entries WHERE namespace=? AND memory_id=1""",
                (identifier, namespace),
            )
    connection.commit()
    before = _snapshot(service, handle)
    with pytest.raises(WorkflowExecutionError):
        service.search("", **_arguments(handle))
    with pytest.raises(WorkflowExecutionError):
        service.add("new", **_arguments(handle))
    assert _snapshot(service, handle) == before


def test_original_expiry_is_retained_and_only_explicit_retention_prunes_due_data(memory_tree):
    bounds = replace(Bounds(), max_entries=1, retention_seconds=0.04)
    service, handle, _ = memory_tree(bounds=bounds)
    first = service.add("original", **_arguments(handle))
    saved = service._connection.execute(
        "SELECT created,expires_at FROM bounded_memory_entries WHERE namespace=? AND memory_id=?",
        (_namespace(handle.owner_principal), first),
    ).fetchone()
    assert saved[1] - saved[0] == pytest.approx(0.04)
    reopened, later, _ = memory_tree(owner=handle.owner_principal, bounds=bounds)
    time.sleep(0.08)
    assert reopened.search("", **_arguments(later)) == []
    retained = reopened._connection.execute(
        "SELECT created,expires_at FROM bounded_memory_entries WHERE namespace=? AND memory_id=?",
        (_namespace(handle.owner_principal), first),
    ).fetchone()
    assert tuple(retained) == tuple(saved)
    assert reopened.add("new", **_arguments(later)) > first


def test_separate_maintenance_advances_multiple_expired_pages_without_reader_deletion(memory_tree):
    service, handle, _ = memory_tree(
        bounds=replace(Bounds(), max_entries=3, retention_seconds=0.04, sweep_limit=1)
    )
    original_ids = [service.add(str(index), **_arguments(handle)) for index in range(3)]
    original_times = service._connection.execute(
        "SELECT memory_id,created,expires_at FROM bounded_memory_entries "
        "WHERE namespace=? ORDER BY memory_id",
        (_namespace(handle.owner_principal),),
    ).fetchall()
    time.sleep(0.08)
    before = _snapshot(service, handle)
    assert service.search("", **_arguments(handle)) == []
    assert _snapshot(service, handle) == before
    for expected_remaining in (2, 1, 0):
        assert service.prune_due(**_arguments(handle)) == 1
        retained = service._connection.execute(
            "SELECT memory_id,created,expires_at FROM bounded_memory_entries "
            "WHERE namespace=? ORDER BY memory_id",
            (_namespace(handle.owner_principal),),
        ).fetchall()
        assert len(retained) == expected_remaining
        assert retained == original_times[3 - expected_remaining :]
    assert service.prune_due(**_arguments(handle)) == 0
    assert service.add("after cleanup", **_arguments(handle)) > max(original_ids)


def test_maintenance_rolls_back_then_rejects_actual_stale_owner_takeover(memory_tree, monkeypatch):
    service, handle, ownership = memory_tree(
        bounds=replace(Bounds(), retention_seconds=0.04, sweep_limit=1)
    )
    service.add("original", **_arguments(handle))
    service.add("other expired", **_arguments(handle))
    time.sleep(0.08)
    before = _snapshot(service, handle)
    original_guard = service._finish_guard

    def lost(_handle):
        raise InvocationStateConflict("controlled failed maintenance commit")

    monkeypatch.setattr(service, "_finish_guard", lost)
    with pytest.raises(InvocationStateConflict):
        service.prune_due(**_arguments(handle))
    assert _snapshot(service, handle) == before
    monkeypatch.setattr(service, "_finish_guard", original_guard)
    handle._execution_lease = ownership.renew(handle._execution_lease, seconds=0.04)
    generation = handle._execution_lease.generation
    time.sleep(0.08)
    current = service.invocations.get(handle.invocation_id)
    assert current is not None
    current._execution_lease = ownership.acquire(current, seconds=60)
    assert current._execution_lease.generation == generation + 1
    before = _snapshot(service, handle)
    try:
        with pytest.raises(InvocationStateConflict):
            service.prune_due(**_arguments(handle))
        assert _snapshot(service, handle) == before
        assert service.prune_due(**_arguments(current)) == 1
        assert len(_snapshot(service, current)[0]) == 1
    finally:
        ownership.release(current._execution_lease)


def test_changed_deployment_profile_cannot_adopt_existing_namespace(memory_tree):
    service, handle, _ = memory_tree()
    service.add("original", **_arguments(handle))
    changed, other, _ = memory_tree(
        owner=handle.owner_principal, bounds=replace(Bounds(), version="2")
    )
    before = _snapshot(service, handle)
    with pytest.raises(WorkflowExecutionError):
        changed.search("", **_arguments(other))
    assert _snapshot(service, handle) == before


def test_legacy_default_storage_and_reopen_remain_separate(tmp_path):
    database = str(tmp_path / "legacy.sqlite3")
    legacy = MemoryService(database)
    identifier = legacy.add("legacy π", {"old": None})
    legacy.close()
    reopened = MemoryService(database)
    try:
        assert reopened.search("π")[0]["id"] == identifier
        assert reopened.search("π")[0]["metadata"] == {"old": None}
    finally:
        reopened.close()
