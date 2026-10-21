"""Deployment-selected finite memory, separate from session/checkpoint state.

This is an independent common implementation. The frozen Apache memory
adapter was reviewed for scope separation, not copied or used as an oracle.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from copy import copy
from dataclasses import dataclass
from typing import Any, Protocol

from .errors import ConfigurationError, InvocationStateConflict, WorkflowExecutionError
from .memory import MemoryService
from .ownership import execution_write_guard, owned_write_transaction
from .persistence import ExecutionHandle, InvocationStore, _decode_payload, _verify_identity
from .storage import StorageConnection


class MemoryBounds(Protocol):
    enabled: bool
    id: str
    version: str
    max_text_bytes: int
    max_metadata_bytes: int
    max_query_bytes: int
    max_query_terms: int
    max_results: int
    max_entries: int
    max_namespace_bytes: int
    max_scan_rows: int
    retention_seconds: float | None
    sweep_limit: int


def _failure(reason: str) -> WorkflowExecutionError:
    return WorkflowExecutionError(
        "bounded memory admission failed", details={"stage": "memory", "reason": reason}
    )


def _text(value: Any, limit: int, reason: str) -> str:
    if type(value) is not str or len(value) > limit:
        raise _failure(reason)
    try:
        if len(value.encode("utf-8", "strict")) > limit:
            raise _failure(reason)
    except UnicodeError:
        raise _failure(reason) from None
    return value


def _metadata(value: Any, limit: int) -> str:
    if type(value) is not dict:
        raise _failure("metadata_invalid")
    pending: list[tuple[Any, int]] = [(value, 0)]
    nodes = 0
    text_bytes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > 4096 or depth > 16:
            raise _failure("metadata_invalid")
        if type(item) is dict:
            if len(item) > 4096:
                raise _failure("metadata_invalid")
            for key, nested in item.items():
                _text(key, limit, "metadata_invalid")
                text_bytes += len(key.encode())
                pending.append((nested, depth + 1))
        elif type(item) is list:
            if len(item) > 4096:
                raise _failure("metadata_invalid")
            pending.extend((nested, depth + 1) for nested in item)
        elif type(item) is str:
            _text(item, limit, "metadata_invalid")
            text_bytes += len(item.encode())
        elif item is None or type(item) in {bool, int}:
            if type(item) is int and item.bit_length() > 256:
                raise _failure("metadata_invalid")
        elif type(item) is not float or not math.isfinite(item):
            raise _failure("metadata_invalid")
        if text_bytes > limit:
            raise _failure("metadata_invalid")
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return _text(encoded, limit, "metadata_invalid")


@dataclass(frozen=True, slots=True)
class _Entry:
    identifier: int
    text: str
    metadata: str
    created: float
    expires_at: float | None

    def digest(self, namespace: str, profile: str) -> str:
        value = [
            namespace,
            profile,
            self.identifier,
            self.text,
            self.metadata,
            self.created,
            self.expires_at,
        ]
        return hashlib.sha256(
            json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
        ).hexdigest()

    @property
    def size(self) -> int:
        return len(self.text.encode()) + len(self.metadata.encode())

    def as_value(self) -> dict[str, Any]:
        return {
            "id": self.identifier,
            "text": self.text,
            "metadata": json.loads(self.metadata),
            "created": self.created,
        }


class BoundedMemoryService(MemoryService):
    """Finite principal memory using the invocation's own writer transaction.

    Mutations have no independent replay key. An unresolved enclosing agent
    effect remains uncertain, rather than repeating or deduplicating by text.
    """

    _connection: StorageConnection

    def __init__(self, invocations: InvocationStore, bounds: MemoryBounds) -> None:
        self.invocations = invocations
        # All profile fields are scalars: capture their deployment values so
        # mutating a caller's config object cannot alter an admitted quota.
        self.bounds = copy(bounds)
        self.database = None
        self._connection = invocations.connection
        numeric = [
            bounds.max_text_bytes,
            bounds.max_metadata_bytes,
            bounds.max_query_bytes,
            bounds.max_query_terms,
            bounds.max_results,
            bounds.max_entries,
            bounds.max_namespace_bytes,
            bounds.max_scan_rows,
            bounds.sweep_limit,
        ]
        if (
            not bounds.enabled
            or any(type(value) is not int or value < 1 for value in numeric)
            or bounds.max_scan_rows < bounds.max_entries
            or bounds.max_scan_rows > 10000
            or bounds.max_results > 1000
            or bounds.max_text_bytes > 1048576
            or bounds.max_metadata_bytes > 262144
            or bounds.max_query_bytes > 65536
            or bounds.max_query_terms > 256
            or bounds.sweep_limit > 10000
            or bounds.max_namespace_bytes > 67108864
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,127}", bounds.id)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,127}", bounds.version)
            or (
                bounds.retention_seconds is not None
                and (
                    type(bounds.retention_seconds) not in {int, float}
                    or not math.isfinite(bounds.retention_seconds)
                    or not 0 < bounds.retention_seconds <= 31536000
                )
            )
        ):
            raise ConfigurationError("bounded memory profile is invalid")
        self.profile_digest = hashlib.sha256(
            json.dumps(
                {
                    name: getattr(bounds, name)
                    for name in (
                        "id",
                        "version",
                        "max_text_bytes",
                        "max_metadata_bytes",
                        "max_query_bytes",
                        "max_query_terms",
                        "max_results",
                        "max_entries",
                        "max_namespace_bytes",
                        "max_scan_rows",
                        "retention_seconds",
                        "sweep_limit",
                    )
                },
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        ).hexdigest()
        self._connection.execute("""CREATE TABLE IF NOT EXISTS bounded_memory_namespaces (
            namespace TEXT PRIMARY KEY, profile_digest TEXT NOT NULL,
            entries BIGINT NOT NULL, bytes BIGINT NOT NULL, next_id BIGINT NOT NULL)""")
        self._connection.execute("""CREATE TABLE IF NOT EXISTS bounded_memory_entries (
            namespace TEXT NOT NULL, memory_id BIGINT NOT NULL, text TEXT NOT NULL,
            metadata TEXT NOT NULL, created DOUBLE PRECISION NOT NULL,
            expires_at DOUBLE PRECISION, record_digest TEXT NOT NULL,
            PRIMARY KEY(namespace, memory_id))""")
        self._connection.commit()

    def _scope(self, namespace: str | None, handle: ExecutionHandle | None) -> str:
        if handle is None or handle._execution_lease is None or not handle.owner_principal:
            raise InvocationStateConflict("bounded memory requires original execution ownership")
        expected = (
            "owa/principal-memory/v1:"
            + hashlib.sha256(handle.owner_principal.encode("utf-8")).hexdigest()
        )
        if namespace != expected:
            raise _failure("namespace_invalid")
        return expected

    def validate_payload(self, name: str, payload: Any) -> None:
        """Pure role-batch preflight; no owner, SQL, namespace or secret work."""
        if type(payload) is not dict:
            raise _failure("payload_invalid")
        if name == "add_memory":
            if set(payload) - {"text", "metadata"}:
                raise _failure("payload_invalid")
            _text(payload.get("text"), self.bounds.max_text_bytes, "text_invalid")
            _metadata(payload.get("metadata", {}), self.bounds.max_metadata_bytes)
        elif name == "search_memory":
            if set(payload) - {"query", "limit"}:
                raise _failure("payload_invalid")
            query = _text(payload.get("query", ""), self.bounds.max_query_bytes, "query_invalid")
            limit = payload.get("limit", 10)
            if (
                len(set(query.lower().split())) > self.bounds.max_query_terms
                or type(limit) is not int
                or not 1 <= limit <= self.bounds.max_results
            ):
                raise _failure("query_invalid")
        elif name == "delete_memory":
            if (
                set(payload) != {"id"}
                or type(payload["id"]) is not int
                or not 0 < payload["id"] < 2**63
            ):
                raise _failure("identifier_invalid")
        else:
            raise _failure("operation_invalid")

    def _running(self, handle: ExecutionHandle) -> float:
        connection = self._connection
        suffix = " FOR SHARE" if connection.is_postgresql else ""
        row = connection.execute(
            "SELECT payload,status FROM invocations WHERE invocation_id=?" + suffix,
            (handle.invocation_id,),
        ).fetchone()
        if row is None or row[1] != "running" or row[0] != handle._persisted_payload:
            raise InvocationStateConflict("bounded memory execution is no longer current")
        _verify_identity(handle, _decode_payload(row[0]))
        clock = (
            "EXTRACT(EPOCH FROM clock_timestamp())"
            if connection.is_postgresql
            else "((julianday('now') - 2440587.5) * 86400.0)"
        )
        return float(connection.execute("SELECT " + clock).fetchone()[0])

    def _finish_guard(self, handle: ExecutionHandle) -> None:
        assert handle._execution_lease is not None
        guard, values = execution_write_guard(self._connection, handle._execution_lease)
        if self._connection.execute(guard, values).fetchone() is None:
            raise InvocationStateConflict("bounded memory execution ownership expired")

    def _quota(self, namespace: str, *, create: bool = True) -> tuple[int, int, int]:
        connection = self._connection
        if create:
            connection.execute(
                """INSERT INTO bounded_memory_namespaces
                (namespace,profile_digest,entries,bytes,next_id) VALUES (?,?,0,0,1)
                ON CONFLICT(namespace) DO NOTHING""",
                (namespace, self.profile_digest),
            )
        suffix = " FOR UPDATE" if connection.is_postgresql else ""
        row = connection.execute(
            """SELECT CASE WHEN length(profile_digest)=64 THEN profile_digest ELSE NULL END,
            entries,bytes,next_id
            FROM bounded_memory_namespaces WHERE namespace=?"""
            + suffix,
            (namespace,),
        ).fetchone()
        if row is None and not create:
            # A reader does not manufacture a quota row. _rows still rejects
            # orphan entries if this supposedly empty namespace has data.
            return 0, 0, 1
        if (
            row is None
            or row[0] != self.profile_digest
            or any(type(value) is not int for value in row[1:])
            or not 0 <= row[1] <= self.bounds.max_entries
            or not 0 <= row[2] <= self.bounds.max_namespace_bytes
            or not 1 <= row[3] < 2**63 - 1
        ):
            raise _failure("quota_invalid")
        return row[1], row[2], row[3]

    def _rows(self, namespace: str, quota: tuple[int, int, int]) -> list[_Entry]:
        connection = self._connection
        length = "octet_length" if connection.is_postgresql else "length"
        text = "text" if connection.is_postgresql else "CAST(text AS BLOB)"
        metadata = "metadata" if connection.is_postgresql else "CAST(metadata AS BLOB)"
        inventory = connection.execute(
            f"""SELECT COUNT(*),
            COALESCE(SUM(text_bytes+metadata_bytes),0),
            COALESCE(MAX(CASE WHEN text_bytes>? OR metadata_bytes>? THEN 1 ELSE 0 END),0)
            FROM (SELECT {length}({text}) AS text_bytes,
                {length}({metadata}) AS metadata_bytes FROM bounded_memory_entries
                WHERE namespace=? ORDER BY memory_id LIMIT ?) AS finite_memory_inventory""",
            (
                self.bounds.max_text_bytes,
                self.bounds.max_metadata_bytes,
                namespace,
                self.bounds.max_scan_rows + 1,
            ),
        ).fetchone()
        if (
            inventory is None
            or inventory[0] != quota[0]
            or inventory[1] != quota[1]
            or inventory[2] != 0
            or inventory[0] > self.bounds.max_scan_rows
            or inventory[1] > self.bounds.max_namespace_bytes
        ):
            raise _failure("quota_invalid")
        # Oversized/corrupt values never cross the DB driver materialization
        # boundary, and the extra row detects incomplete ranking scans.
        rows = connection.execute(
            f"""SELECT memory_id,
            CASE WHEN {length}({text})<=? THEN text ELSE NULL END,
            CASE WHEN {length}({metadata})<=? THEN metadata ELSE NULL END,
            created,expires_at,
            CASE WHEN length(record_digest)=64 THEN record_digest ELSE NULL END
            FROM bounded_memory_entries
            WHERE namespace=? ORDER BY memory_id LIMIT ?""",
            (
                self.bounds.max_text_bytes,
                self.bounds.max_metadata_bytes,
                namespace,
                self.bounds.max_scan_rows + 1,
            ),
        ).fetchall()
        if len(rows) > self.bounds.max_scan_rows or len(rows) != quota[0]:
            raise _failure("quota_invalid")
        result: list[_Entry] = []
        size = 0
        for row in rows:
            if (
                type(row[0]) is not int
                or not 0 < row[0] < quota[2]
                or type(row[1]) is not str
                or type(row[2]) is not str
                or type(row[3]) not in {int, float}
                or not math.isfinite(row[3])
                or row[3] <= 0
                or (
                    row[4] is not None
                    and (
                        type(row[4]) not in {int, float}
                        or not math.isfinite(row[4])
                        or row[4] <= row[3]
                    )
                )
            ):
                raise _failure("entry_invalid")
            entry = _Entry(
                row[0], row[1], row[2], float(row[3]), float(row[4]) if row[4] is not None else None
            )
            expected_expiry = (
                entry.created + self.bounds.retention_seconds
                if self.bounds.retention_seconds is not None
                else None
            )
            if entry.expires_at != expected_expiry:
                raise _failure("entry_invalid")
            try:
                if (
                    _metadata(json.loads(entry.metadata), self.bounds.max_metadata_bytes)
                    != entry.metadata
                ):
                    raise _failure("entry_invalid")
                if entry.digest(namespace, self.profile_digest) != row[5]:
                    raise _failure("entry_invalid")
            except (ValueError, UnicodeError, RecursionError):
                raise _failure("entry_invalid") from None
            size += entry.size
            if size > self.bounds.max_namespace_bytes:
                raise _failure("quota_invalid")
            result.append(entry)
        if size != quota[1]:
            raise _failure("quota_invalid")
        return result

    def _prune(self, namespace: str, rows: list[_Entry], now: float) -> int:
        if self.bounds.retention_seconds is None:
            return 0
        due = [row for row in rows if row.expires_at is not None and row.expires_at <= now]
        selected = due[: self.bounds.sweep_limit]
        for row in selected:
            self._connection.execute(
                "DELETE FROM bounded_memory_entries WHERE namespace=? AND memory_id=?",
                (namespace, row.identifier),
            )
            self._connection.execute(
                "UPDATE bounded_memory_namespaces SET entries=entries-1,bytes=bytes-? "
                "WHERE namespace=?",
                (row.size, namespace),
            )
        return len(selected)

    def prune_due(
        self, *, namespace: str | None = None, execution_handle: ExecutionHandle | None = None
    ) -> int:
        """One owner-fenced maintenance page; service requires memory.prune.

        No new public agent tool is introduced. Retention is a deployment
        opt-in, and search does not gain authority to delete operator data.
        """
        if self.bounds.retention_seconds is None:
            raise _failure("retention_disabled")
        bound = self._scope(namespace, execution_handle)
        assert execution_handle is not None
        with owned_write_transaction(
            self._connection,
            execution_handle._execution_lease,
            invocation_id=execution_handle.invocation_id,
            workflow_fingerprint=execution_handle.workflow_fingerprint,
        ):
            now = self._running(execution_handle)
            rows = self._rows(bound, self._quota(bound, create=False))
            count = self._prune(bound, rows, now)
            self._finish_guard(execution_handle)
        return count

    def add(
        self,
        text: str,
        metadata: dict[str, Any] | None = None,
        *,
        namespace: str | None = None,
        execution_handle: ExecutionHandle | None = None,
    ) -> int:
        text = _text(text, self.bounds.max_text_bytes, "text_invalid")
        encoded = _metadata({} if metadata is None else metadata, self.bounds.max_metadata_bytes)
        bound = self._scope(namespace, execution_handle)
        assert execution_handle is not None
        with owned_write_transaction(
            self._connection,
            execution_handle._execution_lease,
            invocation_id=execution_handle.invocation_id,
            workflow_fingerprint=execution_handle.workflow_fingerprint,
        ):
            now = self._running(execution_handle)
            quota = self._quota(bound)
            rows = self._rows(bound, quota)
            self._prune(bound, rows, now)
            quota = self._quota(bound)
            entry = _Entry(
                quota[2],
                text,
                encoded,
                now,
                now + self.bounds.retention_seconds if self.bounds.retention_seconds else None,
            )
            if (
                quota[0] >= self.bounds.max_entries
                or quota[1] + entry.size > self.bounds.max_namespace_bytes
            ):
                raise _failure("quota_exceeded")
            self._connection.execute(
                """INSERT INTO bounded_memory_entries
                (namespace,memory_id,text,metadata,created,expires_at,record_digest)
                VALUES (?,?,?,?,?,?,?)""",
                (
                    bound,
                    entry.identifier,
                    text,
                    encoded,
                    now,
                    entry.expires_at,
                    entry.digest(bound, self.profile_digest),
                ),
            )
            self._connection.execute(
                """UPDATE bounded_memory_namespaces
                SET entries=entries+1,bytes=bytes+?,next_id=next_id+1 WHERE namespace=?""",
                (entry.size, bound),
            )
            self._finish_guard(execution_handle)
        return entry.identifier

    def search(
        self,
        query: str,
        limit: int = 10,
        *,
        namespace: str | None = None,
        execution_handle: ExecutionHandle | None = None,
    ) -> list[dict[str, Any]]:
        query = _text(query, self.bounds.max_query_bytes, "query_invalid")
        terms = set(query.lower().split())
        if (
            len(terms) > self.bounds.max_query_terms
            or type(limit) is not int
            or not 1 <= limit <= self.bounds.max_results
        ):
            raise _failure("query_invalid")
        bound = self._scope(namespace, execution_handle)
        assert execution_handle is not None
        with owned_write_transaction(
            self._connection,
            execution_handle._execution_lease,
            invocation_id=execution_handle.invocation_id,
            workflow_fingerprint=execution_handle.workflow_fingerprint,
        ):
            now = self._running(execution_handle)
            rows = self._rows(bound, self._quota(bound, create=False))
            self._finish_guard(execution_handle)
        live = [row for row in rows if row.expires_at is None or row.expires_at > now]
        ranked = sorted(
            live,
            key=lambda row: (
                -sum(term in row.text.lower() for term in terms),
                row.created,
                row.identifier,
            ),
        )
        return [
            row.as_value()
            for row in ranked
            if not terms or any(term in row.text.lower() for term in terms)
        ][:limit]

    def delete(
        self,
        memory_id: int,
        *,
        namespace: str | None = None,
        execution_handle: ExecutionHandle | None = None,
    ) -> bool:
        if type(memory_id) is not int or not 0 < memory_id < 2**63:
            raise _failure("identifier_invalid")
        bound = self._scope(namespace, execution_handle)
        assert execution_handle is not None
        with owned_write_transaction(
            self._connection,
            execution_handle._execution_lease,
            invocation_id=execution_handle.invocation_id,
            workflow_fingerprint=execution_handle.workflow_fingerprint,
        ):
            self._running(execution_handle)
            rows = self._rows(bound, self._quota(bound))
            matched = next((row for row in rows if row.identifier == memory_id), None)
            if matched is not None:
                self._connection.execute(
                    "DELETE FROM bounded_memory_entries WHERE namespace=? AND memory_id=?",
                    (bound, memory_id),
                )
                self._connection.execute(
                    "UPDATE bounded_memory_namespaces SET entries=entries-1,bytes=bytes-? "
                    "WHERE namespace=?",
                    (matched.size, bound),
                )
            self._finish_guard(execution_handle)
        return matched is not None

    def close(self) -> None:
        # RuntimeServices owns the common invocation connection.
        pass
