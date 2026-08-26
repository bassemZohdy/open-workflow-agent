"""Runtime catalog and deterministic model abstractions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from .errors import ModelError, ToolError


class Model(Protocol):
    async def complete(self, prompt: Any, *, options: dict[str, Any] | None = None) -> Any: ...


class FakeModel:
    """Deterministic model for tests and local development."""

    def __init__(self, response: Any = None, *, failures: int = 0) -> None:
        self.response = response
        self.failures = failures
        self.calls: list[Any] = []

    async def complete(self, prompt: Any, *, options: dict[str, Any] | None = None) -> Any:
        self.calls.append(prompt)
        if self.failures:
            self.failures -= 1
            raise ModelError("controlled fake model failure")
        response = self.response
        if isinstance(response, list):
            response = response.pop(0) if response else None
        if callable(response):
            response = response(prompt)
        if response is not None:
            if hasattr(response, "__await__"):
                return await response
            return response
        if isinstance(prompt, dict):
            return {"response": prompt.get("input", prompt.get("prompt", prompt))}
        return {"response": prompt}


class LiteLLMModel:
    """Optional provider adapter; importing LiteLLM is deferred until invocation."""

    def __init__(
        self, name: str, *, temperature: float = 0.0, options: dict[str, Any] | None = None
    ) -> None:
        self.name = name
        self.temperature = temperature
        self.options = options or {}

    async def complete(self, prompt: Any, *, options: dict[str, Any] | None = None) -> Any:
        try:
            from litellm import acompletion  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ModelError(
                "LiteLLM is not installed; install the optional model dependency"
            ) from exc
        messages = (
            prompt if isinstance(prompt, list) else [{"role": "user", "content": str(prompt)}]
        )
        response = await acompletion(
            model=self.name,
            messages=messages,
            temperature=self.temperature,
            **self.options,
            **(options or {}),
        )
        try:
            return response.choices[0].message.content
        except (AttributeError, IndexError, KeyError) as exc:
            raise ModelError("LiteLLM returned an invalid completion response") from exc


@dataclass(slots=True)
class ManagedFunctionCapabilities:
    """Narrow runtime view exposed to trusted managed catalog functions.

    This is a programming/policy boundary, not hostile-code isolation. The
    object intentionally exposes only the approved agent-tool surface and does
    not provide configuration, raw environment, filesystem, secrets, protocol
    clients, persistence stores, or subprocess APIs.
    """

    agent_tools: tuple[str, ...] = ()
    _invoke_agent_tool: Callable[[str, Any], Awaitable[Any]] | None = field(
        default=None, repr=False
    )

    async def invoke_agent_tool(self, name: str, payload: Any) -> Any:
        if name not in self.agent_tools:
            raise ToolError(f"agent tool is not approved for managed function context: {name}")
        if self._invoke_agent_tool is None:
            raise ToolError("managed function tool execution is unavailable")
        return await self._invoke_agent_tool(name, payload)


@dataclass(slots=True)
class CatalogContext:
    model: Model
    agent_instruction: str = ""
    services: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Replace broad runtime services with a capability-scoped facade."""

        services = self.services
        if services is None or isinstance(services, ManagedFunctionCapabilities):
            return
        tools = tuple(str(name) for name in getattr(services, "agent_tools", ()))
        invoker = getattr(services, "invoke_agent_tool", None)
        self.services = ManagedFunctionCapabilities(
            agent_tools=tools,
            _invoke_agent_tool=invoker if callable(invoker) else None,
        )


CatalogFunction = Callable[[Any, CatalogContext], Awaitable[Any]]


class FunctionCatalog:
    def __init__(self) -> None:
        self._functions: dict[str, CatalogFunction] = {}

    def register(self, name: str, function: CatalogFunction) -> None:
        self._functions[name] = function

    def has(self, name: str) -> bool:
        return name in self._functions

    async def call(self, name: str, payload: Any, context: CatalogContext) -> Any:
        function = self._functions.get(name)
        if function is None:
            raise ToolError(f"catalog function is not registered: {name}")
        return await function(payload, context)

    @classmethod
    def default(
        cls, model: Model, *, instruction: str = "", services: Any = None
    ) -> FunctionCatalog:
        catalog = cls()

        async def agent(payload: Any, context: CatalogContext) -> Any:
            prompt = payload if isinstance(payload, dict) else {"input": payload}
            available_tools = (
                list(getattr(context.services, "agent_tools", ()))
                if context.services is not None
                else []
            )
            prompt = {
                **prompt,
                "instruction": context.agent_instruction,
                "tools": available_tools,
            }
            messages: list[dict[str, Any]] = [
                {"role": "user", "content": prompt.get("input", prompt)}
            ]
            current: Any = prompt
            for _ in range(8):
                response = await context.model.complete(current)
                requests = _tool_requests(response)
                if not requests:
                    return response
                if context.services is None:
                    raise ToolError("agent tool execution requires runtime services")
                messages.append({"role": "assistant", "tool_calls": requests})
                results: list[dict[str, Any]] = []
                for request in requests:
                    name = str(request.get("name", ""))
                    arguments = request.get("arguments", request.get("input", {}))
                    result = await context.services.invoke_agent_tool(name, arguments)
                    results.append({"name": name, "result": result})
                    messages.append({"role": "tool", "name": name, "content": result})
                current = {**prompt, "messages": messages, "tool_results": results}
            raise ToolError("agent exceeded the maximum tool-call rounds")

        async def llm(payload: Any, context: CatalogContext) -> Any:
            return await context.model.complete(payload)

        catalog.register("agent:1.0.0@default", agent)
        catalog.register("llm:1.0.0@default", llm)
        return catalog


def _tool_requests(response: Any) -> list[dict[str, Any]]:
    if not isinstance(response, dict):
        return []
    single = response.get("tool_call")
    if isinstance(single, dict):
        return [single]
    multiple = response.get("tool_calls")
    if isinstance(multiple, list):
        return [item for item in multiple if isinstance(item, dict)]
    return []
