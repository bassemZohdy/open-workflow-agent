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
        if callable(self.response):
            return self.response(prompt)
        if self.response is not None:
            return self.response
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
class CatalogContext:
    model: Model
    agent_instruction: str = ""
    services: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)


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
            return await context.model.complete(prompt)

        async def llm(payload: Any, context: CatalogContext) -> Any:
            return await context.model.complete(payload)

        catalog.register("agent:1.0.0@default", agent)
        catalog.register("llm:1.0.0@default", llm)
        return catalog
