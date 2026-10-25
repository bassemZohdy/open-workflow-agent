"""Private deployment-side stores; public native memory admission stays closed."""

from __future__ import annotations

import asyncio
import math
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path

from langgraph.store.base import BaseStore
from open_workflow_agent.errors import ConfigurationError
from open_workflow_agent.memory_capacity import MemoryNamespaceBounds
from open_workflow_agent.memory_reads import MemoryReadBinding
from open_workflow_agent.memory_retention import MemoryRecordRetention
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.storage import is_postgresql_datasource

from .native_memory import NativeMemoryReadBounds, NativeMemoryReader
from .native_memory_expiry import expiry_filtered_store
from .native_memory_writes import NativeMemoryWriter, mutation_backend, mutation_storage_identity


async def _close(stack: AsyncExitStack, timeout: float) -> None:
    try:
        async with asyncio.timeout(timeout):
            await stack.aclose()
    except Exception:
        raise ConfigurationError(
            "native memory store close failed", details={"reason": "store_close_failure"}
        ) from None


@dataclass(frozen=True, slots=True)
class NativeMemoryProvider:
    """Trusted installed binding, never a workflow-supplied provider or path."""

    services: RuntimeServices
    store: BaseStore
    binding: MemoryReadBinding
    retention: MemoryRecordRetention | None = None
    namespace_limits: MemoryNamespaceBounds | None = None

    def writer(self, collection: str, *, max_record_bytes: int = 65_536) -> NativeMemoryWriter:
        return NativeMemoryWriter(
            self.services,
            self.binding,
            collection,
            max_record_bytes=max_record_bytes,
            retention=self.retention,
            namespace_limits=self.namespace_limits,
        )

    def reader(
        self, collection: str, *, bounds: NativeMemoryReadBounds | None = None
    ) -> NativeMemoryReader:
        return NativeMemoryReader(
            self.services, self.store, collection=collection, bounds=bounds, binding=self.binding
        )


@asynccontextmanager
async def open_native_memory_provider(
    services: RuntimeServices,
    *,
    provider_id: str,
    version: str,
    setup_timeout_seconds: float = 10.0,
    retention: MemoryRecordRetention | None = None,
    namespace_limits: MemoryNamespaceBounds | None = None,
) -> AsyncIterator[NativeMemoryProvider]:
    """Open separate embedded storage or the deployment's optional PostgreSQL.

    No semantic index, embedding service or TTL sweeper is started here.
    The private writer may apply a typed deployment-side record expiry policy.
    Dependency and platform checks precede namespace/directory creation.
    """
    if not services.memory_enabled:
        raise ConfigurationError("native memory is disabled by deployment")
    if retention is not None and not isinstance(retention, MemoryRecordRetention):
        raise ValueError("native memory requires a typed installed retention policy")
    if namespace_limits is not None and not isinstance(namespace_limits, MemoryNamespaceBounds):
        raise ValueError("native memory requires typed installed namespace bounds")
    if (
        type(setup_timeout_seconds) not in (int, float)
        or not 0 < setup_timeout_seconds <= 30
        or not math.isfinite(setup_timeout_seconds)
    ):
        raise ValueError("native memory setup requires a finite deadline of at most 30 seconds")
    path = services.memory_provider_database_path("langgraph", create_namespace=False)
    postgresql = is_postgresql_datasource(path)
    binding = MemoryReadBinding(provider_id, version, "postgresql" if postgresql else "sqlite")
    module_name, class_name = (
        ("langgraph.store.postgres.aio", "AsyncPostgresStore")
        if postgresql
        else ("langgraph.store.sqlite.aio", "AsyncSqliteStore")
    )
    try:
        store_type = getattr(import_module(module_name), class_name)
        store_type = expiry_filtered_store(store_type, postgresql=postgresql)
    except (ImportError, AttributeError):
        raise ConfigurationError(
            "native memory requires its installed storage extra",
            details={"reason": "missing_dependency"},
        ) from None
    proactor_loop = getattr(asyncio, "ProactorEventLoop", None)
    if (
        postgresql
        and proactor_loop is not None
        and isinstance(asyncio.get_running_loop(), proactor_loop)
    ):
        raise ConfigurationError(
            "LangGraph PostgreSQL memory requires a selector event loop on Windows",
            details={"reason": "unsupported_event_loop"},
        )
    stack = AsyncExitStack()
    services.memory_writes.check_binding(
        binding, mutation_storage_identity(path, retention, namespace_limits)
    )
    try:
        async with asyncio.timeout(setup_timeout_seconds):
            path = services.memory_provider_database_path("langgraph")
            if not postgresql:
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            store = await stack.enter_async_context(store_type.from_conn_string(path))
            await store.setup()
            from .native_memory_mutations import prepare_mutation_storage

            await prepare_mutation_storage(store, postgresql=postgresql)
            services.memory_writes.register(
                mutation_backend(services, path, binding, retention, namespace_limits)
            )
    except BaseException as error:
        try:
            await _close(stack, setup_timeout_seconds)
        except Exception:
            pass  # Retain the original setup failure or cancellation.
        if isinstance(error, asyncio.CancelledError) or not isinstance(error, Exception):
            raise
        raise ConfigurationError(
            "native memory store setup failed", details={"reason": "store_setup_failure"}
        ) from None
    try:
        yield NativeMemoryProvider(services, store, binding, retention, namespace_limits)
    except BaseException:
        try:
            await _close(stack, setup_timeout_seconds)
        except Exception:
            pass  # Never replace the caller's original error with cleanup.
        raise
    else:
        await _close(stack, setup_timeout_seconds)
