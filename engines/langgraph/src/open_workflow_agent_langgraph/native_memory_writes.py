"""Private authorized writers and read-only native marker verification."""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from open_workflow_agent.effects import effect_request_digest
from open_workflow_agent.errors import ConfigurationError, ToolError
from open_workflow_agent.execution_context import current_action_context
from open_workflow_agent.memory_mutations import TABLE, MemoryMutation, MemoryMutationMarker
from open_workflow_agent.memory_reads import MemoryReadBinding
from open_workflow_agent.memory_retention import MemoryRecordRetention
from open_workflow_agent.memory_writes import MemoryMutationBackend
from open_workflow_agent.persistence import ExecutionHandle
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.storage import is_postgresql_datasource

from .native_memory_mutations import apply_native_mutation


def mutation_storage_identity(database: str, retention: MemoryRecordRetention | None = None) -> str:
    """Bind the installed location without retaining authentication secrets."""
    if is_postgresql_datasource(database):
        from psycopg.conninfo import conninfo_to_dict

        location: Any = {
            key: value
            for key, value in conninfo_to_dict(database).items()
            if key not in {"password", "sslpassword", "connect_timeout", "application_name"}
        }
    else:
        location = str(Path(database).resolve()) if database != ":memory:" else database
    identity = effect_request_digest("owa/memory-storage/v1", {"location": location})
    if retention is not None:
        return effect_request_digest(
            "owa/memory-storage-policy/v1",
            {"location": identity, "retention": retention.descriptor},
        )
    return identity


def inspect_native_marker(database: str, request: MemoryMutation) -> MemoryMutationMarker | None:
    """Bounded read-only lookup; never initializes tables, schemas or files."""
    postgresql = is_postgresql_datasource(database)
    size = "octet_length" if postgresql else "length"
    columns = ",".join(
        f"CASE WHEN {size}({name})<={bound} THEN {name} ELSE NULL END"
        for name, bound in (
            ("identity", 64),
            ("invocation_id", 256),
            ("fingerprint", 256),
            ("request_digest", 64),
        )
    )
    statement = f"SELECT {columns} FROM {TABLE} WHERE identity=" + ("%s" if postgresql else "?")
    try:
        if postgresql:
            import psycopg

            with psycopg.connect(database, autocommit=True, connect_timeout=5) as connection:
                with connection.transaction():
                    connection.execute("SET TRANSACTION READ ONLY")
                    connection.execute("SET LOCAL statement_timeout=5000")
                    row = connection.execute(statement, (request.identity.key,)).fetchone()
        else:
            with closing(
                sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
            ) as connection:
                connection.execute("PRAGMA query_only=ON")
                row = connection.execute(statement, (request.identity.key,)).fetchone()
    except Exception:
        raise ToolError(
            "native memory marker could not be verified", details={"reason": "marker_unavailable"}
        ) from None
    if row is None:
        return None
    marker = MemoryMutationMarker(*row)
    if marker != MemoryMutationMarker.for_request(request):
        raise ToolError(
            "native memory marker is inconsistent", details={"reason": "marker_changed"}
        )
    return marker


def mutation_backend(
    services: RuntimeServices,
    database: str,
    binding: MemoryReadBinding,
    retention: MemoryRecordRetention | None = None,
) -> MemoryMutationBackend:
    def inspect(request: MemoryMutation) -> MemoryMutationMarker | None:
        if request.retention != retention:
            raise ConfigurationError("native memory retention differs from the installed policy")
        return inspect_native_marker(database, request)

    async def apply(request: MemoryMutation, handle: ExecutionHandle) -> MemoryMutationMarker:
        if request.retention != retention:
            raise ConfigurationError("native memory retention differs from the installed policy")
        if handle._execution_lease is None:
            raise ConfigurationError("native memory requires original execution ownership")
        return await apply_native_mutation(
            database,
            request,
            services.execution_ownership.native_fence(handle._execution_lease),
            invocations=services.invocations,
            handle=handle,
        )

    return MemoryMutationBackend(
        binding,
        mutation_storage_identity(database, retention),
        apply,
        inspect,
    )


class NativeMemoryWriter:
    """Private deployment binding; no namespace, provider or path in method inputs."""

    def __init__(
        self,
        services: RuntimeServices,
        binding: MemoryReadBinding,
        collection: str,
        *,
        max_record_bytes: int = 65_536,
        retention: MemoryRecordRetention | None = None,
    ) -> None:
        if (
            type(collection) is not str
            or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", collection)
            or type(max_record_bytes) is not int
            or not 1 <= max_record_bytes <= 65_536
            or (retention is not None and not isinstance(retention, MemoryRecordRetention))
        ):
            raise ValueError("native memory writes require finite collection and record bounds")
        self._services, self._binding = services, binding
        self._collection, self._maximum = collection, max_record_bytes
        self._retention = retention

    async def put(self, key: str, value: dict[str, Any]) -> None:
        await self._write(key, value, delete=False)

    async def delete(self, key: str) -> None:
        await self._write(key, None, delete=True)

    async def _write(self, key: str, value: dict[str, Any] | None, *, delete: bool) -> None:
        if not self._services.memory_enabled:
            raise ConfigurationError("native memory is disabled by deployment")
        context = current_action_context()
        namespace = self._services._memory_namespace()
        if context is None or namespace is None:
            raise ConfigurationError("native memory requires original invocation authority")
        if delete:
            request = MemoryMutation(
                context.identity,
                self._binding,
                namespace,
                self._collection,
                key,
                "delete",
                None,
                self._maximum,
                self._retention,
            )
        else:
            if type(value) is not dict:
                raise ToolError("native memory put requires a finite JSON object")
            request = MemoryMutation.put(
                context.identity,
                self._binding,
                namespace,
                self._collection,
                key,
                value,
                max_record_bytes=self._maximum,
                retention=self._retention,
            )
        await self._services.authorize_current_action(
            "memory.delete" if delete else "memory.add", "memory:self", request.request_digest
        )
        await self._services.memory_writes.mutate(
            context.handle, request, observer=context.observer
        )
