"""Map an SDK stream namespace to one existing private child checkpoint thread."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from open_workflow_agent.errors import InvocationStateError


class ScopedSubgraphSaver(BaseCheckpointSaver[Any]):  # type: ignore[misc]
    """Keep child storage identity stable while the SDK carries a logical namespace."""

    def __init__(self, inner: Any, thread_id: str, logical_namespace: str) -> None:
        super().__init__(serde=inner.serde)
        if (
            not isinstance(thread_id, str) or not thread_id
            or not isinstance(logical_namespace, str) or not logical_namespace
            or len(logical_namespace.encode("utf-8")) > 4096
        ):
            raise InvocationStateError("native subgraph checkpoint binding is invalid")
        self._inner = inner
        self._thread_id = thread_id
        self._logical_namespace = logical_namespace

    def _config(self, config: Any, *, physical: bool) -> dict[str, Any]:
        if not isinstance(config, dict) or not isinstance(config.get("configurable"), dict):
            raise InvocationStateError("native subgraph checkpoint identity is invalid")
        value = config["configurable"]
        expected = self._logical_namespace if physical else ""
        if value.get("thread_id") != self._thread_id or value.get("checkpoint_ns") != expected:
            raise InvocationStateError("native subgraph checkpoint identity is inconsistent")
        target = "" if physical else self._logical_namespace
        return {**config, "configurable": {**value, "checkpoint_ns": target}}

    def _tuple(self, value: Any) -> Any:
        if value is None:
            return None
        return value._replace(
            config=self._config(value.config, physical=False),
            parent_config=self._config(value.parent_config, physical=False)
            if value.parent_config is not None else None,
        )

    async def aget_tuple(self, config: Any) -> Any:
        return self._tuple(await self._inner.aget_tuple(self._config(config, physical=True)))

    async def alist(
        self, config: Any, *, filter: Any = None, before: Any = None, limit: Any = None
    ) -> AsyncIterator[Any]:
        physical = self._config(config, physical=True)
        prior = self._config(before, physical=True) if before is not None else None
        async for value in self._inner.alist(physical, filter=filter, before=prior, limit=limit):
            yield self._tuple(value)

    async def aput(self, config: Any, checkpoint: Any, metadata: Any, new_versions: Any) -> Any:
        value = await self._inner.aput(
            self._config(config, physical=True), checkpoint, metadata, new_versions
        )
        return self._config(value, physical=False)

    async def aput_writes(
        self, config: Any, writes: Any, task_id: str, task_path: str = ""
    ) -> None:
        await self._inner.aput_writes(
            self._config(config, physical=True), writes, task_id, task_path
        )

    def get_next_version(self, current: Any, channel: Any) -> Any:
        return self._inner.get_next_version(current, channel)
