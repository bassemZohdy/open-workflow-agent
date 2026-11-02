"""Characterize native subgraph streaming against independent checkpoint identities.

This is an SDK feasibility test, not an implemented or advertised OWA profile.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any, TypedDict
from uuid import uuid4

import pytest

pytest.importorskip("langgraph.graph")
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.config import get_config
from langgraph.graph import END, START, StateGraph


class _State(TypedDict):
    value: int


def _namespace(config: dict[str, Any], namespace: str) -> dict[str, Any]:
    return {**config, "configurable": {**config["configurable"], "checkpoint_ns": namespace}}


class _PrototypeSaver(BaseCheckpointSaver):
    """Test-only translation; production recovery and interruption are not qualified."""

    def __init__(self, inner: Any) -> None:
        super().__init__(serde=inner.serde)
        self.inner = inner

    async def aget_tuple(self, config: Any) -> Any:
        result = await self.inner.aget_tuple(_namespace(config, ""))
        if result is None:
            return None
        logical = config["configurable"]["checkpoint_ns"]
        return result._replace(
            config=_namespace(result.config, logical),
            parent_config=_namespace(result.parent_config, logical)
            if result.parent_config
            else None,
        )

    async def aput(self, config: Any, checkpoint: Any, metadata: Any, new_versions: Any) -> Any:
        result = await self.inner.aput(_namespace(config, ""), checkpoint, metadata, new_versions)
        return _namespace(result, config["configurable"]["checkpoint_ns"])

    async def aput_writes(
        self, config: Any, writes: Any, task_id: str, task_path: str = ""
    ) -> None:
        await self.inner.aput_writes(_namespace(config, ""), writes, task_id, task_path)

    def get_next_version(self, current: Any, channel: Any) -> Any:
        return self.inner.get_next_version(current, channel)


@asynccontextmanager
async def _saver(backend: str, tmp_path: Any) -> Any:
    if backend == "postgresql":
        datasource = os.getenv("OWA_TEST_POSTGRES_URL")
        if not datasource:
            pytest.skip("PostgreSQL datasource required")
        saver_class = pytest.importorskip("langgraph.checkpoint.postgres.aio").AsyncPostgresSaver
        context = saver_class.from_conn_string(datasource)
    else:
        saver_class = pytest.importorskip("langgraph.checkpoint.sqlite.aio").AsyncSqliteSaver
        context = saver_class.from_conn_string(str(tmp_path / "sdk-subgraph.sqlite"))
    async with context as saver:
        await saver.setup()
        yield saver


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("mode", ["independent", "inherited", "prototype"])
async def test_sdk_nested_streaming_and_checkpoint_namespace_are_independent(
    tmp_path: Any, backend: str, mode: str
) -> None:
    suffix = uuid4().hex
    root_thread, child_thread = "root-" + suffix, "child-" + suffix
    async with _saver(backend, tmp_path) as saver:
        prototype = _PrototypeSaver(saver)
        child_builder = StateGraph(_State)
        child_builder.add_node("inner", lambda state: {"value": state["value"] + 1})
        child_builder.add_edge(START, "inner")
        child_builder.add_edge("inner", END)
        child = child_builder.compile(checkpointer=prototype if mode == "prototype" else saver)

        async def outer(state: _State) -> Any:
            if mode == "independent":
                config = {"configurable": {"thread_id": child_thread, "checkpoint_ns": ""}}
            else:
                parent = get_config()
                config = {**parent, "configurable": dict(parent["configurable"])}
                config["configurable"]["thread_id"] = child_thread
                config["configurable"].pop("checkpoint_id", None)
                if mode == "prototype":
                    config["configurable"]["__pregel_checkpointer"] = prototype
            result = None
            async for stream_mode, payload in child.astream(
                state, config, stream_mode=["values", "updates"], durability="sync"
            ):
                if stream_mode == "values":
                    result = payload
            return result

        root_builder = StateGraph(_State)
        root_builder.add_node("outer", outer)
        root_builder.add_edge(START, "outer")
        root_builder.add_edge("outer", END)
        root = root_builder.compile(checkpointer=saver)
        events = [
            event
            async for event in root.astream(
                {"value": 1},
                {"configurable": {"thread_id": root_thread}},
                stream_mode=["values", "updates"],
                subgraphs=True,
                durability="sync",
            )
        ]
        nested = [namespace for namespace, _, _ in events if namespace]
        assert events[-1][2] == {"value": 2}
        root_state = await saver.aget_tuple(
            {"configurable": {"thread_id": root_thread, "checkpoint_ns": ""}}
        )
        assert root_state is not None and root_state.checkpoint["channel_values"]["value"] == 2
        child_config = {"configurable": {"thread_id": child_thread, "checkpoint_ns": ""}}
        retained = await saver.aget_tuple(child_config)
        if mode == "independent":
            assert not nested and retained is not None
            durable_config = child_config
        elif mode == "inherited":
            assert nested and retained is None
            namespaced = {
                "configurable": {"thread_id": child_thread, "checkpoint_ns": nested[0][0]}
            }
            assert await saver.aget_tuple(namespaced) is not None
            durable_config = namespaced
        else:
            assert nested and retained is not None
            assert retained.checkpoint["channel_values"]["value"] == 2
            assert retained.config["configurable"]["checkpoint_ns"] == ""
            logical_config = _namespace(retained.config, nested[0][0])
            marker = str(uuid4())
            await prototype.aput_writes(logical_config, [("probe", {"value": 3})], marker)
            logical = await prototype.aget_tuple(logical_config)
            assert logical is not None
            assert logical.config["configurable"]["checkpoint_ns"] == nested[0][0]
            assert (marker, "probe", {"value": 3}) in logical.pending_writes
            durable_config = child_config
    async with _saver(backend, tmp_path) as reopened:
        persisted = await reopened.aget_tuple(durable_config)
        assert persisted is not None
        assert persisted.checkpoint["channel_values"]["value"] == 2
        if mode == "prototype":
            assert (marker, "probe", {"value": 3}) in persisted.pending_writes
