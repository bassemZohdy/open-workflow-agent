"""Common private write intents, native evidence and owned completion receipts."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from typing import Any
from weakref import WeakValueDictionary

from ._effect_storage import insert_pending_receipt
from .effects import (
    EffectIdentity,
    EffectJournal,
    EffectReceipt,
    _receipt_payload,
    effect_request_digest,
)
from .errors import OwaError
from .memory_mutations import (
    MAX_MUTATIONS,
    PROFILE,
    MemoryMutation,
    MemoryMutationMarker,
    mutation_error,
)
from .memory_reads import MemoryReadBinding, _bounded_column, _unique
from .model_responses import bounded_model_json
from .ownership import execution_write_guard, owned_write_transaction
from .persistence import ExecutionHandle, InvocationStore
from .storage import StorageConnection

OWNER = "memory-write:v1"
PREFIX = "policy:memory-mutation:v1:"
TABLE = "memory_write_intents_v1"


@dataclass(frozen=True, slots=True)
class MemoryMutationBackend:
    """Installed backend functions; workflow data cannot supply either callback."""

    binding: MemoryReadBinding
    storage_identity: str
    apply: Callable[[MemoryMutation, ExecutionHandle], Awaitable[MemoryMutationMarker]]
    inspect: Callable[[MemoryMutation], MemoryMutationMarker | None]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.binding, MemoryReadBinding)
            or self.binding.backend == "memory"
            or type(self.storage_identity) is not str
            or not re.fullmatch(r"[a-f0-9]{64}", self.storage_identity)
            or not callable(self.apply)
            or not callable(self.inspect)
        ):
            raise ValueError("memory writes require an installed finite backend binding")


def _table_exists(connection: StorageConnection) -> bool:
    if connection.is_postgresql:
        return connection.execute("SELECT to_regclass(?)", (TABLE,)).fetchone()[0] is not None
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)
        ).fetchone()
        is not None
    )


def _ensure_schema(connection: StorageConnection) -> None:
    if connection.is_postgresql:
        connection.execute("SELECT pg_advisory_xact_lock(?,?)", (0x4F5741, 0x4D574A31))
    if _table_exists(connection):
        return
    if (
        connection.execute(
            "SELECT 1 FROM workflow_effects WHERE owner=? LIMIT 1", (OWNER,)
        ).fetchone()
        is not None
    ):
        raise mutation_error("intent_missing")
    connection.execute(
        f"CREATE TABLE {TABLE} (identity TEXT PRIMARY KEY,"
        "invocation_id TEXT NOT NULL,fingerprint TEXT NOT NULL,request_json TEXT NOT NULL,"
        "request_digest TEXT NOT NULL,storage_identity TEXT NOT NULL,phase TEXT NOT NULL)"
    )
    connection.execute(f"CREATE INDEX memory_write_invocation_v1 ON {TABLE}(invocation_id)")


def _receipt(request: MemoryMutation, *, completed: bool) -> EffectReceipt:
    return EffectReceipt(
        request.request_digest,
        OWNER,
        status="completed" if completed else "pending",
        output={
            "profile": PROFILE,
            "marker_digest": effect_request_digest(
                PROFILE + ":marker", asdict(MemoryMutationMarker.for_request(request))
            ),
        }
        if completed
        else None,
    )


class MemoryWrites:
    """No native provider is awaited while a common transaction is held.

    A dispatched intent with no native evidence has an unknown outcome. It is
    never automatically dispatched again, including after process restart.
    """

    def __init__(self, journal: EffectJournal, invocations: InvocationStore) -> None:
        if journal._invocations is not invocations:
            raise ValueError("memory writes require their original invocation store")
        self.journal, self.invocations, self.connection = journal, invocations, journal.connection
        if journal._memory_writes is not None:
            raise ValueError("memory writes are already installed on this journal")
        journal._memory_writes = self
        self._backends: dict[MemoryReadBinding, MemoryMutationBackend] = {}
        self._locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()

    def check_binding(self, binding: MemoryReadBinding, storage_identity: str) -> None:
        previous = self._backends.get(binding)
        if previous is not None and previous.storage_identity != storage_identity:
            raise mutation_error("provider_changed")

    def register(self, backend: MemoryMutationBackend) -> None:
        if not isinstance(backend, MemoryMutationBackend):
            raise ValueError("memory writes require a typed installed backend")
        self.check_binding(backend.binding, backend.storage_identity)
        self._backends[backend.binding] = backend

    def _backend(self, request: MemoryMutation) -> MemoryMutationBackend:
        backend = self._backends.get(request.binding)
        if backend is None:
            raise mutation_error("provider_unavailable")
        return backend

    def _marker(
        self, backend: MemoryMutationBackend, request: MemoryMutation
    ) -> MemoryMutationMarker | None:
        try:
            marker = backend.inspect(request)
        except OwaError:
            raise
        except Exception:
            raise mutation_error("marker_inspection_failed") from None
        if marker is not None and marker != MemoryMutationMarker.for_request(request):
            raise mutation_error("marker_changed")
        return marker

    def _load(
        self, identity: EffectIdentity, receipt: EffectReceipt
    ) -> tuple[MemoryMutation, str, MemoryMutationBackend]:
        if not _table_exists(self.connection):
            raise mutation_error("intent_missing")
        row = self.connection.execute(
            "SELECT invocation_id,fingerprint,"
            + _bounded_column(self.connection, "request_json", 131_072)
            + f",request_digest,storage_identity,phase FROM {TABLE} WHERE identity=?",
            (identity.key,),
        ).fetchone()
        if (
            row is None
            or row[0] != identity.invocation_id
            or row[1] != identity.workflow_fingerprint
            or type(row[2]) is not str
            or row[5] not in {"prepared", "dispatched", "completed"}
        ):
            raise mutation_error("intent_changed")
        request = MemoryMutation.from_descriptor(json.loads(row[2], object_pairs_hook=_unique))
        backend = self._backend(request)
        saved = self.invocations.get(identity.invocation_id)
        if (
            request.identity != identity
            or row[2] != bounded_model_json(request.descriptor, limit=131_072)
            or row[3] != request.request_digest
            or row[4] != backend.storage_identity
            or saved is None
            or saved.owner_principal is None
            or request.namespace
            != "owa/principal-memory/v1:"
            + hashlib.sha256(saved.owner_principal.encode()).hexdigest()
        ):
            raise mutation_error("binding_changed")
        index = self.connection.execute(
            "SELECT invocation_id,fingerprint,"
            + _bounded_column(self.connection, "payload", 131_072)
            + " FROM workflow_effect_invocations WHERE identity=?",
            (identity.key,),
        ).fetchone()
        if (
            index is None
            or index[0] != identity.invocation_id
            or index[1] != identity.workflow_fingerprint
            or type(index[2]) is not str
            or EffectIdentity.from_value(json.loads(index[2], object_pairs_hook=_unique))
            != identity
        ):
            raise mutation_error("identity_missing")
        if receipt != _receipt(request, completed=row[5] == "completed"):
            raise mutation_error("receipt_changed")
        marker = self._marker(backend, request)
        if (row[5] == "completed" and marker is None) or (
            row[5] == "prepared" and marker is not None
        ):
            raise mutation_error("marker_missing_or_orphaned")
        return request, row[5], backend

    def verify(self, identity: EffectIdentity, receipt: EffectReceipt) -> None:
        self._load(identity, receipt)

    def verify_recovery(self, identity: EffectIdentity, receipt: EffectReceipt) -> None:
        request, phase, backend = self._load(identity, receipt)
        if phase == "dispatched" and self._marker(backend, request) is None:
            raise mutation_error("outcome_unknown")

    async def mutate(
        self,
        handle: ExecutionHandle,
        request: MemoryMutation,
        *,
        observer: Callable[..., Any] | None = None,
    ) -> None:
        request.verify_invocation(self.invocations, handle)
        if request.binding.backend != self.connection.kind:
            raise mutation_error("ownership_storage_unsupported")
        lock = self._locks.setdefault(request.identity.key, asyncio.Lock())
        async with lock:
            await self._mutate(handle, request, observer)

    async def _mutate(
        self, handle: ExecutionHandle, request: MemoryMutation, observer: Callable[..., Any] | None
    ) -> None:
        identity = request.identity
        backend = self._backend(request)
        request.verify_invocation(self.invocations, handle)
        lease = handle._execution_lease
        assert lease is not None
        with owned_write_transaction(
            self.connection,
            lease,
            invocation_id=handle.invocation_id,
            workflow_fingerprint=handle.workflow_fingerprint,
        ):
            receipt = self.journal.get(identity)
            if receipt is None:
                if (
                    self.connection.execute(
                        "SELECT 1 FROM workflow_effect_invocations WHERE identity=?",
                        (identity.key,),
                    ).fetchone()
                    is not None
                    or (
                        _table_exists(self.connection)
                        and self.connection.execute(
                            f"SELECT 1 FROM {TABLE} WHERE identity=?", (identity.key,)
                        ).fetchone()
                        is not None
                    )
                    or self._marker(backend, request) is not None
                ):
                    raise mutation_error("intent_orphaned")
                _ensure_schema(self.connection)
                count = self.connection.execute(
                    f"SELECT COUNT(*) FROM {TABLE} WHERE invocation_id=?", (handle.invocation_id,)
                ).fetchone()[0]
                if count >= MAX_MUTATIONS:
                    raise mutation_error("mutation_limit")
                self.connection.execute(
                    f"INSERT INTO {TABLE} VALUES (?,?,?,?,?,?,?)",
                    (
                        identity.key,
                        handle.invocation_id,
                        handle.workflow_fingerprint,
                        bounded_model_json(request.descriptor, limit=131_072),
                        request.request_digest,
                        backend.storage_identity,
                        "prepared",
                    ),
                )
                receipt = _receipt(request, completed=False)
                insert_pending_receipt(self.connection, identity, receipt)
                if self.journal._execution_authorization_intent is not None:
                    self.journal._execution_authorization_intent(
                        identity,
                        receipt,
                        execution_lease=lease,
                        first_intent=True,
                    )
            saved_request, phase, _ = self._load(identity, receipt)
            if saved_request != request:
                raise mutation_error("request_changed")
        if phase != "completed":
            marker = self._marker(backend, request)
            if phase == "dispatched" and marker is None:
                raise mutation_error("outcome_unknown")
            if phase == "prepared":
                with owned_write_transaction(
                    self.connection,
                    lease,
                    invocation_id=handle.invocation_id,
                    workflow_fingerprint=handle.workflow_fingerprint,
                ):
                    latest = self.journal.get(identity)
                    if latest is None or self._load(identity, latest)[1] != "prepared":
                        raise mutation_error("intent_changed")
                    self.connection.execute(
                        f"UPDATE {TABLE} SET phase='dispatched' "
                        "WHERE identity=? AND phase='prepared'",
                        (identity.key,),
                    )
                try:
                    marker = await backend.apply(request, handle)
                except OwaError:
                    raise
                except Exception:
                    raise mutation_error("provider_failure") from None
                if marker != MemoryMutationMarker.for_request(request):
                    raise mutation_error("marker_changed")
            request.verify_invocation(self.invocations, handle)
            with owned_write_transaction(
                self.connection,
                lease,
                invocation_id=handle.invocation_id,
                workflow_fingerprint=handle.workflow_fingerprint,
            ):
                latest = self.journal.get(identity)
                if (
                    latest is None
                    or self._load(identity, latest)[1] != "dispatched"
                    or self._marker(backend, request) is None
                ):
                    raise mutation_error("marker_missing_or_orphaned")
                receipt = _receipt(request, completed=True)
                if self.journal._execution_authorization_seal is not None:
                    self.journal._execution_authorization_seal(
                        identity, receipt, execution_lease=lease
                    )
                self.connection.execute(
                    f"UPDATE {TABLE} SET phase='completed' WHERE identity=? AND phase='dispatched'",
                    (identity.key,),
                )
                self.connection.execute(
                    "UPDATE workflow_effects SET status='completed',payload=? "
                    "WHERE identity=? AND owner=? AND status='pending'",
                    (
                        json.dumps(_receipt_payload(receipt), ensure_ascii=False, allow_nan=False),
                        identity.key,
                        OWNER,
                    ),
                )
                if self.journal.get(identity) != receipt:
                    raise mutation_error("receipt_changed")
                guard, values = execution_write_guard(self.connection, lease)
                if self.connection.execute(guard, values).fetchone() is None:
                    raise mutation_error("ownership_lost")
        if observer is not None:
            result = observer(identity, receipt)
            if inspect.isawaitable(result):
                await result


def verify_memory_write_index(
    connection: StorageConnection, invocation_id: str, fingerprint: str, identities: set[str]
) -> None:
    if not _table_exists(connection):
        if identities:
            raise mutation_error("intent_missing")
        return
    rows = connection.execute(
        f"SELECT identity,fingerprint FROM {TABLE} WHERE invocation_id=? LIMIT ?",
        (invocation_id, MAX_MUTATIONS + 1),
    ).fetchall()
    if (
        len(rows) > MAX_MUTATIONS
        or any(row[1] != fingerprint for row in rows)
        or {row[0] for row in rows} != identities
    ):
        raise mutation_error("identity_missing")
