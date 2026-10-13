"""Finite deployment-owned role bindings over common catalog/model/tool services.

Roles restrict an original caller; they never issue authority or launch agents,
children or provider-specific execution outside the common service boundary.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from jsonschema import Draft202012Validator

from .catalog import BoundedModel, CatalogContext, FakeModel, FunctionCatalog, LiteLLMModel, Model
from .effects import effect_request_digest
from .errors import ConfigurationError, ModelError, ToolError
from .execution_authority import current_authority
from .expressions import validate_expression_data
from .security import AuthorizationPolicy, ProfileAuthentication, authorize

if TYPE_CHECKING:
    from .config import AgentRoleConfig, ProviderBoundsConfig
    from .services import RuntimeServices

PROFILE = "owa/agent-role-binding/v1"
MAX_DATA_BYTES = 16_777_216
_CONTROL_FIELDS = frozenset(
    {
        "role",
        "model",
        "provider",
        "options",
        "tools",
        "instruction",
        "credentials",
        "security_profile",
        "authority",
    }
)


def _json(value: Any, *, limit: int = MAX_DATA_BYTES) -> str:
    validate_expression_data(value)
    result = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    if len(result.encode()) > limit:
        raise ModelError("role data exceeds its finite profile")
    return result


def validate_role_schema(schema: dict[str, Any] | None) -> None:
    if schema is None:
        return
    if type(schema) is not dict:
        raise ValueError("role output schema requires an inline finite JSON object")
    _json(schema, limit=65_536)

    def refs(value: Any, depth: int = 0) -> None:
        if depth > 32:
            raise ValueError("role output schema exceeds its finite depth")
        if isinstance(value, dict):
            for name, item in value.items():
                if name in {"$ref", "$dynamicRef", "$recursiveRef"} and (
                    type(item) is not str or not item.startswith("#")
                ):
                    raise ValueError("role output schema external references are unsupported")
                if name == "$id":
                    raise ValueError("role output schema base URI overrides are unsupported")
                refs(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                refs(item, depth + 1)

    refs(schema)
    Draft202012Validator.check_schema(schema)


@dataclass(frozen=True, slots=True)
class RoleDescriptor:
    name: str
    declaration_json: str

    @classmethod
    def capture(cls, name: str, config: AgentRoleConfig) -> RoleDescriptor:
        from .config import AgentRoleConfig

        config = AgentRoleConfig.model_validate(config.model_dump(mode="json"))
        return cls(
            name,
            _json(
                {"profile": PROFILE, "name": name, **config.model_dump(mode="json")}, limit=262_144
            ),
        )

    def as_value(self) -> dict[str, Any]:
        value = json.loads(self.declaration_json)
        if type(value) is not dict:
            raise ConfigurationError("role descriptor is inconsistent")
        return value

    @property
    def references(self) -> tuple[str, str]:
        version = self.as_value()["version"]
        return (f"agent-{self.name}:{version}@default", f"llm-{self.name}:{version}@default")

    @property
    def digest(self) -> str:
        return effect_request_digest(PROFILE, self.as_value())


@dataclass(frozen=True, slots=True)
class _RoleScope:
    descriptor: RoleDescriptor


_ROLE: ContextVar[_RoleScope | None] = ContextVar("owa_agent_role", default=None)


def current_role_descriptor() -> RoleDescriptor | None:
    scope = _ROLE.get()
    return scope.descriptor if scope is not None else None


@contextmanager
def _scope(descriptor: RoleDescriptor) -> Iterator[None]:
    token = _ROLE.set(_RoleScope(descriptor))
    try:
        yield
    finally:
        _ROLE.reset(token)


def authorize_role_action(action: str, resource: str) -> None:
    descriptor = current_role_descriptor()
    if descriptor is None:
        return
    value = descriptor.as_value()
    if action == "tool.invoke" and resource not in value["tools"]:
        raise ToolError("tool is outside the deployment role binding")
    if action == "workflow.run":
        raise ToolError("role delegation is not supported")
    if value["policy"] is not None:
        authority = current_authority()
        if authority is None or not authorize(
            authority.principal,
            AuthorizationPolicy.model_validate(value["policy"]),
            action=action,
            resource=resource,
        ):
            raise ToolError("original caller is outside the role action policy")


def _provider_bounds(config: AgentRoleConfig) -> ProviderBoundsConfig:
    from .config import ProviderBoundsConfig

    return ProviderBoundsConfig(
        timeout_seconds=config.model.options.get("timeout", 30),
        max_output_bytes=config.max_output_bytes,
        max_tool_calls=config.max_tool_calls,
    )


def _bounded_role_model(model: Model, config: AgentRoleConfig) -> Model:
    bounds = config.model.bounds
    if bounds is None or isinstance(model, LiteLLMModel):
        return model
    return BoundedModel(
        model,
        bounds.model_copy(
            update={
                "timeout_seconds": min(
                    bounds.timeout_seconds, config.model.options.get("timeout", 30)
                )
            }
        ),
    )


class RoleBindings:
    def __init__(
        self, services: RuntimeServices, role_models: Mapping[str, Model] | None = None
    ) -> None:
        self.services = services
        configured = services.config.agent.roles
        injected = dict(role_models or {})
        if set(injected) - set(configured):
            raise ConfigurationError("injected role model is not deployment-bound")
        self._descriptors = MappingProxyType(
            {name: RoleDescriptor.capture(name, config) for name, config in configured.items()}
        )
        self._models = MappingProxyType(
            {
                name: injected.get(name)
                or (
                    FakeModel()
                    if config.model.provider == "fake"
                    else LiteLLMModel(
                        config.model.name,
                        temperature=config.model.temperature,
                        options=config.model.options,
                        credentials=config.model.credentials,
                        authorize_credential=services.authorize_model_credential,
                        bounds=config.model.bounds or _provider_bounds(config),
                    )
                )
                for name, config in configured.items()
            }
        )
        self._models = MappingProxyType(
            {
                name: _bounded_role_model(model, configured[name])
                for name, model in self._models.items()
            }
        )
        self._tool_bindings_json = _json(self._tool_bindings(), limit=262_144)
        self.verify()

    def _tool_bindings(self) -> list[dict[str, Any]]:
        names = {
            name
            for descriptor in self._descriptors.values()
            for name in descriptor.as_value()["tools"]
        }
        bindings = []
        if len(names) > 64:
            raise ConfigurationError("role tool set exceeds its finite profile")
        for name in sorted(names):
            tool = self.services.tools.tools.get(name)
            if tool is None:
                bindings.append({"name": name, "type": "builtin"})
                continue
            # Only known nonsecret routing/projection fields qualify this profile.
            if set(tool.options) - {
                "method",
                "name",
                "transport",
                "protocolVersion",
                "binding",
                "operationId",
                "output",
                "redirect",
            }:
                raise ConfigurationError("role tool options require a qualified routing binding")
            for key, item in tool.options.items():
                if key == "redirect":
                    valid = type(item) is bool
                elif key == "transport":
                    valid = type(item) is dict and len(item) == 1
                    if valid and "stdio" in item:
                        valid = (
                            type(item["stdio"]) is dict
                            and set(item["stdio"]) == {"binding"}
                            and type(item["stdio"]["binding"]) is str
                        )
                    elif valid and "http" in item:
                        valid = (
                            type(item["http"]) is dict
                            and set(item["http"]) == {"endpoint"}
                            and type(item["http"]["endpoint"]) is str
                        )
                    else:
                        valid = False
                else:
                    valid = type(item) is str and len(item.encode("utf-8")) <= 4096
                if not valid:
                    raise ConfigurationError("role tool routing binding is invalid")
            authentication = tool.authentication
            if authentication is not None and not isinstance(authentication, ProfileAuthentication):
                raise ConfigurationError("role tool credentials require a named deployment profile")
            selection = tool.openapi_selection
            selection_binding = None
            if selection is not None:
                from .openapi_client import OpenApiOperationSelection

                selection = OpenApiOperationSelection.from_value(selection.as_value())
                selection_binding = {
                    "document_uri": selection.description.uri,
                    "document_digest": selection.description.digest,
                    "operation_id": selection.operation_id,
                    "version": selection.version,
                    "method": selection.method,
                    "endpoint": selection.endpoint,
                    "path": selection.path,
                    "credential_profile": selection.credential_profile,
                }
            bindings.append(
                {
                    "name": name,
                    "type": tool.type,
                    "endpoint": tool.endpoint,
                    "options": tool.options,
                    "security_profile": authentication.profile_name
                    if authentication is not None
                    else None,
                    "openapi_selection": selection_binding,
                }
            )
        return bindings

    @property
    def function_calls(self) -> frozenset[str]:
        return frozenset(
            reference
            for descriptor in self._descriptors.values()
            for reference in descriptor.references
        )

    def verify(self) -> None:
        current = {
            name: RoleDescriptor.capture(name, config)
            for name, config in self.services.config.agent.roles.items()
        }
        if current != dict(self._descriptors):
            raise ConfigurationError("deployment role binding changed")
        if _json(self._tool_bindings(), limit=262_144) != self._tool_bindings_json:
            raise ConfigurationError("deployment role tool routing changed")
        if sum(len(item.declaration_json.encode("utf-8")) for item in current.values()) > 262_144:
            raise ConfigurationError("role binding set exceeds its finite profile")
        available = set(self.services.agent_tools)
        if self.services.config.agent.tools:
            available &= set(self.services.config.agent.tools)
        if current and len(available) > 256:
            raise ConfigurationError("role deployment allowlist exceeds its finite profile")
        if any(set(descriptor.as_value()["tools"]) - available for descriptor in current.values()):
            raise ConfigurationError("role tools are outside the current deployment allowlist")

    def snapshot(self) -> dict[str, Any]:
        self.verify()
        available = set(self.services.agent_tools)
        if self.services.config.agent.tools:
            available &= set(self.services.config.agent.tools)
        return {
            "profile": PROFILE,
            "bindings": [self._descriptors[name].as_value() for name in sorted(self._descriptors)],
            "current_tool_allowlist": sorted(available),
            "effective_tool_bindings": json.loads(self._tool_bindings_json),
        }

    def register(self, catalog: FunctionCatalog) -> None:
        for name, descriptor in self._descriptors.items():
            for reference in descriptor.references:

                async def invoke(
                    payload: Any,
                    context: CatalogContext,
                    *,
                    selected: str = name,
                    agent: bool = reference.startswith("agent-"),
                ) -> Any:
                    return await self.call(selected, payload, context, agent=agent)

                catalog.register(reference, invoke)
        catalog.role_function_calls = self.function_calls

    async def call(self, name: str, payload: Any, context: CatalogContext, *, agent: bool) -> Any:
        self.verify()
        descriptor = self._descriptors.get(name)
        if descriptor is None:
            raise ToolError("agent role is not deployment-bound")
        _json(payload)
        if isinstance(payload, dict) and set(payload) & _CONTROL_FIELDS:
            raise ToolError("role model/tool/authority overrides are forbidden")
        value = descriptor.as_value()
        model = self._models[name]
        prompt = payload if isinstance(payload, dict) else {"input": payload}
        prompt = {
            **prompt,
            "instruction": value["instruction"],
            "tools": value["tools"] if agent else [],
        }
        current = prompt
        messages: list[dict[str, Any]] = []
        tool_calls = 0
        model_calls = 0
        with _scope(descriptor):
            for _ in range(value["max_rounds"] if agent else 1):
                if model_calls >= value["max_model_calls"]:
                    raise ModelError("role exceeded its model-call budget")
                _json(current)
                await self.services.authorize_current_action(
                    "model.complete", value["model"]["name"]
                )
                model_calls += 1
                try:
                    if isinstance(model, (LiteLLMModel, BoundedModel)):
                        # The provider owns preparation + send in one deadline,
                        # distinguishing local denial from an unresolved attempt.
                        response = await model.complete(current)
                    else:
                        async with asyncio.timeout(value["model"]["options"].get("timeout", 30)):
                            response = await model.complete(current)
                except TimeoutError:
                    raise ModelError("role model-call budget expired") from None
                _json(response, limit=value["max_output_bytes"])
                requests = response.get("tool_calls") if isinstance(response, dict) else None
                if isinstance(response, dict) and "tool_call" in response:
                    if "tool_calls" in response:
                        raise ToolError("role returned ambiguous tool requests")
                    requests = [response["tool_call"]]
                if requests is None:
                    if value["output_schema"] is not None:
                        try:
                            Draft202012Validator(value["output_schema"]).validate(response)
                        except Exception:
                            raise ModelError(
                                "role output does not match its declared schema"
                            ) from None
                    return response
                if (
                    not agent
                    or type(requests) is not list
                    or not requests
                    or any(
                        type(request) is not dict
                        or set(request) - {"name", "arguments", "input"}
                        or type(request.get("name")) is not str
                        or {"arguments", "input"} <= set(request)
                        for request in requests
                    )
                ):
                    raise ToolError("role returned an unsupported tool request")
                if tool_calls + len(requests) > value["max_tool_calls"]:
                    raise ToolError("role exceeded its tool-call budget")
                # Entire batch must pass before any tool side effect.
                for request in requests:
                    self.services.validate_agent_tool_payload(
                        request["name"], request.get("arguments", request.get("input", {}))
                    )
                for request in requests:
                    await self.services.preflight_agent_tool(
                        request["name"], request.get("arguments", request.get("input", {}))
                    )
                results = []
                for request in requests:
                    tool_calls += 1
                    result = await self.services.invoke_agent_tool(
                        request["name"], request.get("arguments", request.get("input", {}))
                    )
                    _json(result, limit=value["max_output_bytes"])
                    results.append({"name": request["name"], "result": result})
                messages.extend(
                    [
                        {"role": "assistant", "tool_calls": requests},
                        {"role": "tool", "results": results},
                    ]
                )
                current = {**prompt, "messages": messages, "tool_results": results}
        raise ToolError("role exceeded its round budget")
