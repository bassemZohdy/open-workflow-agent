"""The stream namespace adapter cannot redirect private checkpoint operations."""

import pytest
from open_workflow_agent.errors import InvocationStateError

pytest.importorskip("langgraph.graph")
from langgraph.checkpoint.base import BaseCheckpointSaver
from open_workflow_agent_langgraph.native_subgraph_saver import ScopedSubgraphSaver


@pytest.mark.parametrize(
    ("thread", "namespace"),
    [("other-thread", "node:123"), ("child-thread", ""), ("child-thread", "other:456")],
)
@pytest.mark.asyncio
async def test_subgraph_saver_rejects_cross_binding_before_storage_access(thread, namespace):
    saver = ScopedSubgraphSaver(BaseCheckpointSaver(), "child-thread", "node:123")
    config = {"configurable": {"thread_id": thread, "checkpoint_ns": namespace}}
    with pytest.raises(InvocationStateError):
        await saver.aget_tuple(config)
    with pytest.raises(InvocationStateError):
        await saver.aput_writes(config, [], "task")


@pytest.mark.asyncio
async def test_direct_scope_rejects_a_loop_occurrence_namespace():
    saver = ScopedSubgraphSaver(BaseCheckpointSaver(), "child-thread", "node:123")
    config = {"configurable": {"thread_id": "child-thread", "checkpoint_ns": "node:123|1"}}
    with pytest.raises(InvocationStateError):
        await saver.aget_tuple(config)


@pytest.mark.parametrize("thread,namespace", [("", "node"), ("thread", ""), ("thread", "x" * 4097)])
def test_subgraph_saver_rejects_invalid_binding(thread, namespace):
    with pytest.raises(InvocationStateError):
        ScopedSubgraphSaver(BaseCheckpointSaver(), thread, namespace)


@pytest.mark.parametrize("suffix", ["0", "01", "32", "-1", "1|1", "9" * 100])
def test_loop_checkpoint_binding_rejects_invalid_occurrence(suffix):
    saver = ScopedSubgraphSaver(
        BaseCheckpointSaver(), "child-thread", "node:123", allow_occurrence=True
    )
    config = {"configurable": {"thread_id": "child-thread", "checkpoint_ns": "node:123|" + suffix}}
    with pytest.raises(InvocationStateError):
        saver._config(config, physical=True)


@pytest.mark.parametrize("suffix", ["1", "31"])
def test_loop_checkpoint_binding_preserves_the_logical_occurrence(suffix):
    saver = ScopedSubgraphSaver(
        BaseCheckpointSaver(), "child-thread", "node:123", allow_occurrence=True
    )
    namespace = "node:123|" + suffix
    config = {"configurable": {"thread_id": "child-thread", "checkpoint_ns": namespace}}
    physical = saver._config(config, physical=True)
    assert physical["configurable"]["checkpoint_ns"] == ""
    assert saver._config(physical, physical=False, namespace=namespace) == config


@pytest.mark.asyncio
async def test_loop_history_rejects_a_cursor_from_another_occurrence():
    saver = ScopedSubgraphSaver(
        BaseCheckpointSaver(), "child-thread", "node:123", allow_occurrence=True
    )
    config = {"configurable": {"thread_id": "child-thread", "checkpoint_ns": "node:123|1"}}
    before = {"configurable": {"thread_id": "child-thread", "checkpoint_ns": "node:123|2"}}
    with pytest.raises(InvocationStateError):
        await anext(saver.alist(config, before=before))
