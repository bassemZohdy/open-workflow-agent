"""Common invocation metadata persistence and engine-neutral handles."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from .errors import InvocationStateError, WorkflowDefinitionChanged, WorkflowExecutionError
from .expressions import validate_expression_data
from .lifecycle import VALID_INVOCATION_TRANSITIONS
from .storage import StorageConnection, open_storage


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
        self.connection.execute(
            "INSERT INTO invocations(invocation_id, payload, status) VALUES (?, ?, ?)",
            (handle.invocation_id, json.dumps(asdict(handle)), handle.status),
        )
        self.connection.commit()
        return handle

    def get(self, invocation_id: str) -> ExecutionHandle | None:
        row = self.connection.execute(
            "SELECT payload FROM invocations WHERE invocation_id = ?", (invocation_id,)
        ).fetchone()
        if row is None:
            return None
        payload = json.loads(row[0])
        # Keep persisted handles readable across the addition of the common
        # ownership field; old rows represent trusted/unowned invocations.
        payload.setdefault("owner_principal", None)
        return ExecutionHandle(**payload)

    def update(self, handle: ExecutionHandle, **changes: Any) -> ExecutionHandle:
        requested_status = changes.get("status", handle.status)
        allowed = VALID_INVOCATION_TRANSITIONS.get(handle.status, frozenset())
        if requested_status not in allowed:
            raise InvocationStateError(
                f"invalid invocation transition: {handle.status} -> {requested_status}",
                details={"from": handle.status, "to": requested_status},
            )
        known_changes = {key: value for key, value in changes.items() if hasattr(handle, key)}
        candidate = replace(handle, **known_changes)
        try:
            updated = self.connection.execute(
                "UPDATE invocations SET payload = ?, status = ? WHERE invocation_id = ?",
                (json.dumps(asdict(candidate)), candidate.status, candidate.invocation_id),
            )
            if updated.rowcount != 1:
                raise ValueError("invocation is absent")
            self.connection.commit()
        except Exception:
            try:
                self.connection.raw.rollback()
            except Exception:
                pass
            raise WorkflowExecutionError("invocation state could not be stored") from None
        for key, value in known_changes.items():
            setattr(handle, key, value)
        return handle

    def verify_fingerprint(self, handle: ExecutionHandle, fingerprint: str) -> None:
        if handle.workflow_fingerprint != fingerprint:
            raise WorkflowDefinitionChanged(
                "persisted workflow definition differs from the loaded definition",
                details={"stored": handle.workflow_fingerprint, "current": fingerprint},
            )

    def close(self) -> None:
        self.connection.close()
