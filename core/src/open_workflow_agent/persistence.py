"""Common invocation metadata persistence and engine-neutral handles."""

from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .errors import (
    InvocationStateConflict,
    InvocationStateError,
    WorkflowDefinitionChanged,
    WorkflowExecutionError,
)
from .expressions import validate_expression_data
from .lifecycle import TERMINAL_INVOCATION_STATES, VALID_INVOCATION_TRANSITIONS
from .storage import StorageConnection, open_storage

_UNBOUND_INPUT = object()

if TYPE_CHECKING:
    from .ownership import ExecutionLease


@dataclass(slots=True)
class ExecutionHandle:
    invocation_id: str
    engine: str
    engine_execution_reference: str
    user_id: str | None
    session_id: str
    workflow_name: str
    workflow_version: str
    workflow_fingerprint: str
    status: str = "running"
    output: Any = None
    error: dict[str, Any] | None = None
    parent_invocation_id: str | None = None
    parent_task_reference: str | None = None
    # Deployment-level caller identity for object-level authorization.  This
    # is deliberately common invocation metadata, not an engine credential or
    # native execution reference.
    owner_principal: str | None = None
    # Exact durable snapshot for conditional writes; private runtime bookkeeping.
    _persisted_payload: str | None = field(default=None, init=False, repr=False, compare=False)
    _execution_lease: ExecutionLease | None = field(
        default=None, init=False, repr=False, compare=False
    )


def _public_fields(handle: ExecutionHandle) -> dict[str, Any]:
    return {item.name: getattr(handle, item.name) for item in fields(handle) if item.init}


def _encode_handle(handle: ExecutionHandle) -> str:
    payload = _public_fields(handle)
    # Repeated running/waiting transitions must not restore an old writer's
    # authority. This generation stays inside common storage, not public APIs.
    payload["_revision"] = uuid.uuid4().hex
    validate_expression_data(payload)
    return json.dumps(payload, ensure_ascii=False, allow_nan=False)


def _decode_payload(encoded: str) -> dict[str, Any]:
    def unique_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, value in pairs:
            if name in result:
                raise ValueError("duplicate invocation fields")
            result[name] = value
        return result

    payload = json.loads(encoded, object_pairs_hook=unique_fields)
    validate_expression_data(payload)
    if not isinstance(payload, dict):
        raise ValueError("invocation payload must be an object")
    if "_revision" in payload:
        revision = payload.pop("_revision")
        if (
            not isinstance(revision, str)
            or len(revision) != 32
            or uuid.UUID(revision).hex != revision
        ):
            raise ValueError("invalid invocation revision")
    payload.setdefault("owner_principal", None)
    return payload


def _verify_identity(handle: ExecutionHandle, payload: dict[str, Any]) -> None:
    for name, value in _public_fields(handle).items():
        if name not in {"status", "output", "error"} and payload.get(name) != value:
            raise InvocationStateError("invocation identity cannot be changed")


class InvocationStore:
    def __init__(self, database: str | Path = ":memory:") -> None:
        self.connection: StorageConnection = open_storage(database, "owa_runtime")
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS invocations (
            invocation_id TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL)"""
        )
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS invocation_inputs (
            invocation_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, payload TEXT NOT NULL)"""
        )
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS invocation_terminal_times "
            "(invocation_id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
        )
        self.connection.commit()

    def bind_input(self, handle: ExecutionHandle, input_data: Any) -> Any:
        """Retain the first raw workflow input separately from resume messages."""

        validate_expression_data(input_data)
        try:
            encoded = json.dumps(input_data, ensure_ascii=False, allow_nan=False)
            self.connection.execute(
                """INSERT INTO invocation_inputs(invocation_id, fingerprint, payload)
                VALUES (?, ?, ?) ON CONFLICT(invocation_id) DO NOTHING""",
                (handle.invocation_id, handle.workflow_fingerprint, encoded),
            )
            self.connection.commit()
            return self.read_input(handle)
        except WorkflowDefinitionChanged:
            raise
        except Exception:
            try:
                self.connection.raw.rollback()
            except Exception:
                pass
            raise WorkflowExecutionError("invocation input could not be recovered") from None

    def read_input(self, handle: ExecutionHandle) -> Any:
        """Read an existing binding without creating one from a resume message."""
        try:
            row = self.connection.execute(
                "SELECT fingerprint, payload FROM invocation_inputs WHERE invocation_id = ?",
                (handle.invocation_id,),
            ).fetchone()
            if row is None:
                raise ValueError("invocation input is absent")
            if row[0] != handle.workflow_fingerprint:
                raise WorkflowDefinitionChanged(
                    "persisted workflow input belongs to a different definition"
                )
            value = json.loads(row[1])
            validate_expression_data(value)
            return value
        except WorkflowDefinitionChanged:
            raise
        except Exception:
            try:
                self.connection.raw.rollback()
            except Exception:
                pass
            raise WorkflowExecutionError("invocation input could not be recovered") from None

    def create(
        self,
        *,
        engine: str,
        session_id: str | None,
        user_id: str | None,
        workflow_name: str,
        workflow_version: str,
        workflow_fingerprint: str,
        parent_invocation_id: str | None = None,
        parent_task_reference: str | None = None,
        owner_principal: str | None = None,
    ) -> ExecutionHandle:
        handle = self.create_in_transaction(
            self.connection,
            engine=engine,
            session_id=session_id,
            user_id=user_id,
            workflow_name=workflow_name,
            workflow_version=workflow_version,
            workflow_fingerprint=workflow_fingerprint,
            parent_invocation_id=parent_invocation_id,
            parent_task_reference=parent_task_reference,
            owner_principal=owner_principal,
        )
        self.connection.commit()
        return handle

    def create_in_transaction(
        self,
        connection: StorageConnection,
        *,
        engine: str,
        session_id: str | None,
        user_id: str | None,
        workflow_name: str,
        workflow_version: str,
        workflow_fingerprint: str,
        parent_invocation_id: str | None = None,
        parent_task_reference: str | None = None,
        owner_principal: str | None = None,
        input_data: Any = _UNBOUND_INPUT,
    ) -> ExecutionHandle:
        """Insert common identity/input as a participant; the caller owns commit.

        Schedule slots and event trigger cycles use the same handle encoding
        and input binding as ordinary invocations. No engine state is inserted.
        """
        encoded_input: str | None = None
        if input_data is not _UNBOUND_INPUT:
            validate_expression_data(input_data)
            encoded_input = json.dumps(input_data, ensure_ascii=False, allow_nan=False)
        handle = ExecutionHandle(
            invocation_id=str(uuid.uuid4()),
            engine=engine,
            engine_execution_reference=str(uuid.uuid4()),
            user_id=user_id,
            session_id=session_id or str(uuid.uuid4()),
            workflow_name=workflow_name,
            workflow_version=workflow_version,
            workflow_fingerprint=workflow_fingerprint,
            parent_invocation_id=parent_invocation_id,
            parent_task_reference=parent_task_reference,
            owner_principal=owner_principal,
        )
        encoded = _encode_handle(handle)
        connection.execute(
            "INSERT INTO invocations(invocation_id, payload, status) VALUES (?, ?, ?)",
            (handle.invocation_id, encoded, handle.status),
        )
        if encoded_input is not None:
            connection.execute(
                "INSERT INTO invocation_inputs(invocation_id, fingerprint, payload) "
                "VALUES (?, ?, ?)",
                (handle.invocation_id, handle.workflow_fingerprint, encoded_input),
            )
        handle._persisted_payload = encoded
        return handle

    def get(self, invocation_id: str) -> ExecutionHandle | None:
        try:
            row = self.connection.execute(
                "SELECT payload, status FROM invocations WHERE invocation_id = ?", (invocation_id,)
            ).fetchone()
            if row is None:
                return None
            handle = ExecutionHandle(**_decode_payload(row[0]))
            if handle.invocation_id != invocation_id or handle.status != row[1]:
                raise ValueError("invocation columns disagree")
            if handle.status not in VALID_INVOCATION_TRANSITIONS:
                raise ValueError("invalid invocation state")
            handle._persisted_payload = row[0]
            return handle
        except Exception:
            raise WorkflowExecutionError("invocation state could not be recovered") from None

    def refresh(self, handle: ExecutionHandle, *, terminal_only: bool = False) -> ExecutionHandle:
        """A stale executor may reload terminal outcomes without adopting active state."""

        current = self.get(handle.invocation_id)
        if current is None:
            raise WorkflowExecutionError("invocation state could not be recovered")
        _verify_identity(handle, _public_fields(current))
        if terminal_only and current.status not in TERMINAL_INVOCATION_STATES:
            raise InvocationStateConflict("invocation state has changed; reload before retry")
        for name, value in _public_fields(current).items():
            setattr(handle, name, value)
        handle._persisted_payload = current._persisted_payload
        return handle

    def update(
        self, handle: ExecutionHandle, *, completed_at: float | None = None, **changes: Any
    ) -> ExecutionHandle:
        expected = handle._persisted_payload
        if expected is None:
            raise InvocationStateError("invocation handle must be loaded before writing")
        original = _decode_payload(expected)
        _verify_identity(handle, original)
        requested_status = changes.get("status", handle.status)
        allowed = VALID_INVOCATION_TRANSITIONS.get(original["status"], frozenset())
        if requested_status not in allowed:
            raise InvocationStateError(
                f"invalid invocation transition: {handle.status} -> {requested_status}",
                details={"from": handle.status, "to": requested_status},
            )
        known_changes = {
            key: value for key, value in changes.items() if key in _public_fields(handle)
        }
        candidate = replace(handle, **known_changes)
        _verify_identity(candidate, original)
        if original["status"] in TERMINAL_INVOCATION_STATES:
            current = self.get(handle.invocation_id)
            if current is None or current._persisted_payload != expected:
                raise InvocationStateConflict("invocation state has changed; reload before retry")
            return self.refresh(handle, terminal_only=True)
        try:
            encoded = _encode_handle(candidate)
            statement = """UPDATE invocations SET payload = ?, status = ?
                WHERE invocation_id = ? AND status = ? AND payload = ?"""
            parameters: tuple[Any, ...] = (
                encoded,
                candidate.status,
                candidate.invocation_id,
                original["status"],
                expected,
            )
            lease = handle._execution_lease
            if lease is not None:
                from .ownership import execution_write_guard

                if (
                    lease.invocation_id != handle.invocation_id
                    or lease.engine != handle.engine
                    or lease.workflow_fingerprint != handle.workflow_fingerprint
                ):
                    raise InvocationStateConflict("invocation execution ownership is inconsistent")
                guard, owner_parameters = execution_write_guard(self.connection, lease)
                if self.connection.is_postgresql:
                    # Lock the ownership row through this state transaction;
                    # an expiry/takeover cannot cross the guarded common write.
                    statement = (
                        f"WITH valid_execution_owner AS MATERIALIZED ({guard}) "
                        + statement
                        + " AND EXISTS (SELECT 1 FROM valid_execution_owner)"
                    )
                    parameters = (*owner_parameters, *parameters)
                else:
                    # A single SQLite UPDATE holds the shared database writer
                    # lock until commit, including evaluation of this guard.
                    statement += f" AND EXISTS ({guard})"
                    parameters = (*parameters, *owner_parameters)

            def write() -> None:
                updated = self.connection.execute(statement, parameters)
                if updated.rowcount != 1:
                    raise InvocationStateConflict(
                        "invocation state has changed; reload before retry"
                    )
                if candidate.status in TERMINAL_INVOCATION_STATES:
                    self._record_terminal_time(candidate, encoded, completed_at)

            if self.connection.is_postgresql and candidate.status in TERMINAL_INVOCATION_STATES:
                # The owner/CAS update and completion timestamp share one commit.
                with self.connection.raw.transaction():
                    write()
            else:
                write()
            self.connection.commit()
        except InvocationStateConflict:
            try:
                self.connection.raw.rollback()
            except Exception:
                pass
            raise
        except Exception:
            try:
                self.connection.raw.rollback()
            except Exception:
                pass
            raise WorkflowExecutionError("invocation state could not be stored") from None
        for key, value in known_changes.items():
            setattr(handle, key, value)
        handle._persisted_payload = encoded
        return handle

    def _record_terminal_time(
        self, handle: ExecutionHandle, snapshot: str, completed_at: float | None
    ) -> None:
        instant = time.time() if completed_at is None else completed_at
        if isinstance(instant, bool) or not math.isfinite(instant) or instant <= 0:
            raise InvocationStateError("terminal completion timestamp is invalid")
        payload = json.dumps(
            {
                "profile": "owa/terminal-time/v1",
                "fingerprint": handle.workflow_fingerprint,
                "status": handle.status,
                "completed_at": instant,
                "snapshot_digest": hashlib.sha256(snapshot.encode()).hexdigest(),
            },
            sort_keys=True,
            allow_nan=False,
        )
        existing = self.connection.execute(
            "SELECT payload FROM invocation_terminal_times WHERE invocation_id=?",
            (handle.invocation_id,),
        ).fetchone()
        if existing is not None and existing[0] != payload:
            raise InvocationStateConflict("terminal completion timestamp already disagrees")
        self.connection.execute(
            "INSERT INTO invocation_terminal_times(invocation_id,payload) VALUES (?, ?) "
            "ON CONFLICT(invocation_id) DO NOTHING",
            (handle.invocation_id, payload),
        )

    @contextmanager
    def _operator_control_transaction(self) -> Iterator[None]:
        if self.connection.is_postgresql:
            with self.connection.raw.transaction():
                yield
            return
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            yield
            self.connection.commit()
        except BaseException:
            self.connection.raw.rollback()
            raise

    def cancel_authoritatively(
        self,
        handle: ExecutionHandle,
        *,
        error: dict[str, Any],
        completed_at: float | None = None,
    ) -> ExecutionHandle:
        """Commit an authorized operator control and execution revocation together.

        Caller authorization remains at the existing operator boundary. This
        control does not adopt a worker lease or establish any remote undo.
        """
        return self._cancel_controlled(handle, error=error, completed_at=completed_at)

    def cancel_under_parent_control(
        self,
        handle: ExecutionHandle,
        parent: ExecutionHandle,
        *,
        execution_lease: ExecutionLease,
        error: dict[str, Any],
        completed_at: float | None = None,
    ) -> ExecutionHandle:
        """Fence a workflow-owned child cancellation through its parent's commit."""
        if (
            execution_lease is None
            or execution_lease.invocation_id != parent.invocation_id
            or execution_lease.engine != parent.engine
            or execution_lease.workflow_fingerprint != parent.workflow_fingerprint
        ):
            raise InvocationStateConflict("parent execution ownership is inconsistent")
        return self._cancel_controlled(
            handle,
            error=error,
            completed_at=completed_at,
            parent=parent,
            execution_lease=execution_lease,
        )

    def _cancel_controlled(
        self,
        handle: ExecutionHandle,
        *,
        error: dict[str, Any],
        completed_at: float | None,
        parent: ExecutionHandle | None = None,
        execution_lease: ExecutionLease | None = None,
    ) -> ExecutionHandle:
        from .ownership import owned_write_transaction

        expected = handle._persisted_payload
        if expected is None:
            raise InvocationStateError("invocation handle must be loaded before control")
        original = _decode_payload(expected)
        _verify_identity(handle, original)
        validate_expression_data(error)
        if parent is not None:
            if parent._persisted_payload is None:
                raise InvocationStateError("parent handle must be loaded before control")
            _verify_identity(parent, _decode_payload(parent._persisted_payload))
            if (
                handle.invocation_id == parent.invocation_id
                or handle.parent_invocation_id != parent.invocation_id
                or handle.engine != parent.engine
                or handle.owner_principal != parent.owner_principal
                or handle.user_id != parent.user_id
                or handle.session_id == parent.session_id
            ):
                raise InvocationStateConflict("child control identity is inconsistent")
        transaction = (
            self._operator_control_transaction()
            if parent is None
            else owned_write_transaction(
                self.connection,
                execution_lease,
                invocation_id=parent.invocation_id,
                workflow_fingerprint=parent.workflow_fingerprint,
            )
        )
        try:
            with transaction:
                if parent is not None:
                    lock = " FOR SHARE" if self.connection.is_postgresql else ""
                    row = self.connection.execute(
                        "SELECT payload,status FROM invocations WHERE invocation_id=?" + lock,
                        (parent.invocation_id,),
                    ).fetchone()
                    if (
                        row is None
                        or row[0] != parent._persisted_payload
                        or row[1] not in {"running", "waiting"}
                    ):
                        raise InvocationStateConflict("parent control state has changed")
                # Native/common writers lock ownership before invocation state.
                # Take the same order to avoid a revoke-versus-checkpoint deadlock.
                if self.connection.is_postgresql:
                    has_ownership = (
                        self.connection.execute(
                            "SELECT to_regclass('invocation_ownership')"
                        ).fetchone()[0]
                        is not None
                    )
                else:
                    has_ownership = (
                        self.connection.execute(
                            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                            ("invocation_ownership",),
                        ).fetchone()
                        is not None
                    )
                owner = None
                if has_ownership:
                    lock = " FOR UPDATE" if self.connection.is_postgresql else ""
                    owner = self.connection.execute(
                        "SELECT engine,fingerprint FROM invocation_ownership "
                        "WHERE invocation_id=?" + lock,
                        (handle.invocation_id,),
                    ).fetchone()
                    if owner is not None and owner != (
                        handle.engine,
                        handle.workflow_fingerprint,
                    ):
                        raise InvocationStateConflict("controlled execution identity disagrees")
                current = self.get(handle.invocation_id)
                if current is None:
                    raise InvocationStateConflict("controlled invocation is absent")
                _verify_identity(handle, _public_fields(current))
                if current.status in TERMINAL_INVOCATION_STATES:
                    if (
                        original["status"] in TERMINAL_INVOCATION_STATES
                        and current._persisted_payload != expected
                    ):
                        raise InvocationStateConflict("terminal control state has changed")
                    result = current
                else:
                    if current._persisted_payload != expected:
                        raise InvocationStateConflict("controlled invocation state has changed")
                    result = replace(current, status="cancelled", output=None, error=error)
                    encoded = _encode_handle(result)
                    changed = self.connection.execute(
                        "UPDATE invocations SET payload=?,status='cancelled' "
                        "WHERE invocation_id=? AND status=? AND payload=?",
                        (encoded, handle.invocation_id, current.status, expected),
                    )
                    if changed.rowcount != 1:
                        raise InvocationStateConflict("controlled invocation state has changed")
                    self._record_terminal_time(result, encoded, completed_at)
                    result._persisted_payload = encoded
                if owner is not None and result.status == "cancelled":
                    self.connection.execute(
                        "UPDATE invocation_ownership SET expires_at=0 "
                        "WHERE invocation_id=? AND engine=? AND fingerprint=?",
                        (handle.invocation_id, handle.engine, handle.workflow_fingerprint),
                    )
        except InvocationStateConflict:
            raise
        except InvocationStateError:
            raise
        except Exception:
            raise WorkflowExecutionError(
                "invocation cancellation control could not be stored"
            ) from None
        for name, value in _public_fields(result).items():
            setattr(handle, name, value)
        handle._persisted_payload = result._persisted_payload
        return handle

    def terminal_completed_at(self, handle: ExecutionHandle) -> float:
        """Read the original terminal instant bound to the immutable common snapshot."""
        try:
            current = self.get(handle.invocation_id)
            if current is None or current.status not in TERMINAL_INVOCATION_STATES:
                raise ValueError("invocation is not terminal")
            _verify_identity(handle, _public_fields(current))
            row = self.connection.execute(
                "SELECT payload FROM invocation_terminal_times WHERE invocation_id=?",
                (handle.invocation_id,),
            ).fetchone()
            if row is None or current._persisted_payload is None:
                raise ValueError("terminal timestamp is absent")
            value = json.loads(row[0])
            instant = value["completed_at"]
            if (
                set(value)
                != {"profile", "fingerprint", "status", "completed_at", "snapshot_digest"}
                or value["profile"] != "owa/terminal-time/v1"
                or value["fingerprint"] != current.workflow_fingerprint
                or value["status"] != current.status
                or value["snapshot_digest"]
                != hashlib.sha256(current._persisted_payload.encode()).hexdigest()
                or isinstance(instant, bool)
                or not isinstance(instant, (int, float))
                or not math.isfinite(instant)
                or instant <= 0
            ):
                raise ValueError("terminal timestamp disagrees")
            return float(instant)
        except Exception:
            raise WorkflowExecutionError(
                "terminal completion time is unavailable or inconsistent"
            ) from None

    def verify_fingerprint(self, handle: ExecutionHandle, fingerprint: str) -> None:
        if handle.workflow_fingerprint != fingerprint:
            raise WorkflowDefinitionChanged(
                "persisted workflow definition differs from the loaded definition",
                details={"stored": handle.workflow_fingerprint, "current": fingerprint},
            )

    def close(self) -> None:
        self.connection.close()
