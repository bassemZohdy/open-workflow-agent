"""Externally configured agent tools and explicit workflow-call adapters."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .config import ToolConfig
from .errors import ToolError
from .protocols import ProtocolServices


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    type: str
    endpoint: str | None
    options: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AgentToolBinding:
    name: str
    description: str
    invoke: Callable[[Any], Awaitable[Any]]


class ToolRegistry:
    def __init__(
        self, tools: list[ToolDefinition] | None = None, protocols: ProtocolServices | None = None
    ) -> None:
        self.tools = {tool.name: tool for tool in tools or []}
        self.protocols = protocols or ProtocolServices()

    @classmethod
    def from_config(cls, config: list[ToolConfig], protocols: ProtocolServices) -> ToolRegistry:
        tools = [
            ToolDefinition(
                name=item.name or f"{item.type}-{index}",
                type=item.type,
                endpoint=item.endpoint,
                options=item.options,
            )
            for index, item in enumerate(config)
        ]
        return cls(tools, protocols)

    def names(self) -> tuple[str, ...]:
        return tuple(self.tools)

    def bindings(self) -> tuple[AgentToolBinding, ...]:
        bindings: list[AgentToolBinding] = []
        for tool in self.tools.values():

            async def invoke(payload: Any, name: str = tool.name) -> Any:
                return await self.invoke(name, payload)

            bindings.append(
                AgentToolBinding(
                    name=tool.name,
                    description=f"Configured {tool.type} tool {tool.name}",
                    invoke=invoke,
                )
            )
        return tuple(bindings)

    async def invoke(self, name: str, payload: Any) -> Any:
        tool = self.tools.get(name)
        if tool is None:
            raise ToolError(f"configured tool not found: {name}")
        if isinstance(payload, dict) and tool.options:
            payload = {**tool.options, **payload}
        if tool.endpoint and isinstance(payload, dict):
            payload = {**payload, "endpoint": tool.endpoint}
        return await self.protocols.call(tool.type, payload)
