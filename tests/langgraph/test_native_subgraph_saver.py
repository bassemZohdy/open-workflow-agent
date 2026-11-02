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


@pytest.mark.parametrize("thread,namespace", [("", "node"), ("thread", ""), ("thread", "x" * 4097)])
def test_subgraph_saver_rejects_invalid_binding(thread, namespace):
    with pytest.raises(InvocationStateError):
        ScopedSubgraphSaver(BaseCheckpointSaver(), thread, namespace)
