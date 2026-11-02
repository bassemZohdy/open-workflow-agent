"""Map an SDK stream namespace to one existing private child checkpoint thread."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from open_workflow_agent.errors import InvocationStateError


class ScopedSubgraphSaver(BaseCheckpointSaver[Any]):  # type: ignore[misc]
    """Keep child storage identity stable while the SDK carries a logical namespace."""

    def __init__(
        self, inner: Any, thread_id: str, logical_namespace: str, *, allow_occurrence: bool = False,
        max_occurrence: int = 31,
    ) -> None:
        super().__init__(serde=inner.serde)
        if (
            not isinstance(thread_id, str) or not thread_id
            or not isinstance(logical_namespace, str) or not logical_namespace
            or len(logical_namespace.encode("utf-8")) > 4096
            or type(allow_occurrence) is not bool
            or type(max_occurrence) is not int or not 1 <= max_occurrence <= 31
            or (allow_occurrence and len(logical_namespace.encode("utf-8")) > 4093)
        ):
            raise InvocationStateError("native subgraph checkpoint binding is invalid")
        self._inner = inner
        self._thread_id = thread_id
        self._logical_namespace = logical_namespace
        self._allow_occurrence = allow_occurrence
        self._max_occurrence = max_occurrence

    def _namespace(self, config: Any) -> str:
        if not isinstance(config, dict) or not isinstance(config.get("configurable"), dict):
            raise InvocationStateError("native subgraph checkpoint identity is invalid")
        value = config["configurable"]
        namespace = value.get("checkpoint_ns")
        if value.get("thread_id") != self._thread_id or not isinstance(namespace, str):
            raise InvocationStateError("native subgraph checkpoint identity is inconsistent")
        if namespace == self._logical_namespace:
            return namespace
        prefix = self._logical_namespace + "|"
        if self._allow_occurrence and namespace.startswith(prefix):
            suffix = namespace[len(prefix):]
            if 1 <= len(suffix) <= 2 and suffix.isascii() and suffix.isdigit():
                if str(int(suffix)) != suffix:
                    raise InvocationStateError(
                        "native subgraph checkpoint identity is inconsistent"
                    )
                if 1 <= int(suffix) <= self._max_occurrence:
                    return namespace
        raise InvocationStateError("native subgraph checkpoint identity is inconsistent")

    def _config(
        self, config: Any, *, physical: bool, namespace: str | None = None
    ) -> dict[str, Any]:
        if physical:
            self._namespace(config)
            target = ""
        else:
            if not isinstance(config, dict) or not isinstance(config.get("configurable"), dict):
                raise InvocationStateError("native subgraph checkpoint identity is invalid")
            value = config["configurable"]
            if value.get("thread_id") != self._thread_id or value.get("checkpoint_ns") != "":
                raise InvocationStateError("native subgraph checkpoint identity is inconsistent")
            target = namespace if namespace is not None else self._logical_namespace
            self._namespace({
                "configurable": {"thread_id": self._thread_id, "checkpoint_ns": target}
            })
        value = config["configurable"]
        return {**config, "configurable": {**value, "checkpoint_ns": target}}

    def _tuple(self, value: Any, namespace: str) -> Any:
        if value is None:
            return None
        return value._replace(
            config=self._config(value.config, physical=False, namespace=namespace),
            parent_config=self._config(value.parent_config, physical=False, namespace=namespace)
            if value.parent_config is not None else None,
        )

    async def aget_tuple(self, config: Any) -> Any:
        namespace = self._namespace(config)
        value = await self._inner.aget_tuple(self._config(config, physical=True))
        return self._tuple(value, namespace)

    async def alist(
        self, config: Any, *, filter: Any = None, before: Any = None, limit: Any = None
    ) -> AsyncIterator[Any]:
        namespace = self._namespace(config)
        physical = self._config(config, physical=True)
        if before is not None and self._namespace(before) != namespace:
            raise InvocationStateError("native subgraph checkpoint identity is inconsistent")
        prior = self._config(before, physical=True) if before is not None else None
        async for value in self._inner.alist(physical, filter=filter, before=prior, limit=limit):
            yield self._tuple(value, namespace)

    async def aput(self, config: Any, checkpoint: Any, metadata: Any, new_versions: Any) -> Any:
        namespace = self._namespace(config)
        value = await self._inner.aput(
            self._config(config, physical=True), checkpoint, metadata, new_versions
        )
        return self._config(value, physical=False, namespace=namespace)

    async def aput_writes(
        self, config: Any, writes: Any, task_id: str, task_path: str = ""
    ) -> None:
        await self._inner.aput_writes(
            self._config(config, physical=True), writes, task_id, task_path
        )

    def get_next_version(self, current: Any, channel: Any) -> Any:
        return self._inner.get_next_version(current, channel)
