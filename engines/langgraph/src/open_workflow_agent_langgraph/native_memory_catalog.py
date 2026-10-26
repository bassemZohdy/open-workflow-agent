"""Installed native stores exposed only through versioned OWS catalog calls."""

from __future__ import annotations

import copy
from contextlib import AsyncExitStack
from typing import Any

from open_workflow_agent.catalog import CatalogContext, CatalogFunction
from open_workflow_agent.config import NativeMemoryBindingConfig
from open_workflow_agent.errors import ConfigurationError, ToolError
from open_workflow_agent.memory_capacity import MemoryNamespaceBounds
from open_workflow_agent.memory_retention import MemoryRecordRetention
from open_workflow_agent.memory_search import MemorySearchQuery
from open_workflow_agent.native_memory_contract import OPERATIONS, references, validate_arguments
from open_workflow_agent.services import RuntimeServices

from .native_memory import NativeMemoryReadBounds
from .native_memory_embeddings import NativeMemoryEmbeddings
from .native_memory_provider import NativeMemoryProvider, open_native_memory_provider
from .native_memory_semantic_storage import verify_semantic_storage
from .native_storage_logging import protect_native_storage_logging


class NativeMemoryCatalog:
    def __init__(self, services: RuntimeServices) -> None:
        self.services = services
        self.bindings = copy.deepcopy(services.config.memory.native)
        self.providers: dict[str, NativeMemoryProvider] = {}
        self.handlers: dict[str, CatalogFunction] = {}
        self.stack = AsyncExitStack()
        self.opened = False

    def verify(self) -> None:
        if (
            not self.opened
            or self.services.config.memory.native != self.bindings
            or not self.services.memory_enabled
            or self.services.config.memory.enabled is False
            or any(
                not self.services.catalog.is_registered(name, function)
                for name, function in self.handlers.items()
            )
        ):
            raise ConfigurationError("native memory deployment binding changed or is unavailable")
        for binding in self.bindings:
            provider = self.providers[binding.id]
            if provider.semantic is not None:
                if (
                    self.services.native_memory_semantic_profiles.get(binding.id)
                    != provider.semantic
                ):
                    raise ConfigurationError("native memory semantic artifact binding changed")
                verify_semantic_storage(
                    self.services.memory_provider_database_path(
                        "langgraph", create_namespace=False
                    ),
                    provider.binding,
                    binding.collection,
                    provider.semantic,
                )

    def guard(self, reference: str) -> str | None:
        if reference not in self.handlers:
            return None
        self.verify()
        operation = reference.split(":", 1)[0].removeprefix("memory.")
        return (
            "memory.add"
            if operation == "put"
            else "memory.delete"
            if operation in {"delete", "sweep"}
            else "memory.search"
        )

    async def open(self) -> None:
        protect_native_storage_logging()
        if not self.services.config.execution_authorization.enabled:
            raise ConfigurationError(
                "native memory bindings require original execution authorization"
            )
        if (
            self.services.memory_provider_database_path("langgraph", create_namespace=False)
            == ":memory:"
        ):
            raise ConfigurationError("native memory bindings require durable storage")
        if any(self.services.catalog.has(reference) for reference in references(self.bindings)):
            raise ConfigurationError("native memory function reference is already installed")
        try:
            shared_embeddings = None
            for binding in self.bindings:
                embeddings = None
                if binding.semantic is not None:
                    if shared_embeddings is None:
                        shared_embeddings = await NativeMemoryEmbeddings.open(
                            binding.semantic.policy()
                        )
                    embeddings = shared_embeddings.rebind(binding.semantic.policy())
                provider = await self.stack.enter_async_context(
                    open_native_memory_provider(
                        self.services,
                        provider_id=binding.id,
                        version=binding.version,
                        namespace_limits=MemoryNamespaceBounds(
                            "native-" + binding.id,
                            "1",
                            binding.max_records,
                            binding.max_value_bytes,
                            binding.cleanup_limit,
                        ),
                        retention=MemoryRecordRetention(
                            "native-" + binding.id, "1", binding.retention_seconds
                        )
                        if binding.retention_seconds is not None
                        else None,
                        semantic=embeddings.policy if embeddings is not None else None,
                        embeddings=embeddings,
                        collection=binding.collection,
                    )
                )
                self.providers[binding.id] = provider
                if provider.semantic is not None:
                    self.services.native_memory_semantic_profiles[binding.id] = provider.semantic
            for binding in self.bindings:
                operations = (
                    [*OPERATIONS, "semantic-search"] if binding.semantic is not None else OPERATIONS
                )
                for operation in operations:
                    name = (
                        f"memory.search:1.1.0@{binding.id}"
                        if operation == "semantic-search"
                        else f"memory.{operation}:1.0.0@{binding.id}"
                    )
                    function = self.handler(binding, operation)
                    self.handlers[name] = function
                    self.services.catalog.register(name, function)
            self.services.catalog.role_function_calls = self.services.workflow_function_calls
            self.opened = True
            self.services.native_memory_call_guard = self.guard
        except BaseException:
            for binding in self.bindings:
                self.services.native_memory_semantic_profiles.pop(binding.id, None)
            try:
                await self.stack.aclose()
            except Exception:
                pass
            raise

    async def close(self) -> None:
        if self.services.native_memory_call_guard == self.guard:
            self.services.native_memory_call_guard = None
        self.opened = False
        for binding in self.bindings:
            self.services.native_memory_semantic_profiles.pop(binding.id, None)
        try:
            await self.stack.aclose()
        finally:
            for name, function in self.handlers.items():
                self.services.catalog.unregister(name, function)

    def handler(self, binding: NativeMemoryBindingConfig, operation: str) -> CatalogFunction:
        provider = self.providers[binding.id]
        reader = provider.reader(
            binding.collection,
            bounds=NativeMemoryReadBounds(binding.max_record_bytes, binding.read_timeout_seconds),
        )
        writer = provider.writer(binding.collection, max_record_bytes=binding.max_record_bytes)

        async def call(payload: Any, _context: CatalogContext) -> Any:
            self.verify()
            try:
                validate_arguments(operation, payload)
                if operation == "get":
                    return {"value": await reader.get(payload["key"])}
                if operation in {"search", "semantic-search"}:
                    if "filter" in payload and type(payload["filter"]) is not dict:
                        raise ValueError("search filter must be an object")
                    query = MemorySearchQuery.create(
                        payload.get("filter"),
                        payload.get("limit", binding.max_search_results),
                        text=payload["query"] if operation == "semantic-search" else None,
                        index=provider.semantic if operation == "semantic-search" else None,
                    )
                    if query.limit > binding.max_search_results:
                        raise ValueError("search limit exceeds installed binding")
                    return await reader.search(query)
                if operation == "put":
                    await writer.put(payload["key"], payload["value"])
                elif operation == "delete":
                    await writer.delete(payload["key"])
                else:
                    await writer.sweep_expired()
                return {"ok": True}
            except ValueError:
                raise ToolError(
                    "native memory arguments are outside the installed profile"
                ) from None

        return call
