"""Atomic, private common-storage reservation for a native replay target.

Reservation does not admit, fork, or dispatch a workflow. The caller must use
an authorized native preflight and reconcile all later phases under ownership.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from open_workflow_agent.effects import EffectIdentity
from open_workflow_agent.execution_authority import ExecutionAuthority
from open_workflow_agent.persistence import ExecutionHandle, _public_fields, _verify_identity
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.state_lineage import PREFIX, StateLineageRequest, lineage_error
from open_workflow_agent.storage import StorageConnection
from open_workflow_agent.workflow import WorkflowPlan

from .native_lineage_preflight import NativeLineagePreflight, _owner

_TABLE = "native_replay_reservations_v1"


def _json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )


def _identity(handle: ExecutionHandle) -> dict[str, Any]:
    return {
        k: v for k, v in _public_fields(handle).items() if k not in {"status", "output", "error"}
    }


class NativeReplayReservations:
    """Store the request and its new common target in one transaction."""

    def __init__(self, services: RuntimeServices) -> None:
        self.services = services
        self.connection = services.effect_journal.connection
        self.connection.execute(
            f"CREATE TABLE IF NOT EXISTS {_TABLE} ("
            "identity TEXT PRIMARY KEY,target_id TEXT NOT NULL UNIQUE,payload TEXT NOT NULL,"
            "digest TEXT NOT NULL)"
        )
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS native_replay_reservation_lock_v1 "
            "(identity TEXT PRIMARY KEY, value INTEGER NOT NULL)"
        )
        self.connection.execute(
            "INSERT INTO native_replay_reservation_lock_v1(identity,value) VALUES ('global',1) "
            "ON CONFLICT(identity) DO NOTHING"
        )
        self.connection.commit()

    @contextmanager
    def _transaction(self) -> Iterator[StorageConnection]:
        connection = self.connection
        if connection.is_postgresql:
            with connection.raw.transaction():
                if connection.execute(
                    "SELECT value FROM native_replay_reservation_lock_v1 "
                    "WHERE identity='global' FOR UPDATE"
                ).fetchone() != (1,):
                    raise lineage_error("reservation_lock_unavailable")
                yield connection
        else:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if connection.execute(
                    "SELECT value FROM native_replay_reservation_lock_v1 WHERE identity='global'"
                ).fetchone() != (1,):
                    raise lineage_error("reservation_lock_unavailable")
                yield connection
                connection.commit()
            except BaseException:
                connection.raw.rollback()
                raise

    def reserve(
        self,
        source: ExecutionHandle,
        authority: ExecutionAuthority,
        plan: WorkflowPlan,
        preflight: NativeLineagePreflight,
    ) -> ExecutionHandle:
        """Reserve once; retries must carry the identical inspected command."""
        request = preflight.request
        _owner(self.services, source, authority, "replay")
        preflight.verify_basis(preflight.input_json, preflight.snapshots)
        if (
            request.kind != "replay"
            or request.source_invocation_id != source.invocation_id
            or request.workflow_fingerprint != source.workflow_fingerprint
            or request.workflow_fingerprint != plan.fingerprint
            or plan.native_state_replay is None
            or plan.native_state_replay.digest != request.policy_digest
            or _json(json.loads(preflight.input_json))
            != _json(self.services.invocations.read_input(source))
        ):
            raise lineage_error("reservation_request_invalid")
        with self._transaction() as connection:
            row = self._row(request.identity.key)
            if row is not None:
                return self._recover(row, request)
            if (
                connection.execute(
                    f"SELECT 1 FROM {_TABLE} WHERE target_id=?", (request.target_invocation_id,)
                ).fetchone()
                is not None
            ):
                raise lineage_error("reservation_target_conflict")
            saved = connection.execute(
                "SELECT payload FROM invocations WHERE invocation_id=?", (source.invocation_id,)
            ).fetchone()
            if saved is None:
                raise lineage_error("reservation_source_missing")
            _verify_identity(source, json.loads(saved[0]))
            if (
                connection.execute(
                    "SELECT 1 FROM invocations WHERE invocation_id=?",
                    (request.target_invocation_id,),
                ).fetchone()
                is not None
            ):
                raise lineage_error("reservation_target_conflict")
            target = self.services.invocations.create_in_transaction(
                connection,
                engine=source.engine,
                session_id=source.session_id,
                user_id=source.user_id,
                owner_principal=source.owner_principal,
                workflow_name=source.workflow_name,
                workflow_version=source.workflow_version,
                workflow_fingerprint=source.workflow_fingerprint,
                input_data=json.loads(preflight.input_json),
                reserved_invocation_id=request.target_invocation_id,
            )
            payload = _json(
                {
                    "request": request.descriptor,
                    "target": _identity(target),
                    "input_digest": hashlib.sha256(
                        _json(json.loads(preflight.input_json)).encode()
                    ).hexdigest(),
                }
            )
            connection.execute(
                f"INSERT INTO {_TABLE}(identity,target_id,payload,digest) VALUES (?,?,?,?)",
                (
                    request.identity.key,
                    target.invocation_id,
                    payload,
                    hashlib.sha256(payload.encode()).hexdigest(),
                ),
            )
            return target

    def recover(
        self, source: ExecutionHandle, authority: ExecutionAuthority, request_id: str
    ) -> tuple[StateLineageRequest, ExecutionHandle]:
        """Read an existing reservation without retaining private SDK coordinates."""
        _owner(self.services, source, authority, "replay")
        if type(request_id) is not str or not re.fullmatch(r"[a-f0-9]{64}", request_id):
            raise lineage_error("reservation_request_invalid")
        identity = EffectIdentity(
            source.invocation_id,
            source.workflow_fingerprint,
            "/do",
            (("/do", 1),),
            PREFIX + request_id,
        )
        row = self._row(identity.key)
        try:
            if row is None or type(row[1]) is not str or len(row[1].encode()) > 131_072:
                raise ValueError("reservation unavailable")
            request = StateLineageRequest.from_descriptor(json.loads(row[1])["request"])
            if request.identity != identity or request.kind != "replay":
                raise ValueError("reservation identity changed")
            return request, self._recover(row, request)
        except Exception:
            raise lineage_error("reservation_unavailable") from None

    def _row(self, identity: str) -> Any:
        size = (
            "octet_length(payload)"
            if self.connection.is_postgresql
            else "length(CAST(payload AS BLOB))"
        )
        return self.connection.execute(
            f"SELECT target_id,CASE WHEN {size}<=131072 THEN payload ELSE NULL END,digest "
            f"FROM {_TABLE} WHERE identity=?",
            (identity,),
        ).fetchone()

    def _recover(self, row: Any, request: StateLineageRequest) -> ExecutionHandle:
        try:
            target_id, payload, digest = row
            if type(payload) is not str or len(payload.encode()) > 131_072:
                raise ValueError("reservation payload invalid")
            if hashlib.sha256(payload.encode()).hexdigest() != digest:
                raise ValueError("reservation digest changed")
            value = json.loads(payload)
            if (
                set(value) != {"request", "target", "input_digest"}
                or value["request"] != request.descriptor
            ):
                raise ValueError("reservation request changed")
            target = self.services.invocations.get(target_id)
            if (
                target is None
                or target_id != request.target_invocation_id
                or _identity(target) != value["target"]
            ):
                raise ValueError("reservation target changed")
            actual = _json(self.services.invocations.read_input(target))
            if hashlib.sha256(actual.encode()).hexdigest() != value["input_digest"]:
                raise ValueError("reservation input changed")
            return target
        except Exception:
            raise lineage_error("reservation_unavailable") from None
