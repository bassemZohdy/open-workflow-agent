"""Private owned state-control intents; native checkpoints stay in the adapter."""

from __future__ import annotations

import asyncio
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
    ReceiptDependency,
    _receipt_payload,
    effect_request_digest,
)
from .errors import InvocationStateError, ModelError, OwaError
from .execution_authority import ExecutionAuthority
from .execution_authorization import ExecutionAuthorizationStore
from .memory_reads import _bounded_column
from .model_responses import bounded_model_json
from .ownership import execution_write_guard, owned_write_transaction
from .persistence import ExecutionHandle, InvocationStore, _verify_identity
from .storage import StorageConnection

PROFILE = "owa/state-update/v1"
ATTESTED_PROFILE = "owa/state-update/v2"
PREFIX = "policy:state-update:v1:"
OWNER = "state-update:v1"
TABLE = "state_update_intents_v1"
MAX_UPDATES = 64
_HEX = re.compile(r"[a-f0-9]{64}")
_TASK = re.compile(r"/do/(0|[1-9][0-9]*)/(?:[^/~\x00-\x1f]|~[01])+")


def state_update_error(reason: str) -> InvocationStateError:
    return InvocationStateError(
        "state update evidence is unavailable or inconsistent",
        details={"stage": "state_update", "reason": reason},
    )


def _json(value: Any, limit: int = 131_072) -> str:
    try:
        return bounded_model_json(value, limit=limit)
    except ModelError:
        raise state_update_error("data_invalid") from None


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise state_update_error("duplicate_field")
        result[key] = value
    return result


@dataclass(frozen=True, slots=True)
class StateUpdateRequest:
    invocation_id: str
    workflow_fingerprint: str
    request_id: str
    base_reference: str
    task_reference: str
    values_json: str
    policy_digest: str
    storage_identity: str
    base_digest: str | None = None
    projection_digest: str | None = None
    previous_controls: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (self.base_digest is None) != (self.projection_digest is None) or any(
            value is not None and (type(value) is not str or not _HEX.fullmatch(value))
            for value in (self.base_digest, self.projection_digest)
        ):
            raise state_update_error("attestation_invalid")
        if (
            type(self.previous_controls) is not tuple
            or len(self.previous_controls) > 63
            or self.request_id in self.previous_controls
            or any(
                type(value) is not str or not _HEX.fullmatch(value)
                for value in self.previous_controls
            )
            or len(set(self.previous_controls)) != len(self.previous_controls)
            or (self.projection_digest is None and self.previous_controls)
        ):
            raise state_update_error("control_lineage_invalid")
        if any(
            type(value) is not str or not _HEX.fullmatch(value)
            for value in (
                self.request_id,
                self.base_reference,
                self.policy_digest,
                self.storage_identity,
            )
        ) or any(
            type(value) is not str
            or not 0 < len(value.encode()) <= 256
            or any(ord(character) < 32 for character in value)
            for value in (self.invocation_id, self.workflow_fingerprint)
        ):
            raise state_update_error("request_invalid")
        if (
            type(self.task_reference) is not str
            or len(self.task_reference.encode()) > 4096
            or not _TASK.fullmatch(self.task_reference)
        ):
            raise state_update_error("task_invalid")
        if type(self.values_json) is not str or len(self.values_json.encode()) > 65_536:
            raise state_update_error("value_invalid")
        try:
            value = json.loads(self.values_json, object_pairs_hook=_unique)
            if type(value) is not dict or _json(value, 65_536) != self.values_json:
                raise state_update_error("value_invalid")
        except (ValueError, RecursionError):
            raise state_update_error("value_invalid") from None
        _json(self.descriptor)

    @classmethod
    def create(cls, *, values: dict[str, Any], **bindings: Any) -> StateUpdateRequest:
        return cls(values_json=_json(values, 65_536), **bindings)

    @property
    def identity(self) -> EffectIdentity:
        # Changing the attributed task must conflict with the original request,
        # not manufacture another idempotency slot for the same control ID.
        return EffectIdentity(
            self.invocation_id,
            self.workflow_fingerprint,
            "/do",
            (("/do", 1),),
            PREFIX + self.request_id,
        )

    @property
    def descriptor(self) -> dict[str, Any]:
        value = asdict(self)
        if self.projection_digest is None:
            value.pop("base_digest")
            value.pop("projection_digest")
            value.pop("previous_controls")
        else:
            value["previous_controls"] = list(self.previous_controls)
        return {"profile": self.profile, **value}

    @property
    def profile(self) -> str:
        return PROFILE if self.projection_digest is None else ATTESTED_PROFILE

    @classmethod
    def from_descriptor(cls, value: Any) -> StateUpdateRequest:
        if not isinstance(value, dict) or type(value.get("profile")) is not str:
            raise state_update_error("request_invalid")
        legacy = set(cls.__dataclass_fields__) - {
            "base_digest",
            "projection_digest",
            "previous_controls",
        }
        if (
            type(value) is not dict
            or (value.get("profile") == PROFILE and set(value) != {"profile", *legacy})
            or (
                value.get("profile") == ATTESTED_PROFILE
                and (
                    set(value) != {"profile", *cls.__dataclass_fields__}
                    or value.get("base_digest") is None
                    or value.get("projection_digest") is None
                )
            )
            or value.get("profile") not in {PROFILE, ATTESTED_PROFILE}
        ):
            raise state_update_error("request_invalid")
        fields = {key: item for key, item in value.items() if key != "profile"}
        if value["profile"] == ATTESTED_PROFILE:
            if type(fields["previous_controls"]) is not list:
                raise state_update_error("control_lineage_invalid")
            fields["previous_controls"] = tuple(fields["previous_controls"])
        return cls(**fields)

    @property
    def request_digest(self) -> str:
        return effect_request_digest(self.profile, self.descriptor)

    @property
    def values(self) -> dict[str, Any]:
        return json.loads(self.values_json)  # type: ignore[no-any-return]


@dataclass(frozen=True, slots=True)
class StateUpdateProof:
    request_digest: str
    base_reference: str
    result_reference: str

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not _HEX.fullmatch(value) for value in asdict(self).values()
        ):
            raise state_update_error("proof_invalid")
        if self.base_reference == self.result_reference:
            raise state_update_error("proof_invalid")

    def verify(self, request: StateUpdateRequest) -> None:
        if (self.request_digest, self.base_reference) != (
            request.request_digest,
            request.base_reference,
        ):
            raise state_update_error("proof_changed")


@dataclass(frozen=True, slots=True)
class StateUpdateBackend:
    storage_identity: str
    apply: Callable[[StateUpdateRequest, ExecutionHandle], Awaitable[StateUpdateProof]]
    inspect: Callable[[StateUpdateRequest], StateUpdateProof | None]

    def __post_init__(self) -> None:
        if (
            type(self.storage_identity) is not str
            or not _HEX.fullmatch(self.storage_identity)
            or not callable(self.apply)
            or not callable(self.inspect)
        ):
            raise ValueError("state updates require an installed bounded backend")


def _exists(connection: StorageConnection) -> bool:
    if connection.is_postgresql:
        return connection.execute("SELECT to_regclass(?)", (TABLE,)).fetchone()[0] is not None
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)
        ).fetchone()
        is not None
    )


def _prepare(connection: StorageConnection) -> None:
    if connection.is_postgresql:
        connection.execute("SELECT pg_advisory_xact_lock(?,?)", (0x4F5741, 0x53554A31))
    if _exists(connection):
        return
    if connection.execute(
        "SELECT 1 FROM workflow_effects WHERE owner=? LIMIT 1", (OWNER,)
    ).fetchone():
        raise state_update_error("intent_missing")
    connection.execute(
        f"CREATE TABLE {TABLE} (identity TEXT PRIMARY KEY,invocation_id TEXT NOT NULL,"
        "fingerprint TEXT NOT NULL,request_json TEXT NOT NULL,request_digest TEXT NOT NULL,"
        "authorization_digest TEXT NOT NULL,phase TEXT NOT NULL,proof_json TEXT)"
    )
    connection.execute(f"CREATE INDEX state_update_invocation_v1 ON {TABLE}(invocation_id)")


def _receipt(request: StateUpdateRequest, proof: StateUpdateProof | None) -> EffectReceipt:
    return EffectReceipt(
        request.request_digest,
        OWNER,
        status="pending" if proof is None else "completed",
        output=None if proof is None else {"profile": request.profile, **asdict(proof)},
    )


class StateUpdates:
    """Common control audit only; no native state or plan is persisted here.

    Unknown dispatched operations cannot be sent again. All database transactions
    end before awaiting the native updater. No public capability installs this yet.
    """

    def __init__(
        self,
        journal: EffectJournal,
        invocations: InvocationStore,
        authorizations: ExecutionAuthorizationStore,
    ) -> None:
        if (
            journal._invocations is not invocations
            or authorizations.journal is not journal
            or authorizations.invocations is not invocations
            or journal._state_updates is not None
        ):
            raise ValueError("state controls require original common services")
        self.journal, self.invocations, self.authorizations = journal, invocations, authorizations
        self.connection = journal.connection
        self._backends: dict[str, StateUpdateBackend] = {}
        self._locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
        journal._state_updates = self

    def register(self, backend: StateUpdateBackend) -> None:
        if not isinstance(backend, StateUpdateBackend):
            raise ValueError("state controls require a typed backend")
        if backend.storage_identity in self._backends:
            raise state_update_error("backend_already_installed")
        self._backends[backend.storage_identity] = backend

    def _backend(self, request: StateUpdateRequest) -> StateUpdateBackend:
        backend = self._backends.get(request.storage_identity)
        if backend is None:
            raise state_update_error("backend_missing")
        return backend

    def _proof(self, request: StateUpdateRequest) -> StateUpdateProof | None:
        proof = self._backend(request).inspect(request)
        if proof is not None:
            if not isinstance(proof, StateUpdateProof):
                raise state_update_error("proof_invalid")
            proof.verify(request)
        return proof

    def _handle(self, handle: ExecutionHandle, request: StateUpdateRequest) -> None:
        saved = self.invocations.get(handle.invocation_id)
        if (
            saved is None
            or saved._persisted_payload is None
            or saved.status not in {"running", "waiting"}
            or handle.status != saved.status
            or handle._execution_lease is None
            or handle.owner_principal is None
            or request.invocation_id != handle.invocation_id
            or request.workflow_fingerprint != handle.workflow_fingerprint
        ):
            raise state_update_error("invocation_invalid")
        _verify_identity(handle, json.loads(saved._persisted_payload))

    def _load(
        self, identity: EffectIdentity, receipt: EffectReceipt
    ) -> tuple[StateUpdateRequest, str]:
        if not _exists(self.connection):
            raise state_update_error("intent_missing")
        row = self.connection.execute(
            "SELECT invocation_id,fingerprint,"
            + ",".join(
                _bounded_column(self.connection, key, bound)
                for key, bound in (
                    ("request_json", 131_072),
                    ("request_digest", 64),
                    ("authorization_digest", 64),
                    ("phase", 16),
                    ("proof_json", 1024),
                )
            )
            + f" FROM {TABLE} WHERE identity=?",
            (identity.key,),
        ).fetchone()
        if (
            row is None
            or tuple(row[:2]) != (identity.invocation_id, identity.workflow_fingerprint)
            or type(row[2]) is not str
            or row[5] not in {"prepared", "dispatched", "completed"}
        ):
            raise state_update_error("intent_changed")
        request = StateUpdateRequest.from_descriptor(json.loads(row[2], object_pairs_hook=_unique))
        if (
            request.identity != identity
            or row[2] != _json(request.descriptor)
            or row[3] != request.request_digest
        ):
            raise state_update_error("request_changed")
        index = self.connection.execute(
            "SELECT invocation_id,fingerprint,"
            + _bounded_column(self.connection, "payload", 8192)
            + " FROM workflow_effect_invocations WHERE identity=?",
            (identity.key,),
        ).fetchone()
        if (
            index is None
            or tuple(index[:2]) != tuple(row[:2])
            or type(index[2]) is not str
            or EffectIdentity.from_value(json.loads(index[2], object_pairs_hook=_unique))
            != identity
        ):
            raise state_update_error("identity_missing")
        decision = self.authorizations.inspect_decision(
            identity, "state.update", "invocation:" + request.invocation_id, request.request_digest
        ).receipt
        if (
            decision is None
            or decision.status != "completed"
            or ReceiptDependency._digest(decision) != row[4]
        ):
            raise state_update_error("authority_changed")
        native = self._proof(request)
        proof = None
        if row[5] == "completed":
            if type(row[6]) is not str:
                raise state_update_error("proof_missing")
            proof = StateUpdateProof(**json.loads(row[6], object_pairs_hook=_unique))
            proof.verify(request)
            if proof != native:
                raise state_update_error("proof_changed")
        elif row[6] is not None or (row[5] == "prepared" and native is not None):
            raise state_update_error("proof_orphaned")
        if receipt != _receipt(request, proof):
            raise state_update_error("receipt_changed")
        return request, row[5]

    def verify(self, identity: EffectIdentity, receipt: EffectReceipt) -> None:
        self._load(identity, receipt)

    def completed_request(self, identity: EffectIdentity) -> StateUpdateRequest:
        receipt = self.journal.get(identity)
        if receipt is None or receipt.status != "completed":
            raise state_update_error("control_acknowledgement_required")
        request, phase = self._load(identity, receipt)
        if phase != "completed" or request.projection_digest is None:
            raise state_update_error("control_attestation_required")
        return request

    def attested_ids(
        self, invocation_id: str, fingerprint: str, *, prepared_request_id: str | None = None
    ) -> set[str]:
        """All version-two control occurrences, including unresolved dispatches."""
        if not _exists(self.connection):
            return set()
        rows = self.connection.execute(
            "SELECT identity,"
            + _bounded_column(self.connection, "request_json", 131_072)
            + f" FROM {TABLE} WHERE invocation_id=? LIMIT ?",
            (invocation_id, MAX_UPDATES + 1),
        ).fetchall()
        if len(rows) > MAX_UPDATES:
            raise state_update_error("update_limit")
        result = set()
        for key, encoded in rows:
            request = StateUpdateRequest.from_descriptor(
                json.loads(encoded, object_pairs_hook=_unique)
            )
            if request.identity.key != key or request.workflow_fingerprint != fingerprint:
                raise state_update_error("identity_missing")
            if request.projection_digest is not None:
                if request.request_id == prepared_request_id:
                    receipt = self.journal.get(request.identity)
                    if receipt is None:
                        raise state_update_error("intent_missing")
                    _, phase = self._load(request.identity, receipt)
                    if phase == "prepared":
                        # Only the same not-yet-dispatched command may rebuild
                        # its original preflight after process restart.
                        continue
                self.completed_request(request.identity)
                result.add(request.request_id)
        return result

    def verify_recovery(self, identity: EffectIdentity, receipt: EffectReceipt) -> None:
        request, phase = self._load(identity, receipt)
        if phase == "dispatched" and self._proof(request) is None:
            raise state_update_error("outcome_unknown")

    async def update(
        self,
        handle: ExecutionHandle,
        request: StateUpdateRequest,
        authority: ExecutionAuthority | None,
    ) -> StateUpdateProof:
        try:
            return await self._update(handle, request, authority)
        except OwaError:
            raise
        except Exception:
            raise state_update_error("control_failure") from None

    async def _update(
        self,
        handle: ExecutionHandle,
        request: StateUpdateRequest,
        authority: ExecutionAuthority | None,
    ) -> StateUpdateProof:
        self._handle(handle, request)
        if authority is None or authority.principal.identity != handle.owner_principal:
            raise state_update_error("original_owner_required")
        resource = "invocation:" + handle.invocation_id
        self.authorizations.authorize_request(authority, "state.update", resource)
        lock = self._locks.setdefault(request.identity.key, asyncio.Lock())
        async with lock:
            # A retained decision cannot substitute for today's policy checks.
            decision = self.authorizations.authorize(
                handle,
                request.identity,
                "state.update",
                resource,
                request.request_digest,
                execution_lease=handle._execution_lease,
            )
            if decision is None:
                raise state_update_error("authorization_required")
            authorization_digest = ReceiptDependency._digest(decision.receipt)
            with owned_write_transaction(
                self.connection,
                handle._execution_lease,
                invocation_id=handle.invocation_id,
                workflow_fingerprint=handle.workflow_fingerprint,
            ):
                receipt = self.journal.get(request.identity)
                if receipt is None:
                    if (
                        self.connection.execute(
                            "SELECT 1 FROM workflow_effect_invocations WHERE identity=?",
                            (request.identity.key,),
                        ).fetchone()
                        is not None
                        or self._proof(request) is not None
                    ):
                        raise state_update_error("intent_orphaned")
                    _prepare(self.connection)
                    if (
                        self.connection.execute(
                            f"SELECT COUNT(*) FROM {TABLE} WHERE invocation_id=?",
                            (handle.invocation_id,),
                        ).fetchone()[0]
                        >= MAX_UPDATES
                    ):
                        raise state_update_error("update_limit")
                    self.connection.execute(
                        f"INSERT INTO {TABLE} VALUES (?,?,?,?,?,?,?,NULL)",
                        (
                            request.identity.key,
                            handle.invocation_id,
                            handle.workflow_fingerprint,
                            _json(request.descriptor),
                            request.request_digest,
                            authorization_digest,
                            "prepared",
                        ),
                    )
                    receipt = _receipt(request, None)
                    insert_pending_receipt(self.connection, request.identity, receipt)
                stored, phase = self._load(request.identity, receipt)
                if stored != request:
                    raise state_update_error("request_changed")
            if phase == "completed":
                proof = self._proof(request)
                assert proof is not None
                return proof
            proof = self._proof(request)
            if phase == "dispatched" and proof is None:
                raise state_update_error("outcome_unknown")
            if phase == "prepared":
                with owned_write_transaction(
                    self.connection,
                    handle._execution_lease,
                    invocation_id=handle.invocation_id,
                    workflow_fingerprint=handle.workflow_fingerprint,
                ):
                    latest = self.journal.get(request.identity)
                    if latest is None or self._load(request.identity, latest)[1] != "prepared":
                        raise state_update_error("intent_changed")
                    self.connection.execute(
                        f"UPDATE {TABLE} SET phase='dispatched' "
                        "WHERE identity=? AND phase='prepared'",
                        (request.identity.key,),
                    )
                proof = await self._backend(request).apply(request, handle)
                if not isinstance(proof, StateUpdateProof):
                    raise state_update_error("proof_invalid")
                proof.verify(request)
            self._handle(handle, request)
            self.authorizations.authorize_request(authority, "state.update", resource)
            self.authorizations.verify_invocation(handle)
            with owned_write_transaction(
                self.connection,
                handle._execution_lease,
                invocation_id=handle.invocation_id,
                workflow_fingerprint=handle.workflow_fingerprint,
            ):
                latest = self.journal.get(request.identity)
                if (
                    latest is None
                    or self._load(request.identity, latest)[1] != "dispatched"
                    or proof is None
                    or self._proof(request) != proof
                ):
                    raise state_update_error("proof_missing")
                self.connection.execute(
                    f"UPDATE {TABLE} SET phase='completed',proof_json=? "
                    "WHERE identity=? AND phase='dispatched'",
                    (_json(asdict(proof), 1024), request.identity.key),
                )
                receipt = _receipt(request, proof)
                changed = self.connection.execute(
                    "UPDATE workflow_effects SET status='completed',payload=? "
                    "WHERE identity=? AND owner=? AND status='pending'",
                    (_json(_receipt_payload(receipt), 4096), request.identity.key, OWNER),
                )
                if changed.rowcount != 1 or self.journal.get(request.identity) != receipt:
                    raise state_update_error("receipt_changed")
                assert handle._execution_lease is not None
                statement, values = execution_write_guard(self.connection, handle._execution_lease)
                if self.connection.execute(statement, values).fetchone() is None:
                    raise state_update_error("ownership_lost")
            return proof


def verify_state_update_index(
    connection: StorageConnection, invocation_id: str, fingerprint: str, identities: set[str]
) -> None:
    if not _exists(connection):
        if identities:
            raise state_update_error("intent_missing")
        return
    rows = connection.execute(
        f"SELECT identity,fingerprint FROM {TABLE} WHERE invocation_id=? LIMIT ?",
        (invocation_id, MAX_UPDATES + 1),
    ).fetchall()
    if (
        len(rows) > MAX_UPDATES
        or any(row[1] != fingerprint for row in rows)
        or {row[0] for row in rows} != identities
    ):
        raise state_update_error("identity_missing")
