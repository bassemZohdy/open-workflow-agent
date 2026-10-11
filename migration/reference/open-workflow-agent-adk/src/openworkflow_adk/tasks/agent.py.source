"""Builders for ADK agent tasks."""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from google.adk.agents import LlmAgent

from openworkflow_adk.config import resolve_agent_characteristics, resolve_provider_config
from openworkflow_adk.models import AgentCharacteristics, ProviderConfig, Task
from openworkflow_adk.resources.providers import create_llm
from openworkflow_adk.suspension import WorkflowSuspended


def _agent_builder(
    name: str,
    task: Task,
    agent_config: AgentCharacteristics,
    model_factory: Callable[[str], Any] | None = None,
    model_specs: dict[str, Any] | None = None,
    environ: dict[str, str] | None = None,
    tool_registry: dict[str, Callable[..., Any]] | None = None,
    provider_configs: dict[str, ProviderConfig] | None = None,
    provider_factory: Callable[[str, ProviderConfig], Any] | None = None,
    resume_input: Any = None,
    route_options: set[str] | None = None,
    agent_defaults: Any = None,
) -> LlmAgent:
    config = resolve_agent_characteristics(
        agent_config,
        defaults=agent_defaults,
        models=model_specs,
        environ=environ,
        providers=provider_configs,
    )
    return _build_agent(
        name,
        config,
        model_factory=model_factory,
        model_specs=model_specs,
        environ=environ,
        tool_registry=tool_registry,
        provider_configs=provider_configs,
        provider_factory=provider_factory,
        as_sub_agent=False,
        resume_input=resume_input,
        route_options=route_options,
    )


_MAX_SUB_AGENT_DEPTH = int(os.environ.get("WORKFLOW_MAX_SUB_AGENT_DEPTH", "10"))


def _build_agent(
    name: str,
    config: Any,
    *,
    model_factory: Callable[[str], Any] | None,
    model_specs: dict[str, Any] | None,
    environ: dict[str, str] | None,
    tool_registry: dict[str, Callable[..., Any]] | None,
    provider_configs: dict[str, ProviderConfig] | None,
    provider_factory: Callable[[str, ProviderConfig], Any] | None,
    as_sub_agent: bool,
    resume_input: Any = None,
    route_options: set[str] | None = None,
    agent_defaults: Any = None,
    depth: int = 0,
) -> LlmAgent:
    """Build one ADK agent and recursively assemble its coordinator tree."""
    if depth > _MAX_SUB_AGENT_DEPTH:
        raise ValueError(
            f"agent sub-agent recursion exceeded maximum depth ({_MAX_SUB_AGENT_DEPTH})"
        )
    model = model_factory(config.model or "") if model_factory else config.model or ""
    if config.provider:
        provider = resolve_provider_config(
            config.provider.model_dump(), providers=provider_configs, environ=environ
        )
        model = (
            provider_factory(config.model or "", provider)
            if provider_factory
            else create_llm(config.model or "", provider)
        )
    tools = [
        (tool_registry or {}).get(tool, tool) if isinstance(tool, str) else tool
        for tool in config.tools
    ]
    if config.request_input is not None:
        question = str(config.request_input.get("question", "Input required to continue."))

        async def request_input(_question: str = question) -> Any:
            """Pause this agent until an external user supplies the requested input."""
            if resume_input is None:
                raise WorkflowSuspended(
                    task=name,
                    resume_at=datetime.now(timezone.utc),
                    reason="human_input",
                )
            return resume_input

        request_input.__name__ = "request_input"
        request_input.__doc__ = question
        tools.append(request_input)
    if route_options:

        async def route_to(route: str, tool_context: Any) -> str:
            """Select one of the workflow switch routes."""
            if route not in route_options:
                raise ValueError(f"unknown workflow route {route!r}")
            tool_context.state["workflow:route"] = route
            return f"selected route {route}"

        route_to.__name__ = "route_to"
        tools.append(route_to)
    if config.memory:
        from google.adk.tools import load_memory

        if load_memory not in tools:
            tools.append(load_memory)
    sub_agents = []
    for index, child in enumerate(config.sub_agents):
        resolved_child = resolve_agent_characteristics(
            child,
            defaults=agent_defaults,
            environ=environ,
            models=model_specs,
            providers=provider_configs,
        )
        sub_agents.append(
            _build_agent(
                resolved_child.name or f"{name}_sub_{index}",
                resolved_child,
                model_factory=model_factory,
                model_specs=model_specs,
                environ=environ,
                tool_registry=tool_registry,
                provider_configs=provider_configs,
                provider_factory=provider_factory,
                as_sub_agent=True,
                resume_input=resume_input,
                route_options=None,
                agent_defaults=agent_defaults,
                depth=depth + 1,
            )
        )
    return LlmAgent(
        name=name,
        description=config.description or "",
        model=model,
        instruction=config.instruction or "",
        tools=tools,
        sub_agents=sub_agents,
        generate_content_config=config.generate_content_config,
        mode="chat" if sub_agents or as_sub_agent else "single_turn",
        output_key=(config.output_key or name) if not as_sub_agent else config.output_key,
    )
