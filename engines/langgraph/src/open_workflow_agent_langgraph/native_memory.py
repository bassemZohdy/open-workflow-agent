"""Private read-only native store component; no workflow capability is enabled.

Common services verify original authority and decide read access. This adapter
only binds that principal namespace to the pinned SDK and projects finite JSON.
An installed binding adds common durable read observations. Public workflow
admission and native write fencing remain separate work.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from langgraph.store.base import BaseStore, Item
from open_workflow_agent.errors import ConfigurationError, ModelError, ToolError
from open_workflow_agent.execution_context import current_action_context
from open_workflow_agent.memory_reads import MemoryReadBinding, MemoryReadObservations
from open_workflow_agent.memory_search import MemorySearchQuery
from open_workflow_agent.model_responses import bounded_model_json

if TYPE_CHECKING:
    from open_workflow_agent.services import RuntimeServices

_COLLECTION = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_PRINCIPAL_NAMESPACE = re.compile(r"owa/principal-memory/v1:([a-f0-9]{64})\Z")


@dataclass(frozen=True, slots=True)
class NativeMemoryReadBounds:
    max_record_bytes: int = 65_536
    timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        if type(self.max_record_bytes) is not int or not 1 <= self.max_record_bytes <= 65_536:
            raise ValueError("native memory record bound must be between 1 and 65536 bytes")
        if type(self.timeout_seconds) not in {int, float} or not 0 < self.timeout_seconds <= 30:
            raise ValueError("native memory read deadline must be finite and at most 30 seconds")


def _public_value(
    item: Any, namespace: tuple[str, ...], key: str, bounds: NativeMemoryReadBounds
) -> dict[str, Any] | None:
    if item is None:
        return None
    if (
        not isinstance(item, Item)
        or type(item.namespace) is not tuple
        or item.namespace != namespace
        or type(item.key) is not str
        or item.key != key
        or type(item.value) is not dict
    ):
        raise ToolError("native memory record is invalid", details={"reason": "invalid_record"})
    try:
        encoded = bounded_model_json(item.value, limit=bounds.max_record_bytes)
    except ModelError:
        raise ToolError(
            "native memory record is outside its finite JSON profile",
            details={"reason": "invalid_record"},
        ) from None
    return cast(dict[str, Any], json.loads(encoded))


class NativeMemoryReader:
    """Read one principal-owned record without exposing an SDK Item or namespace.

    The store and collection are trusted deployment-side constructor inputs;
    get() accepts only a record key. No provider is constructed or registered,
    and no native memory capability is advertised by this component.
    """

    def __init__(
        self,
        services: RuntimeServices,
        store: BaseStore,
        *,
        collection: str,
        bounds: NativeMemoryReadBounds | None = None,
        binding: MemoryReadBinding | None = None,
    ) -> None:
        if type(collection) is not str or not _COLLECTION.fullmatch(collection):
            raise ValueError("native memory collection must be a bounded identifier")
        if not isinstance(store, BaseStore):
            raise ValueError("native memory requires an installed SDK store")
        if bounds is not None and not isinstance(bounds, NativeMemoryReadBounds):
            raise ValueError("native memory requires typed read bounds")
        if binding is not None and not isinstance(binding, MemoryReadBinding):
            raise ValueError("native memory requires a typed installed binding")
        self._services = services
        self._store = store
        self._collection = collection
        self._bounds = bounds or NativeMemoryReadBounds()
        self._binding = binding
        self._observations = (
            MemoryReadObservations(services.effect_journal, services.invocations)
            if binding is not None
            else None
        )

    async def _fetch(self, namespace: tuple[str, ...], key: str) -> dict[str, Any] | None:
        try:
            item = await self._store.aget(namespace, key, refresh_ttl=False)
        except TimeoutError:
            raise
        except Exception:
            raise ToolError(
                "native memory store read failed", details={"reason": "store_failure"}
            ) from None
        return _public_value(item, namespace, key, self._bounds)

    async def get(self, key: str) -> dict[str, Any] | None:
        if type(key) is not str or not _KEY.fullmatch(key):
            raise ToolError("native memory key is invalid", details={"reason": "invalid_key"})
        try:
            async with asyncio.timeout(self._bounds.timeout_seconds):
                if not self._services.memory_enabled:
                    raise ConfigurationError("native memory is disabled by deployment")
                context = current_action_context()
                if context is None:
                    raise ConfigurationError("native memory requires original invocation authority")
                binding = self._services._memory_namespace()
                match = _PRINCIPAL_NAMESPACE.fullmatch(binding or "")
                if match is None:
                    raise ConfigurationError("native memory requires original invocation authority")
                await self._services.authorize_current_action("memory.search", "memory:self")
                namespace = ("owa_native_memory_v1", match[1], self._collection)
                if self._observations is not None and self._binding is not None:
                    return await self._observations.read(
                        context.handle,
                        context.identity,
                        binding=self._binding,
                        namespace=match[0],
                        collection=self._collection,
                        key=key,
                        max_record_bytes=self._bounds.max_record_bytes,
                        fetch=lambda: self._fetch(namespace, key),
                        observer=context.observer,
                    )
                return await self._fetch(namespace, key)
        except TimeoutError:
            raise ToolError(
                "native memory read exceeded its deadline", details={"reason": "timeout"}
            ) from None

    async def search(self, query: MemorySearchQuery) -> dict[str, Any]:
        if (
            not isinstance(query, MemorySearchQuery)
            or self._binding is None
            or self._observations is None
        ):
            raise ConfigurationError("native memory search requires an installed finite binding")
        try:
            async with asyncio.timeout(self._bounds.timeout_seconds):
                if not self._services.memory_enabled:
                    raise ConfigurationError("native memory is disabled by deployment")
                context = current_action_context()
                namespace = self._services._memory_namespace()
                match = _PRINCIPAL_NAMESPACE.fullmatch(namespace or "")
                if context is None or match is None:
                    raise ConfigurationError("native memory requires original invocation authority")
                await self._services.authorize_current_action("memory.search", "memory:self")
                native_namespace = ("owa_native_memory_v1", match[1], self._collection)

                async def fetch() -> dict[str, Any]:
                    try:
                        items = await self._store.asearch(
                            native_namespace,
                            filter=query.filter,
                            limit=query.limit,
                            refresh_ttl=False,
                        )
                    except TimeoutError:
                        raise
                    except Exception:
                        raise ToolError(
                            "native memory search failed", details={"reason": "store_failure"}
                        ) from None
                    if type(items) is not list or len(items) > query.limit:
                        raise ToolError("native memory search result is invalid")
                    keys = [item.key for item in items if isinstance(item, Item)]
                    if (
                        len(keys) != len(items)
                        or len(set(keys)) != len(keys)
                        or any(type(key) is not str or not _KEY.fullmatch(key) for key in keys)
                    ):
                        raise ToolError("native memory search result is invalid")
                    return {
                        "items": [
                            {
                                "key": item.key,
                                "value": _public_value(
                                    item, native_namespace, item.key, self._bounds
                                ),
                            }
                            for item in sorted(items, key=lambda item: item.key)
                        ]
                    }

                result = await self._observations.read(
                    context.handle,
                    context.identity,
                    binding=self._binding,
                    namespace=match[0],
                    collection=self._collection,
                    key=query.key,
                    max_record_bytes=self._bounds.max_record_bytes,
                    fetch=fetch,
                    observer=context.observer,
                    query=query,
                )
                if result is None:
                    raise ToolError("native memory search observation is invalid")
                return result
        except TimeoutError:
            raise ToolError(
                "native memory search exceeded its deadline", details={"reason": "timeout"}
            ) from None
