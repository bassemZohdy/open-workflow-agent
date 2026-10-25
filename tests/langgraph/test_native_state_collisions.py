"""Verify pinned SDK primitives without advertising public native extensions."""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

import pytest
from open_workflow_agent_langgraph.native import LANGGRAPH_AVAILABLE

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not LANGGRAPH_AVAILABLE, reason="native LangGraph dependency is absent"),
]


class LastValueState(TypedDict):
    value: int


class AppendState(TypedDict):
    values: Annotated[list[int], operator.add]


async def test_native_parallel_last_value_collision_is_rejected():
    from langgraph.errors import InvalidUpdateError
    from langgraph.graph import END, START, StateGraph

    graph = StateGraph(LastValueState)
    graph.add_node("first", lambda state: {"value": 1})
    graph.add_node("second", lambda state: {"value": 2})
    for branch in ("first", "second"):
        graph.add_edge(START, branch)
        graph.add_edge(branch, END)
    with pytest.raises(InvalidUpdateError, match="Can receive only one value per step"):
        await graph.compile().ainvoke({"value": 0})


async def test_native_explicit_append_reducer_preserves_parallel_values():
    from langgraph.graph import END, START, StateGraph

    graph = StateGraph(AppendState)
    graph.add_node("first", lambda state: {"values": [1]})
    graph.add_node("second", lambda state: {"values": [2]})
    for branch in ("first", "second"):
        graph.add_edge(START, branch)
        graph.add_edge(branch, END)
    result = await graph.compile().ainvoke({"values": [0]})
    assert result == {"values": [0, 1, 2]}
