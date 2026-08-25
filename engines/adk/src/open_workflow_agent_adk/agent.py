"""ADK agent factory boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from open_workflow_agent.config import AgentConfig, ModelConfig
from open_workflow_agent.tools import AgentToolBinding


@dataclass(frozen=True, slots=True)
class AdkAgentSpec:
    name: str
    instruction: str
    model: ModelConfig
    tools: tuple[Any, ...] = ()


class AdkAgentFactory:
    def create(
        self, agent: AgentConfig, model: ModelConfig, tools: list[Any] | None = None
    ) -> AdkAgentSpec:
        return AdkAgentSpec(agent.name, agent.instruction, model, tuple(tools or ()))

    def bind_tools(
        self, bindings: list[AgentToolBinding] | tuple[AgentToolBinding, ...]
    ) -> list[Any]:
        try:
            from google.adk.tools import FunctionTool
        except ImportError:
            return list(bindings)
        bound: list[Any] = []
        for binding in bindings:

            async def invoke(payload: dict[str, Any], item=binding) -> Any:
                return await item.invoke(payload)

            invoke.__name__ = binding.name
            invoke.__doc__ = binding.description
            bound.append(FunctionTool(invoke))
        return bound
