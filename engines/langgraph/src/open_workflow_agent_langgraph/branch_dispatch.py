"""Bounded native Send fan-out; common core retains join/end/error policy.

The dispatch graph is deliberately transient. Existing native branch scopes,
common receipts and fork lifecycle remain the only durable execution evidence.
"""

from __future__ import annotations

import asyncio
import operator
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from typing import Annotated, Any, TypedDict

from langgraph._internal._constants import CONFIG_KEY_DURABILITY
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from open_workflow_agent.errors import WorkflowExecutionError
from open_workflow_agent.native_extensions import MAX_NATIVE_SEND_BRANCHES


class _DispatchState(TypedDict):
    completed: Annotated[list[int], operator.add]


class _BranchPacket(TypedDict):
    branch_index: int


@asynccontextmanager
async def dispatch_with_native_send(
    branches: list[Coroutine[Any, Any, Any]],
) -> AsyncIterator[list[asyncio.Task[Any]]]:
    """Return actual common branch Tasks after the SDK has dispatched each one."""
    tasks: dict[int, asyncio.Task[Any]] = {}
    ready: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    driver: asyncio.Task[Any] | None = None
    try:
        if not 1 <= len(branches) <= MAX_NATIVE_SEND_BRANCHES:
            raise WorkflowExecutionError("native branch dispatch exceeds its admitted bound")

        def route(_state: _DispatchState) -> list[Send]:
            return [Send("branch", {"branch_index": index}) for index in range(len(branches))]

        async def execute(packet: _BranchPacket) -> _DispatchState:
            if (
                set(packet) != {"branch_index"}
                or type(packet["branch_index"]) is not int
                or not 0 <= packet["branch_index"] < len(branches)
                or packet["branch_index"] in tasks
            ):
                raise WorkflowExecutionError("native branch dispatch packet is invalid")
            index = packet["branch_index"]
            task = asyncio.create_task(branches[index])
            tasks[index] = task
            if len(tasks) == len(branches):
                ready.set_result(None)
            try:
                await task
            except BaseException:
                # Common join inspects the original Task result and owns error,
                # cancellation, suspension and workflow-end arbitration.
                pass
            return {"completed": [index]}

        builder = StateGraph(_DispatchState)
        builder.add_node("branch", execute, input_schema=_BranchPacket)
        builder.add_conditional_edges(START, route)
        builder.add_edge("branch", END)
        # Do not inherit a parent graph's saver for this dispatch-only wrapper.
        graph = builder.compile(checkpointer=False)
        driver = asyncio.create_task(
            graph.ainvoke(
                {"completed": []},
                config={
                    "max_concurrency": len(branches),
                    "recursion_limit": 4,
                    "configurable": {CONFIG_KEY_DURABILITY: "exit"},
                },
            )
        )
        await asyncio.wait({ready, driver}, return_when=asyncio.FIRST_COMPLETED)
        if not ready.done() or ready.cancelled():
            raise WorkflowExecutionError("native branch dispatch failed before launch completed")
        yield [tasks[index] for index in range(len(branches))]
        try:
            await driver
        except Exception:
            raise WorkflowExecutionError("native branch dispatch failed") from None
    finally:
        for task in tasks.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)
        if driver is not None:
            if not driver.done():
                driver.cancel()
            await asyncio.gather(driver, return_exceptions=True)
        ready.cancel()
        for index, branch in enumerate(branches):
            if index not in tasks:
                branch.close()
