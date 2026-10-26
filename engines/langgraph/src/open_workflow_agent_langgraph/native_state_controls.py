"""Private common-control/native-updater binding; no public update route or policy."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Awaitable, Callable, Iterator
from contextlib import closing, contextmanager
from importlib import import_module
from pathlib import Path
from typing import Any

from open_workflow_agent.config import NativeStateConfig
from open_workflow_agent.persistence import ExecutionHandle
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.state_updates import (
    StateUpdateBackend,
    StateUpdateProof,
    StateUpdateRequest,
    state_update_error,
)
from open_workflow_agent.storage import is_postgresql_datasource
from open_workflow_agent.workflow import WorkflowPlan

from .graph import LangGraphScopeExecutor
from .native_state import state_reference
from .native_state_mutations import (
    TABLE,
    NativeStateWrite,
    NativeStateWriteProof,
    apply_native_state_update,
)
from .native_state_storage import checkpoint_index, guard_checkpoint, readonly_saver


@contextmanager
def _proof_connection(database: str) -> Iterator[tuple[Any, bool]]:
    if is_postgresql_datasource(database):
        with import_module("psycopg").connect(
            database, autocommit=True, connect_timeout=5
        ) as connection:
            with connection.transaction():
                connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                connection.execute("SET LOCAL statement_timeout=5000")
                connection.execute("SET LOCAL lock_timeout=5000")
                yield connection, True
        return
    if database == ":memory:" or not Path(database).is_file():
        raise state_update_error("native_storage_missing")
    with closing(
        sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    ) as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        try:
            yield connection, False
        finally:
            connection.rollback()


class LangGraphStateControls:
    """Installed private callbacks must implement the declared update semantics.

    This bridge handles identities and storage proof. Its callback must be a
    trusted prevalidated SDK updater, never workflow-supplied executable code.
    Public policy/schema/attribution and resume validation remain separate gates.
    """

    def __init__(
        self,
        services: RuntimeServices,
        plan: WorkflowPlan,
        updater: Callable[[Any, dict[str, Any], StateUpdateRequest], Awaitable[dict[str, Any]]],
        *,
        bounds: NativeStateConfig | None = None,
    ) -> None:
        self.services, self.plan, self.updater = services, plan, updater
        self.database = services.engine_database_path("langgraph", create_namespace=False)
        self.bounds = NativeStateConfig.model_validate((bounds or NativeStateConfig()).model_dump())
        self.storage_identity = hashlib.sha256(
            json.dumps(
                ["owa/langgraph-state-controls/v1", self.database, plan.fingerprint],
                separators=(",", ":"),
            ).encode()
        ).hexdigest()

    def backend(self) -> StateUpdateBackend:
        return StateUpdateBackend(self.storage_identity, self.apply, self.inspect)

    def _verify(self, request: StateUpdateRequest) -> None:
        if (
            request.storage_identity != self.storage_identity
            or request.workflow_fingerprint != self.plan.fingerprint
            or self.database
            != self.services.engine_database_path("langgraph", create_namespace=False)
        ):
            raise state_update_error("native_binding_changed")

    def inspect(self, request: StateUpdateRequest) -> StateUpdateProof | None:
        self._verify(request)
        root = LangGraphScopeExecutor(self.plan, None, request.invocation_id).checkpoint_config(
            "/do", 1
        )
        thread = root["configurable"]["thread_id"]
        try:
            with _proof_connection(self.database) as (connection, postgres):
                placeholder, size = ("%s", "octet_length") if postgres else ("?", "length")
                fields = (
                    ("invocation_id", 256),
                    ("fingerprint", 256),
                    ("request_digest", 64),
                    ("base_checkpoint", 36),
                    ("result_checkpoint", 36),
                )
                row = connection.execute(
                    "SELECT "
                    + ",".join(
                        f"CASE WHEN {size}({name})<={bound} THEN {name} ELSE NULL END"
                        for name, bound in fields
                    )
                    + f" FROM {TABLE} WHERE identity={placeholder}",
                    (request.identity.key,),
                ).fetchone()
                if row is None:
                    return None
                if tuple(row[:3]) != (
                    request.invocation_id,
                    request.workflow_fingerprint,
                    request.request_digest,
                ):
                    raise state_update_error("native_proof_changed")
                native = NativeStateWriteProof(
                    NativeStateWrite(request.identity.key, request.request_digest, row[3]), row[4]
                )
                parent = connection.execute(
                    "SELECT parent_checkpoint_id FROM checkpoints WHERE thread_id="
                    + placeholder
                    + " AND checkpoint_ns='' AND checkpoint_id="
                    + placeholder,
                    (thread, native.result_checkpoint),
                ).fetchone()
                if parent is None or parent[0] != native.request.base_checkpoint:
                    raise state_update_error("native_lineage_changed")
                proof = self._common_proof(request, native)
                proof.verify(request)
                return proof
        except Exception:
            raise state_update_error("native_proof_unavailable") from None

    @staticmethod
    def _common_proof(
        request: StateUpdateRequest, native: NativeStateWriteProof
    ) -> StateUpdateProof:
        return StateUpdateProof(
            request.request_digest,
            state_reference(
                request.invocation_id, request.workflow_fingerprint, native.request.base_checkpoint
            ),
            state_reference(
                request.invocation_id, request.workflow_fingerprint, native.result_checkpoint
            ),
        )

    async def apply(self, request: StateUpdateRequest, handle: ExecutionHandle) -> StateUpdateProof:
        self._verify(request)
        archived = await self.services.recovery_definitions.resolve_inspection(handle)
        if archived.fingerprint != self.plan.fingerprint or handle._execution_lease is None:
            raise state_update_error("native_definition_changed")
        root = LangGraphScopeExecutor(self.plan, None, handle.invocation_id).checkpoint_config(
            "/do", 1
        )
        thread = root["configurable"]["thread_id"]
        async with readonly_saver(self.database, self.bounds) as (saver, postgres):
            index, _ = await checkpoint_index(saver, thread, self.bounds, postgres)
            keys = [
                key
                for key, _parent in index
                if state_reference(handle.invocation_id, self.plan.fingerprint, key)
                == request.base_reference
            ]
            if len(keys) != 1:
                raise state_update_error("native_base_unavailable")
            base = keys[0]
            await guard_checkpoint(saver, thread, base, self.bounds.max_native_bytes, postgres)

        async def update(saver: Any, config: dict[str, Any]) -> dict[str, Any]:
            return await self.updater(saver, config, request)

        native = await apply_native_state_update(
            self.database,
            self.services.execution_ownership.native_fence(handle._execution_lease),
            handle,
            self.plan,
            NativeStateWrite(request.identity.key, request.request_digest, base),
            update,
        )
        self._verify(request)
        return self._common_proof(request, native)
