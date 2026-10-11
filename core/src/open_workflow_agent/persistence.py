"""Common invocation metadata persistence and engine-neutral handles."""

from __future__ import annotations

import json
import uuid
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
        self.connection.execute(
            "INSERT INTO invocations(invocation_id, payload, status) VALUES (?, ?, ?)",
            (handle.invocation_id, encoded, handle.status),
        )
        self.connection.commit()
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

    def update(self, handle: ExecutionHandle, **changes: Any) -> ExecutionHandle:
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
            updated = self.connection.execute(statement, parameters)
            if updated.rowcount != 1:
                raise InvocationStateConflict("invocation state has changed; reload before retry")
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

    def verify_fingerprint(self, handle: ExecutionHandle, fingerprint: str) -> None:
        if handle.workflow_fingerprint != fingerprint:
            raise WorkflowDefinitionChanged(
                "persisted workflow definition differs from the loaded definition",
                details={"stored": handle.workflow_fingerprint, "current": fingerprint},
            )

    def close(self) -> None:
        self.connection.close()
