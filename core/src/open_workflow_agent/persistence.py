"""Common invocation metadata persistence and engine-neutral handles."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .errors import InvocationStateError, WorkflowDefinitionChanged
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


class InvocationStore:
    def __init__(self, database: str | Path = ":memory:") -> None:
        self.connection: StorageConnection = open_storage(database, "owa_runtime")
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS invocations (
            invocation_id TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL)"""
        )
        self.connection.commit()

    def create(
        self,
        *,
        engine: str,
        session_id: str | None,
        user_id: str | None,
        workflow_name: str,
        workflow_version: str,
        workflow_fingerprint: str,
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
        return ExecutionHandle(**json.loads(row[0])) if row else None

    def update(self, handle: ExecutionHandle, **changes: Any) -> ExecutionHandle:
        requested_status = changes.get("status", handle.status)
        allowed = VALID_INVOCATION_TRANSITIONS.get(handle.status, frozenset())
        if requested_status not in allowed:
            raise InvocationStateError(
                f"invalid invocation transition: {handle.status} -> {requested_status}",
                details={"from": handle.status, "to": requested_status},
            )
        for key, value in changes.items():
            if hasattr(handle, key):
                setattr(handle, key, value)
        self.connection.execute(
            "UPDATE invocations SET payload = ?, status = ? WHERE invocation_id = ?",
            (json.dumps(asdict(handle)), handle.status, handle.invocation_id),
        )
        self.connection.commit()
        return handle

    def verify_fingerprint(self, handle: ExecutionHandle, fingerprint: str) -> None:
        if handle.workflow_fingerprint != fingerprint:
            raise WorkflowDefinitionChanged(
                "persisted workflow definition differs from the loaded definition",
                details={"stored": handle.workflow_fingerprint, "current": fingerprint},
            )

    def close(self) -> None:
        self.connection.close()
