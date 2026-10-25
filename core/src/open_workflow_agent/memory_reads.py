"""Owned common observations for repeatable private provider memory reads."""

from __future__ import annotations

import hashlib
import inspect
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, replace
from typing import Any, Literal, cast

from ._effect_storage import insert_completed_receipt
from .effects import EffectIdentity, EffectJournal, EffectReceipt, effect_request_digest
from .errors import InvocationStateConflict, InvocationStateError, ModelError
from .model_responses import bounded_model_json
from .ownership import owned_write_transaction
from .persistence import ExecutionHandle, InvocationStore, _verify_identity
from .storage import StorageConnection

PROFILE = "owa/memory-read/v1"
OPERATION_PREFIX = "policy:memory-read:v1:"
OWNER = "memory-read:v1"
MAX_READS = 64
MAX_REQUEST_BYTES = 65_536
_REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,127}\Z")
_COLLECTION = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")


def _invalid(reason: str) -> InvocationStateError:
    return InvocationStateError(
        "memory read observation is unavailable or inconsistent",
        details={"stage": "memory", "reason": reason},
    )


@dataclass(frozen=True, slots=True)
class MemoryReadBinding:
    id: str
    version: str
    backend: Literal["memory", "sqlite", "postgresql"]

    def __post_init__(self) -> None:
        if (
            any(type(v) is not str or not _REFERENCE.fullmatch(v) for v in (self.id, self.version))
            or type(self.backend) is not str
            or self.backend not in {"memory", "sqlite", "postgresql"}
        ):
            raise ValueError("memory reads require a bounded installed provider binding")


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _invalid("duplicate_field")
        result[key] = value
    return result


def _value_json(value: Any, maximum: int) -> str:
    if value is not None and type(value) is not dict:
        raise _invalid("value_invalid")
    try:
        return bounded_model_json(value, limit=maximum)
    except ModelError:
        raise _invalid("value_invalid") from None


def _request(
    parent: EffectIdentity,
    binding: MemoryReadBinding,
    namespace: str,
    collection: str,
    key: str,
    maximum: int,
) -> tuple[EffectIdentity, str, str]:
    if (
        not isinstance(parent, EffectIdentity)
        or not isinstance(binding, MemoryReadBinding)
        or type(namespace) is not str
        or not re.fullmatch(r"owa/principal-memory/v1:[a-f0-9]{64}", namespace)
        or type(collection) is not str
        or not _COLLECTION.fullmatch(collection)
        or type(key) is not str
        or not _KEY.fullmatch(key)
        or type(maximum) is not int
        or not 1 <= maximum <= 65_536
    ):
        raise _invalid("request_invalid")
    suffix = effect_request_digest(
        PROFILE + ":slot",
        {
            "parent_operation": parent.operation,
            "collection": collection,
            "key": key,
        },
    )
    identity = replace(parent, operation=OPERATION_PREFIX + suffix)
    value = {
        "profile": PROFILE,
        "parent": {**asdict(parent), "occurrence": [list(entry) for entry in parent.occurrence]},
        "binding": asdict(binding),
        "namespace": namespace,
        "collection": collection,
        "key": key,
        "max_record_bytes": maximum,
    }
    try:
        encoded = bounded_model_json(value, limit=MAX_REQUEST_BYTES)
    except ModelError:
        raise _invalid("request_invalid") from None
    return identity, encoded, effect_request_digest(PROFILE + ":request", value)


def _table_exists(connection: StorageConnection) -> bool:
    if connection.is_postgresql:
        return (
            connection.execute("SELECT to_regclass('memory_read_observations')").fetchone()[0]
            is not None
        )
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_read_observations'"
        ).fetchone()
        is not None
    )


def _bounded_column(connection: StorageConnection, column: str, maximum: int) -> str:
    size = (
        f"octet_length({column})" if connection.is_postgresql else f"length(CAST({column} AS BLOB))"
    )
    return f"CASE WHEN {size}<={maximum} THEN {column} ELSE NULL END"


def _ensure_schema(connection: StorageConnection) -> None:
    if connection.is_postgresql:
        # Serialize first-use DDL across owners of different invocations.
        connection.execute("SELECT pg_advisory_xact_lock(?,?)", (0x4F5741, 0x4D523031))
    if _table_exists(connection):
        return
    if (
        connection.execute(
            "SELECT 1 FROM workflow_effects WHERE owner=? LIMIT 1", (OWNER,)
        ).fetchone()
        is not None
    ):
        raise _invalid("observation_missing")
    connection.execute(
        "CREATE TABLE memory_read_observations (identity TEXT PRIMARY KEY, "
        "invocation_id TEXT NOT NULL,fingerprint TEXT NOT NULL, "
        "request_digest TEXT NOT NULL,request_json TEXT NOT NULL, "
        "value_json TEXT NOT NULL,value_digest TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE INDEX memory_read_invocation_index ON memory_read_observations(invocation_id)"
    )


def _load(
    connection: StorageConnection,
    invocations: InvocationStore,
    identity: EffectIdentity,
    receipt: EffectReceipt,
) -> tuple[str, str]:
    if not _table_exists(connection):
        raise _invalid("observation_missing")
    row = connection.execute(
        "SELECT invocation_id,fingerprint,request_digest,"
        + _bounded_column(connection, "request_json", MAX_REQUEST_BYTES)
        + ","
        + _bounded_column(connection, "value_json", 65_536)
        + ",value_digest FROM memory_read_observations WHERE identity=?",
        (identity.key,),
    ).fetchone()
    if row is None or row[0] != identity.invocation_id or row[1] != identity.workflow_fingerprint:
        raise _invalid("observation_missing")
    if type(row[3]) is not str:
        raise _invalid("request_invalid")
    request = json.loads(row[3], object_pairs_hook=_unique)
    if (
        type(request) is not dict
        or set(request)
        != {
            "profile",
            "parent",
            "binding",
            "namespace",
            "collection",
            "key",
            "max_record_bytes",
        }
        or request["profile"] != PROFILE
    ):
        raise _invalid("request_invalid")
    parent = EffectIdentity.from_value(request["parent"])
    provider = request["binding"]
    if type(provider) is not dict or set(provider) != {"id", "version", "backend"}:
        raise _invalid("binding_invalid")
    expected, encoded, digest = _request(
        parent,
        MemoryReadBinding(**provider),
        request["namespace"],
        request["collection"],
        request["key"],
        request["max_record_bytes"],
    )
    handle = invocations.get(identity.invocation_id)
    if (
        expected != identity
        or encoded != row[3]
        or digest != row[2]
        or handle is None
        or handle.workflow_fingerprint != identity.workflow_fingerprint
        or handle.owner_principal is None
        or request["namespace"]
        != "owa/principal-memory/v1:" + hashlib.sha256(handle.owner_principal.encode()).hexdigest()
    ):
        raise _invalid("binding_invalid")
    index = connection.execute(
        "SELECT invocation_id,fingerprint,"
        + _bounded_column(connection, "payload", MAX_REQUEST_BYTES)
        + " FROM workflow_effect_invocations WHERE identity=?",
        (identity.key,),
    ).fetchone()
    if (
        index is None
        or index[0] != identity.invocation_id
        or index[1] != identity.workflow_fingerprint
        or type(index[2]) is not str
        or EffectIdentity.from_value(json.loads(index[2], object_pairs_hook=_unique)) != identity
    ):
        raise _invalid("identity_missing")
    maximum = request["max_record_bytes"]
    if type(row[4]) is not str or len(row[4].encode()) > maximum:
        raise _invalid("value_invalid")
    value = json.loads(row[4], object_pairs_hook=_unique)
    value_json = _value_json(value, maximum)
    value_digest = effect_request_digest(PROFILE + ":value", value)
    if (
        row[4] != value_json
        or row[5] != value_digest
        or receipt
        != EffectReceipt(
            digest,
            OWNER,
            status="completed",
            output={"profile": PROFILE, "value_digest": value_digest},
        )
    ):
        raise _invalid("observation_changed")
    return encoded, value_json


def verify_memory_read_receipt(
    connection: StorageConnection,
    invocations: InvocationStore | None,
    identity: EffectIdentity,
    receipt: EffectReceipt,
) -> None:
    if invocations is None:
        raise _invalid("invocation_missing")
    _load(connection, invocations, identity, receipt)


def verify_memory_read_index(
    connection: StorageConnection,
    invocation_id: str,
    fingerprint: str,
    identities: set[str],
) -> None:
    """Reconcile both directions, including observations with a deleted effect index."""
    if not _table_exists(connection):
        if identities:
            raise _invalid("observation_missing")
        return
    rows = connection.execute(
        "SELECT identity,fingerprint FROM memory_read_observations WHERE invocation_id=? LIMIT ?",
        (invocation_id, MAX_READS + 1),
    ).fetchall()
    if (
        len(rows) > MAX_READS
        or any(row[1] != fingerprint for row in rows)
        or {row[0] for row in rows} != identities
    ):
        raise _invalid("identity_missing")


class MemoryReadObservations:
    """First committed values; no provider is awaited under a common SQL lock."""

    def __init__(self, journal: EffectJournal, invocations: InvocationStore) -> None:
        if journal._invocations is not invocations:
            raise ValueError("memory observations require their original common invocation store")
        self.journal, self.invocations = journal, invocations
        self.connection = journal.connection

    def _owned(self, handle: ExecutionHandle, identity: EffectIdentity) -> None:
        if (
            handle._execution_lease is None
            or handle.invocation_id != identity.invocation_id
            or handle.workflow_fingerprint != identity.workflow_fingerprint
        ):
            raise InvocationStateConflict("memory reads require original execution ownership")
        saved = self.invocations.get(handle.invocation_id)
        if saved is None or saved._persisted_payload is None:
            raise _invalid("invocation_missing")
        _verify_identity(handle, json.loads(saved._persisted_payload))

    def _existing(self, identity: EffectIdentity, request: str) -> tuple[EffectReceipt, str] | None:
        receipt = self.journal.get(identity)
        if receipt is not None:
            observed_request, value = _load(self.connection, self.invocations, identity, receipt)
            if observed_request != request:
                raise _invalid("request_changed")
            return receipt, value
        if self.connection.execute(
            "SELECT 1 FROM workflow_effect_invocations WHERE identity=?",
            (identity.key,),
        ).fetchone() is not None or (
            _table_exists(self.connection)
            and self.connection.execute(
                "SELECT 1 FROM memory_read_observations WHERE identity=?",
                (identity.key,),
            ).fetchone()
            is not None
        ):
            raise _invalid("observation_missing")
        return None

    def _count(self, invocation_id: str) -> int:
        if not _table_exists(self.connection):
            return 0
        return int(
            self.connection.execute(
                "SELECT COUNT(*) FROM memory_read_observations WHERE invocation_id=?",
                (invocation_id,),
            ).fetchone()[0]
        )

    async def read(
        self,
        handle: ExecutionHandle,
        parent: EffectIdentity,
        *,
        binding: MemoryReadBinding,
        namespace: str,
        collection: str,
        key: str,
        max_record_bytes: int,
        fetch: Callable[[], Awaitable[dict[str, Any] | None]],
        observer: Callable[..., Any] | None = None,
    ) -> dict[str, Any] | None:
        identity, request, digest = _request(
            parent, binding, namespace, collection, key, max_record_bytes
        )
        if handle.owner_principal is None or namespace != (
            "owa/principal-memory/v1:" + hashlib.sha256(handle.owner_principal.encode()).hexdigest()
        ):
            raise _invalid("binding_invalid")
        self._owned(handle, identity)
        with owned_write_transaction(
            self.connection,
            handle._execution_lease,
            invocation_id=handle.invocation_id,
            workflow_fingerprint=handle.workflow_fingerprint,
        ):
            existing = self._existing(identity, request)
            if existing is None and self._count(handle.invocation_id) >= MAX_READS:
                raise _invalid("read_limit")
        if existing is None:
            value_json = _value_json(await fetch(), max_record_bytes)
            self._owned(handle, identity)
            with owned_write_transaction(
                self.connection,
                handle._execution_lease,
                invocation_id=handle.invocation_id,
                workflow_fingerprint=handle.workflow_fingerprint,
            ):
                existing = self._existing(identity, request)
                if existing is None:
                    if self._count(handle.invocation_id) >= MAX_READS:
                        raise _invalid("read_limit")
                    if not _table_exists(self.connection):
                        _ensure_schema(self.connection)
                    value_digest = effect_request_digest(PROFILE + ":value", json.loads(value_json))
                    receipt = EffectReceipt(
                        digest,
                        OWNER,
                        status="completed",
                        output={"profile": PROFILE, "value_digest": value_digest},
                    )
                    self.connection.execute(
                        "INSERT INTO memory_read_observations VALUES (?,?,?,?,?,?,?)",
                        (
                            identity.key,
                            handle.invocation_id,
                            handle.workflow_fingerprint,
                            digest,
                            request,
                            value_json,
                            value_digest,
                        ),
                    )
                    insert_completed_receipt(self.connection, identity, receipt)
                    existing = self._existing(identity, request)
                    if existing is None:
                        raise _invalid("observation_missing")
        receipt, value_json = existing
        if observer is not None:
            pending = observer(identity, receipt)
            if inspect.isawaitable(pending):
                await pending
        return cast(dict[str, Any] | None, json.loads(value_json))
