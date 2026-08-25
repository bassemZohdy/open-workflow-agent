"""ADK agent factory boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from open_workflow_agent.config import AgentConfig, ModelConfig


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
