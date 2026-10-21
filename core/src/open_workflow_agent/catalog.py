"""Runtime catalog and deterministic model abstractions."""

from __future__ import annotations

import asyncio
import copy
import inspect
import json
import math
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Protocol

from .credential_guard import credential_scope
from .errors import ModelError, OwaError, ToolError, _ModelOutcomeUncertain
from .model_responses import bounded_model_json, completion_message
from .security import SecretReference, resolve_secret

if TYPE_CHECKING:
    from .config import ProviderBoundsConfig


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


def _completion_due(seconds: float, *, started: float | None = None) -> float:
    from .workflow import _ACTIVE_TIMEOUTS

    due = (started if started is not None else asyncio.get_running_loop().time()) + seconds
    for scope in _ACTIVE_TIMEOUTS.get():
        original_due = scope.deadline.when()
        if original_due is not None:
            due = min(due, original_due)
    return due


class BoundedModel:
    """Opt-in finite guard over a trusted common Model implementation."""

    def __init__(self, model: Model, bounds: ProviderBoundsConfig) -> None:
        from .config import ProviderBoundsConfig

        self._model = model
        self.bounds = ProviderBoundsConfig.model_validate(bounds.model_dump())

    @property
    def calls(self) -> Any:
        # Preserve deterministic injected-model counters without copying them.
        return getattr(self._model, "calls", None)

    async def complete(self, prompt: Any, *, options: dict[str, Any] | None = None) -> Any:
        if options is not None and type(options) is not dict:
            raise ModelError("model options must be an object")
        due = _completion_due(self.bounds.timeout_seconds)
        bounded_model_json(prompt, limit=self.bounds.max_input_bytes)
        bounded_model_json(options or {}, limit=65_536)
        from .config import MODEL_CREDENTIAL_OPTIONS

        if set(options or {}) & MODEL_CREDENTIAL_OPTIONS:
            raise ModelError("model credential overrides are forbidden")
        attempted = False
        returned = False
        with credential_scope() as guard:
            try:
                if asyncio.get_running_loop().time() >= due:
                    raise TimeoutError
                async with asyncio.timeout_at(due):
                    pending = self._model.complete(prompt, options=options)
                    if not inspect.isawaitable(pending):
                        raise ModelError("common model async interface is invalid")
                    attempted = True
                    result = await pending
                    returned = True
                    encoded = bounded_model_json(result, limit=self.bounds.max_output_bytes)
                    if isinstance(result, dict) and {"tool_call", "tool_calls"}.intersection(
                        result
                    ):
                        if {"tool_call", "tool_calls"} <= set(result):
                            raise ModelError("common model tool request is ambiguous")
                        requests = result.get("tool_calls", [result.get("tool_call")])
                        if (
                            type(requests) is not list
                            or not requests
                            or len(requests) > self.bounds.max_tool_calls
                        ):
                            raise ModelError("common model tool request count is invalid")
                        for request in requests:
                            if (
                                type(request) is not dict
                                or set(request) - {"name", "arguments", "input"}
                                or type(request.get("name")) is not str
                                or not request["name"]
                                or len(request["name"].encode("utf-8")) > 128
                                or {"arguments", "input"} <= set(request)
                            ):
                                raise ModelError("common model tool request is invalid")
                            if "arguments" in request and type(request["arguments"]) is not dict:
                                raise ModelError("common model tool arguments require an object")
                            bounded_model_json(
                                request.get("arguments", request.get("input", {})),
                                limit=self.bounds.max_argument_bytes,
                            )
                    if asyncio.get_running_loop().time() >= due:
                        raise TimeoutError
                    return guard.project(json.loads(encoded))
            except TimeoutError:
                if attempted:
                    raise _ModelOutcomeUncertain(
                        "model outcome remains unresolved", details={"reason": "timeout"}
                    ) from None
                raise ModelError("model preparation deadline expired") from None
            except OwaError as error:
                if returned:
                    raise _ModelOutcomeUncertain(
                        "model returned an unusable outcome", details={"reason": "invalid_response"}
                    ) from None
                guard.error(error)
                raise
            except Exception:
                if returned:
                    raise _ModelOutcomeUncertain(
                        "model returned an unusable outcome", details={"reason": "invalid_response"}
                    ) from None
                if attempted:
                    raise _ModelOutcomeUncertain(
                        "model outcome remains unresolved", details={"reason": "transport_failure"}
                    ) from None
                raise ModelError("model completion failed") from None


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
        bounds: ProviderBoundsConfig | None = None,
    ) -> None:
        self.name = name
        self.temperature = temperature
        from .config import ProviderBoundsConfig

        self.options = copy.deepcopy(options or {})
        self._credentials = MappingProxyType(
            {
                key: SecretReference.model_validate(reference.model_dump())
                for key, reference in (credentials or {}).items()
            }
        )
        self._authorize_credential = authorize_credential
        self.bounds = ProviderBoundsConfig.model_validate(
            bounds.model_dump() if bounds is not None else {}
        )

    async def complete(self, prompt: Any, *, options: dict[str, Any] | None = None) -> Any:
        if options is not None and type(options) is not dict:
            raise ModelError("model options must be an object")
        loop = asyncio.get_running_loop()
        started = loop.time()
        due = _completion_due(self.bounds.timeout_seconds, started=started)
        bounded_model_json(prompt, limit=self.bounds.max_input_bytes)
        bounded_model_json(options or {}, limit=65_536)
        from .config import MODEL_CREDENTIAL_OPTIONS

        if set(options or {}) & (
            MODEL_CREDENTIAL_OPTIONS
            | {
                "model",
                "messages",
                "temperature",
                "api_base",
                "base_url",
                "client",
                "http_client",
                "tools",
                "headers",
                "extra_headers",
            }
        ):
            raise ModelError("model credential/routing overrides are forbidden")
        kwargs = {**self.options, **(options or {})}
        bounded_model_json(kwargs, limit=262_144)
        if set(kwargs) & (
            MODEL_CREDENTIAL_OPTIONS | {"model", "messages", "temperature", "client", "http_client"}
        ):
            raise ModelError("provider identity/options are outside its deployment binding")
        if (
            kwargs.get("stream", False) is not False
            or type(kwargs.get("num_retries", 0)) is not int
            or kwargs.get("num_retries", 0) != 0
            or type(kwargs.get("max_retries", 0)) is not int
            or kwargs.get("max_retries", 0) != 0
            or type(kwargs.get("n", 1)) is not int
            or kwargs.get("n", 1) != 1
            or any(
                key in kwargs
                for key in (
                    "fallbacks",
                    "context_window_fallbacks",
                    "content_policy_fallbacks",
                    "stream_options",
                )
            )
        ):
            raise ModelError("provider streams/retries/fallbacks are outside the finite profile")
        timeout = kwargs.pop("timeout", self.bounds.timeout_seconds)
        if type(timeout) not in {int, float} or not math.isfinite(timeout) or timeout <= 0:
            raise ModelError("provider timeout is invalid")
        timeout = min(timeout, self.bounds.timeout_seconds)
        due = min(due, started + timeout)
        # Preserve the enclosing common task/workflow deadline, including time
        # spent preparing/importing the provider before its first awaited send.
        kwargs["num_retries"] = 0
        kwargs["max_retries"] = 0
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
            attempted = False
            returned = False
            try:
                if loop.time() >= due:
                    raise TimeoutError
                async with asyncio.timeout_at(due):
                    if self._authorize_credential is not None:
                        for reference in self._credentials.values():
                            await self._authorize_credential(reference.from_env)
                    selected = {
                        key: resolve_secret(reference)
                        for key, reference in self._credentials.items()
                    }
                    remaining = due - loop.time()
                    if remaining <= 0:
                        raise TimeoutError
                    # Subtracting monotonic timestamps can round a few ULPs
                    # above the configured duration (for example 30.00000000000003).
                    # Keep the provider-side timeout within the explicit bound.
                    remaining = min(remaining, timeout)
                    pending = acompletion(
                        model=self.name,
                        messages=messages,
                        temperature=self.temperature,
                        timeout=remaining,
                        **kwargs,
                        **selected,
                    )
                    if not inspect.isawaitable(pending):
                        raise ModelError("provider async interface is invalid")
                    attempted = True
                    response = await pending
                    returned = True
                    result = completion_message(
                        response,
                        max_output_bytes=self.bounds.max_output_bytes,
                        max_tool_calls=self.bounds.max_tool_calls,
                        max_argument_bytes=self.bounds.max_argument_bytes,
                    )
                    if loop.time() >= due:
                        raise TimeoutError
                    return guard.project(result)
            except TimeoutError:
                if attempted:
                    raise _ModelOutcomeUncertain(
                        "model provider outcome remains unresolved", details={"reason": "timeout"}
                    ) from None
                raise ModelError("model preparation deadline expired") from None
            except OwaError as error:
                if returned:
                    raise _ModelOutcomeUncertain(
                        "model returned an unusable outcome", details={"reason": "invalid_response"}
                    ) from None
                guard.error(error)
                raise
            except Exception:
                if returned:
                    raise _ModelOutcomeUncertain(
                        "model returned an unusable outcome", details={"reason": "invalid_response"}
                    ) from None
                if attempted:
                    raise _ModelOutcomeUncertain(
                        "model provider outcome remains unresolved",
                        details={"reason": "transport_failure"},
                    ) from None
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
