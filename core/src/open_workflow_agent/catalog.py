"""Runtime catalog and deterministic model abstractions."""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol

from .credential_guard import credential_scope
from .errors import ModelError, OwaError, ToolError
from .security import SecretReference, resolve_secret


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
        self,
        name: str,
        *,
        temperature: float = 0.0,
        options: dict[str, Any] | None = None,
        credentials: Mapping[str, SecretReference] | None = None,
        authorize_credential: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self.name = name
        self.temperature = temperature
        self.options = options or {}
        self._credentials = MappingProxyType(dict(credentials or {}))
        self._authorize_credential = authorize_credential

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
        with credential_scope() as guard:
            try:
                from .config import MODEL_CREDENTIAL_OPTIONS

                if set(options or {}) & MODEL_CREDENTIAL_OPTIONS:
                    raise ModelError("model credential overrides are forbidden")
                if self._authorize_credential is not None:
                    for reference in self._credentials.values():
                        await self._authorize_credential(reference.from_env)
                selected = {
                    key: resolve_secret(reference) for key, reference in self._credentials.items()
                }
                response = await acompletion(
                    model=self.name,
                    messages=messages,
                    temperature=self.temperature,
                    **self.options,
                    **(options or {}),
                    **selected,
                )
                return guard.project(response.choices[0].message.content)
            except OwaError as error:
                guard.error(error)
                raise
            except Exception:
                raise ModelError("model completion failed") from None


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


_PUBLIC_CONTEXT_FIELDS = frozenset(
    {
        "invocation_id",
        "session_id",
        "user_id",
        "engine",
        "workflow_name",
        "workflow_version",
        "parent_invocation_id",
        "parent_task_reference",
        "task_reference",
        "task_name",
        "occurrence",
        "authority",
    }
)


def _immutable_context_value(value: Any, depth: int = 0) -> Any:
    if depth > 16:
        raise ValueError("catalog metadata exceeds its finite profile")
    if value is None or type(value) in {str, bool, int, float}:
        if isinstance(value, str) and len(value.encode("utf-8")) > 4096:
            raise ValueError("catalog metadata exceeds its finite profile")
        if type(value) is int and abs(value) > 2**53 - 1:
            raise ValueError("catalog metadata number is outside its finite profile")
        if type(value) is float and not math.isfinite(value):
            raise ValueError("catalog metadata number is outside its finite profile")
        return value
    if isinstance(value, Mapping) and all(isinstance(key, str) for key in value):
        return MappingProxyType(
            {key: _immutable_context_value(item, depth + 1) for key, item in value.items()}
        )
    if isinstance(value, (tuple, list)):
        return tuple(_immutable_context_value(item, depth + 1) for item in value)
    raise ValueError("catalog metadata is not an approved public descriptor")


@dataclass(frozen=True, slots=True, repr=False)
class _CatalogModel:
    _complete: Callable[..., Awaitable[Any]]
    _authorize: Callable[[], Awaitable[None]]

    async def complete(self, prompt: Any, *, options: dict[str, Any] | None = None) -> Any:
        await self._authorize()
        return await self._complete(prompt, options=options)


@dataclass(slots=True)
class CatalogContext:
    model: Model
    agent_instruction: str = ""
    services: Any = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Replace broad runtime services with a capability-scoped facade."""

        self.metadata = MappingProxyType(
            {
                key: _immutable_context_value(value)
                for key, value in self.metadata.items()
                if key in _PUBLIC_CONTEXT_FIELDS
            }
        )
        services = self.services
        if services is None or isinstance(services, ManagedFunctionCapabilities):
            return
        authorize_model = getattr(services, "authorize_model_completion", None)
        if callable(authorize_model):
            self.model = _CatalogModel(self.model.complete, authorize_model)
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
        self.role_function_calls: frozenset[str] = frozenset()

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
