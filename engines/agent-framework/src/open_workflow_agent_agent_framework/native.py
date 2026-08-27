"""Optional Microsoft Agent Framework workflow bridge."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

try:
    from agent_framework import Executor, WorkflowBuilder, handler

    AGENT_FRAMEWORK_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised without optional dependency
    Executor = None  # type: ignore[assignment,misc]
    WorkflowBuilder = None  # type: ignore[assignment,misc]
    handler = None  # type: ignore[assignment]
    AGENT_FRAMEWORK_AVAILABLE = False


class AgentFrameworkNativeAdapter:
    """Execute the common workflow runner through Agent Framework's workflow runtime.

    Open Workflow remains the semantic authority. Agent Framework schedules one
    bounded adapter executor, while the common executor owns task semantics,
    persistence, cancellation, tools, and lifecycle events. No native workflow
    object is exposed through the public runtime contract.
    """

    @property
    def available(self) -> bool:
        return bool(AGENT_FRAMEWORK_AVAILABLE and Executor and WorkflowBuilder and handler)

    async def invoke(
        self,
        runner: Callable[[Any], Awaitable[Any]],
        input_data: Any,
    ) -> Any:
        if not self.available:
            return await runner(input_data)

        executor_base = Executor
        workflow_builder = WorkflowBuilder
        handler_decorator = handler
        assert executor_base is not None
        assert workflow_builder is not None
        assert handler_decorator is not None

        class OpenWorkflowExecutor(executor_base):  # type: ignore[misc,valid-type]
            def __init__(self) -> None:
                super().__init__(id="open-workflow-agent-portable-executor")

            @handler_decorator
            async def process(self, value: Any, ctx: Any) -> None:
                await ctx.yield_output(await runner(value))

        executor = OpenWorkflowExecutor()
        workflow = workflow_builder(
            start_executor=executor,
            name="open-workflow-agent-portable-bridge",
        ).build()
        events = await workflow.run(input_data)
        outputs = events.get_outputs()
        if not outputs:
            raise RuntimeError("Agent Framework workflow completed without an output")
        return outputs[-1]
