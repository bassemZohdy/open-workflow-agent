"""LangGraph agent factory boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from open_workflow_agent.config import AgentConfig, ModelConfig


@dataclass(frozen=True, slots=True)
class LangGraphAgentSpec:
    name: str
    instruction: str
    model: ModelConfig
    tools: tuple[Any, ...] = ()


class LangGraphAgentFactory:
    def create(
        self, agent: AgentConfig, model: ModelConfig, tools: list[Any] | None = None
    ) -> LangGraphAgentSpec:
        return LangGraphAgentSpec(agent.name, agent.instruction, model, tuple(tools or ()))
