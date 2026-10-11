"""Secure, deployment-controlled resolution of Open Workflow function catalogs."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import re
import socket
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urljoin, urlparse

import yaml

from .catalog import CatalogContext, FunctionCatalog
from .config import CatalogAuthenticationConfig, ExternalCatalogConfig
from .errors import OwaError, ToolError, UnsupportedWorkflowFeature, WorkflowSchemaError
from .expressions import ExpressionEvaluator, ExpressionMode
from .observability import EventSink, WorkflowEvent
from .protocols import (
    AuthenticationProvider,
    HttpClient,
    ProtocolServices,
    resolve_public_addresses_async,
)
from .security import ProfileAuthentication, SecurityConfig
from .versions import version_precedence

if TYPE_CHECKING:
    from .workflow import ExecutionState

CATALOG_FUNCTION_REFERENCE = re.compile(
    r"^(?P<name>[a-z0-9][a-z0-9-]*):(?P<version>[A-Za-z0-9.+-]+)@(?P<catalog>[A-Za-z][A-Za-z0-9_-]*)$"
)
SUPPORTED_FUNCTION_PROTOCOLS = frozenset({"http", "mcp", "a2a", "openapi"})


@dataclass(frozen=True, slots=True)
class CatalogFunctionReference:
    name: str
    version: str
    catalog: str

    @property
    def value(self) -> str:
        return f"{self.name}:{self.version}@{self.catalog}"


@dataclass(frozen=True, slots=True)
class ResolvedCatalogFunction:
    reference: str
    resource_uri: str
    digest: str
    definition_json: str
    execution_definition_json: str | None = None

    @property
    def definition(self) -> dict[str, Any]:
        return dict(json.loads(self.definition_json))

    @property
    def execution_definition(self) -> dict[str, Any]:
        return dict(json.loads(self.execution_definition_json or self.definition_json))


@dataclass(slots=True)
class _CachedResource:
    resource_uri: str
    reference: str
    definition: dict[str, Any]
    etag: str | None
    last_modified: str | None
    fetched_at: float
    digest: str


def parse_catalog_function_reference(value: str) -> CatalogFunctionReference | None:
    match = CATALOG_FUNCTION_REFERENCE.fullmatch(value)
    if match is None or version_precedence(match.group("version")) is None:
        return None
    return CatalogFunctionReference(
        name=match.group("name"),
        version=match.group("version"),
        catalog=match.group("catalog"),
    )


class ExternalCatalogResolver:
    """Resolve only explicitly referenced, version-pinned catalog functions.

    The workflow supplies a catalog endpoint, but the deployment policy owns
    host allowlists, credentials, transport limits, and cache behavior. Remote
    ``run.script`` functions are deliberately rejected: fetching arbitrary code
    is not an execution sandbox.
    """

    def __init__(
        self,
        policies: Mapping[str, ExternalCatalogConfig] | None = None,
        *,
        http: HttpClient | None = None,
        event_sink: EventSink | None = None,
        security: SecurityConfig | None = None,
    ) -> None:
        self.policies = dict(policies or {})
        self.http = http
        self.event_sink = event_sink
        self.security = security
        self._cache: dict[str, _CachedResource] = {}
        self._registered_bindings: dict[str, ResolvedCatalogFunction] = {}
        self._resolved: dict[str, set[str]] = {}
        self._states: dict[str, str] = {name: "configured" for name in self.policies}

    async def resolve_workflow(
        self,
        workflow: Mapping[str, Any],
        catalog: FunctionCatalog,
    ) -> dict[str, Any]:
        """Legacy registration surface; compilation uses immutable bindings."""
        await self.resolve_functions(workflow, catalog)
        return self.capabilities()

    async def resolve_functions(
        self,
        workflow: Mapping[str, Any],
        catalog: FunctionCatalog,
    ) -> tuple[ResolvedCatalogFunction, ...]:
        """Validate trust and return invocation-independent immutable content."""

        use = workflow.get("use")
        catalogs = use.get("catalogs") if isinstance(use, Mapping) else None
        if not isinstance(catalogs, Mapping) or not catalogs:
            return ()

        references = _referenced_functions(workflow)
        endpoints: dict[str, str] = {}
        policies: dict[str, ExternalCatalogConfig] = {}
        for alias_value, definition in catalogs.items():
            alias = str(alias_value)
            if alias == "default":
                raise UnsupportedWorkflowFeature(
                    "explicit external catalog alias 'default' cannot shadow the runtime catalog"
                )
            policy = self._policy(alias)
            endpoint = _endpoint_uri(definition)
            if endpoint is None:
                raise WorkflowSchemaError(f"external catalog '{alias}' requires an endpoint URI")
            _validate_endpoint(endpoint, policy)
            if policy.allowed_endpoints and endpoint.rstrip("/") not in set(
                policy.allowed_endpoints
            ):
                raise UnsupportedWorkflowFeature(
                    f"external catalog endpoint is not approved for alias '{alias}'",
                    details={"catalog": alias},
                )
            self._states[alias] = "validated"
            endpoints[alias] = endpoint
            policies[alias] = policy

        bindings: list[ResolvedCatalogFunction] = []
        for reference in sorted(references, key=lambda item: item.value):
            endpoint = endpoints.get(reference.catalog)
            if endpoint is None or reference.catalog not in policies:
                raise UnsupportedWorkflowFeature(
                    f"catalog '{reference.catalog}' is not deployment-trusted",
                    details={"function": reference.value},
                )
            policy = policies[reference.catalog]
            resource_uri = _function_uri(endpoint, reference)
            _validate_endpoint(resource_uri, policy)
            try:
                resource = await self._fetch(resource_uri, policy, reference.value)
            except OwaError as exc:
                self._states[reference.catalog] = (
                    "unavailable" if isinstance(exc, ToolError) else "rejected"
                )
                raise
            evaluation = workflow.get("evaluate", {})
            components = workflow.get("use", {})
            _validate_function_definition(
                resource.definition, reference, evaluation=evaluation, components=components
            )
            binding = ResolvedCatalogFunction(
                reference.value,
                resource_uri,
                resource.digest,
                json.dumps(
                    resource.definition,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                ),
                json.dumps(
                    _resolve_function_definition(resource.definition, components),
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                ),
            )
            bindings.append(binding)
            expression_mode = cast(ExpressionMode, evaluation.get("mode", "strict"))

            async def invoke(
                payload: Any,
                context: CatalogContext,
                *,
                captured: ResolvedCatalogFunction = binding,
                mode: ExpressionMode = expression_mode,
            ) -> Any:
                return await self.invoke_frozen(
                    captured,
                    payload,
                    variables={"context": context.metadata},
                    metadata=context.metadata,
                    mode=mode,
                )

            catalog.register(reference.value, invoke)
            self._registered_bindings[reference.value] = binding
            self._resolved.setdefault(reference.catalog, set()).add(reference.value)
        return tuple(bindings)

    def registered_binding(self, reference: str) -> ResolvedCatalogFunction | None:
        """Compatibility content; asynchronous plans always use their own closure."""
        return self._registered_bindings.get(reference)

    async def invoke_frozen(
        self,
        binding: ResolvedCatalogFunction,
        payload: Any,
        *,
        variables: Mapping[str, Any],
        operation_id: str | None = None,
        mode: ExpressionMode = "strict",
        metadata: Mapping[str, Any] | None = None,
        parent_state: ExecutionState | None = None,
    ) -> Any:
        """Compatibility invocation through the same portable task pipeline."""
        from .workflow import ExecutionState, WorkflowExecutor

        reference = str(variables.get("_task_reference", "/"))
        execution_reference = str(variables.get("_execution_reference", reference))
        task_metadata = dict(metadata or {})
        if operation_id is not None:
            # The prepared adapter preserves this already computed effect identity.
            task_metadata["_catalog_operation_id"] = operation_id
        task_metadata["_catalog_tasks"] = {execution_reference: binding}
        task_metadata["_external_catalog_resolver"] = self
        executor = WorkflowExecutor(FunctionCatalog())
        if parent_state is not None:
            state = executor._copy_state(parent_state)
            state.data = payload
            state.metadata = task_metadata
            # A synchronously compiled compatibility plan has no derived import
            # scope. Its outer native task remains the checkpoint unit.
            state.scope_executor = None
        else:
            state = ExecutionState(
                data=payload,
                variables=dict(variables),
                context=variables.get("context", payload),
                expressions=ExpressionEvaluator(mode=mode),
                metadata=task_metadata,
            )
        await executor._run_task_item(
            binding.reference,
            binding.execution_definition,
            state,
            reference,
            execution_reference=execution_reference,
        )
        if parent_state is not None:
            parent_state.context = state.context
            parent_state.variables["context"] = state.context
            parent_state.stop = state.stop
        return state.data

    async def invoke_prepared(
        self,
        binding: ResolvedCatalogFunction,
        call: str,
        payload: Any,
        *,
        mode: ExpressionMode = "strict",
    ) -> Any:
        """Dispatch an evaluated protocol payload under current deployment trust."""
        reference = parse_catalog_function_reference(binding.reference)
        if reference is None:
            raise WorkflowSchemaError("invalid frozen catalog reference")
        policy = self._policy(reference.catalog)
        _validate_endpoint(binding.resource_uri, policy)
        if policy.allowed_endpoints and not any(
            _function_uri(endpoint, reference) == binding.resource_uri
            for endpoint in policy.allowed_endpoints
        ):
            raise UnsupportedWorkflowFeature(
                "captured external catalog endpoint is no longer deployment-approved",
                details={"function": binding.reference},
            )
        self._verify_pin(binding.digest, policy, binding.reference, binding.resource_uri)
        expected_call, _ = _validate_function_definition(
            binding.execution_definition, reference, evaluation={"mode": mode}
        )
        if call != expected_call:
            raise UnsupportedWorkflowFeature("captured catalog protocol does not match dispatch")
        if not isinstance(payload, Mapping):
            raise ToolError("catalog function produced a non-object protocol payload")
        invocation_payload = dict(payload)
        if _contains_sensitive_header(invocation_payload):
            raise UnsupportedWorkflowFeature(
                "external catalog invocation authorization headers are disabled"
            )
        _validate_invocation_endpoints(invocation_payload, policy)
        client = self._client(policy, auth_host=urlparse(binding.resource_uri).hostname)
        if client.transport is not None:
            await _validate_invocation_destinations(invocation_payload, policy, client)
        return await ProtocolServices(client).call(call, dict(invocation_payload))

    def capabilities(self) -> dict[str, Any]:
        configured = sorted(self.policies)
        return {
            "enabled": bool(configured),
            "configuredCatalogs": configured,
            "states": {name: self._states.get(name, "configured") for name in configured},
            "resolvedFunctions": sorted(
                function for functions in self._resolved.values() for function in functions
            ),
            "policy": {
                "httpsOnly": True,
                "tlsVerification": True,
                "redirects": False,
                "versionPinning": "semantic_version_required",
                "integrity": {
                    name: "required" if policy.require_integrity_pin else "optional"
                    for name, policy in self.policies.items()
                },
                "cache": {
                    "revalidation": any(policy.revalidate for policy in self.policies.values()),
                    "ttlSeconds": {
                        name: policy.cache_ttl_seconds for name, policy in self.policies.items()
                    },
                },
            },
        }

    def _policy(self, alias: str) -> ExternalCatalogConfig:
        policy_value: Any = self.policies.get(alias)
        if policy_value is None:
            raise UnsupportedWorkflowFeature(
                f"catalog '{alias}' is not deployment-trusted",
                details={"catalog": alias},
            )
        if isinstance(policy_value, ExternalCatalogConfig):
            policy = policy_value
        else:
            try:
                policy = ExternalCatalogConfig.model_validate(policy_value)
            except (TypeError, ValueError) as exc:
                raise WorkflowSchemaError(
                    f"invalid deployment policy for external catalog '{alias}'"
                ) from exc
        if not policy.allowed_hosts:
            raise UnsupportedWorkflowFeature(
                f"external catalog '{alias}' requires a non-empty host allowlist",
                details={"catalog": alias},
            )
        return policy

    def _client(self, policy: ExternalCatalogConfig, *, auth_host: str | None = None) -> HttpClient:
        if self.http is not None:
            if self.http.transport is not None:
                return self.http
            return HttpClient(
                timeout=self.http.timeout,
                max_response_bytes=self.http.max_response_bytes,
                verify_tls=True,
                follow_redirects=False,
                allowed_hosts=self.http.allowed_hosts,
                authentication=self.http.authentication,
                destination_resolver=lambda endpoint: _resolve_external_destination(
                    endpoint, policy
                ),
            )
        auth = policy.authentication
        authentication: AuthenticationProvider | None = _authentication(auth, self.security)
        if authentication is not None and auth_host:
            authentication = _HostBoundAuthentication(authentication, auth_host)
        return HttpClient(
            timeout=policy.timeout_seconds,
            max_response_bytes=policy.max_response_bytes,
            verify_tls=True,
            follow_redirects=False,
            allowed_hosts=set(policy.allowed_hosts),
            authentication=authentication,
        )

    async def _fetch(
        self, resource_uri: str, policy: ExternalCatalogConfig, reference: str
    ) -> _CachedResource:
        now = time.monotonic()
        cached = self._cache.get(resource_uri)
        if cached is not None and now - cached.fetched_at > policy.max_cache_age_seconds:
            self._cache.pop(resource_uri, None)
            cached = None
        if cached is not None and now - cached.fetched_at < policy.cache_ttl_seconds:
            self._verify_pin(cached.digest, policy, reference, resource_uri)
            self._emit("CatalogCacheHit", reference, status="cached")
            return cached

        headers: dict[str, str] = {}
        if cached is not None and policy.revalidate:
            if cached.etag:
                headers["If-None-Match"] = cached.etag
            if cached.last_modified:
                headers["If-Modified-Since"] = cached.last_modified
        try:
            client = self._client(policy, auth_host=urlparse(resource_uri).hostname)
            self._emit("CatalogFetchStarted", reference, status="fetching")
            if client.transport is not None:
                await _validate_network_destination(resource_uri, policy, client)
            response = await client.request(
                "GET",
                resource_uri,
                headers=headers,
                output="response",
                request_timeout=policy.timeout_seconds,
                allow_not_modified=True,
            )
        except OwaError as exc:
            safe_code = exc.code
            self._emit(
                "CatalogFetchFailed",
                reference,
                status="rejected",
                error={"code": safe_code},
            )
            if isinstance(exc, (UnsupportedWorkflowFeature, WorkflowSchemaError)):
                raise
            raise ToolError(
                "external catalog resolution failed",
                details={"function": reference, "code": safe_code},
            ) from exc
        except Exception as exc:
            self._emit(
                "CatalogFetchFailed",
                reference,
                status="unavailable",
                error={"code": "catalog_resolution_failed"},
            )
            raise ToolError(
                "external catalog resolution failed",
                details={"function": reference, "code": "catalog_resolution_failed"},
            ) from exc
        if not isinstance(response, Mapping):
            raise ToolError("external catalog returned an invalid HTTP response")
        status = response.get("status")
        if status == 304 and cached is not None:
            self._verify_pin(cached.digest, policy, reference, resource_uri)
            cached.fetched_at = now
            self._emit("CatalogRevalidated", reference, status="revalidated")
            return cached
        if not isinstance(status, int) or status < 200 or status >= 300:
            raise ToolError(
                "external catalog returned an invalid status",
                details={"function": reference, "status": status},
            )
        body = response.get("body")
        from .workflow import _validate_json_value, load_workflow_text

        if not isinstance(body, (bytes, str)):
            _validate_json_value(body)
        if isinstance(body, bytes):
            raw = body
        elif isinstance(body, str):
            raw = body.encode("utf-8")
        else:
            raw = yaml.safe_dump(body, sort_keys=True).encode("utf-8")
        if len(raw) > policy.max_response_bytes:
            raise ToolError("external catalog response exceeds configured maximum size")
        digest = hashlib.sha256(raw).hexdigest()
        self._verify_pin(digest, policy, reference, resource_uri)
        try:
            definition = (
                dict(body) if isinstance(body, Mapping) else load_workflow_text(raw.decode("utf-8"))
            )
            _validate_json_value(definition)
        except (UnicodeDecodeError, WorkflowSchemaError, RecursionError):
            raise WorkflowSchemaError(
                f"external catalog function {reference} is not valid YAML"
            ) from None
        if not isinstance(definition, Mapping):
            raise WorkflowSchemaError(f"external catalog function {reference} must be an object")
        headers_value = response.get("headers")
        response_headers = dict(headers_value) if isinstance(headers_value, Mapping) else {}
        resource = _CachedResource(
            resource_uri=resource_uri,
            reference=reference,
            definition=dict(definition),
            etag=_header(response_headers, "etag"),
            last_modified=_header(response_headers, "last-modified"),
            fetched_at=now,
            digest=digest,
        )
        if resource_uri not in self._cache and len(self._cache) >= policy.max_cache_entries:
            oldest_uri = min(self._cache, key=lambda uri: self._cache[uri].fetched_at)
            self._cache.pop(oldest_uri, None)
        self._cache[resource_uri] = resource
        self._emit("CatalogResolved", reference, status="resolved")
        return resource

    def _emit(
        self,
        event_type: str,
        reference: str,
        *,
        status: str,
        error: dict[str, Any] | None = None,
    ) -> None:
        if self.event_sink is None:
            return
        self.event_sink.emit(
            WorkflowEvent(
                event_type=event_type,
                operation_id=f"catalog:{reference}",
                status=status,
                error=error,
            )
        )

    @staticmethod
    def _verify_pin(
        digest: str,
        policy: ExternalCatalogConfig,
        reference: str,
        resource_uri: str,
    ) -> None:
        expected = (
            policy.integrity_pins.get(reference)
            or policy.integrity_pins.get(reference.split("@", 1)[0])
            or policy.integrity_pins.get(resource_uri)
        )
        if expected is None and policy.require_integrity_pin:
            raise ToolError(
                "external catalog integrity pin is required",
                details={"function": reference},
            )
        if expected is not None and digest != expected:
            raise ToolError(
                "external catalog integrity verification failed",
                details={"function": reference},
            )


def _authentication(
    config: CatalogAuthenticationConfig,
    security: SecurityConfig | None,
) -> AuthenticationProvider | None:
    if not config.security_profile:
        return None
    if security is None:
        raise ToolError(
            "catalog security profile reference requires runtime security configuration",
            details={"profile": config.security_profile},
        )
    return ProfileAuthentication(security, config.security_profile)


def _endpoint_uri(definition: Any) -> str | None:
    if isinstance(definition, str):
        return definition
    if not isinstance(definition, Mapping):
        return None
    endpoint = definition.get("endpoint")
    if isinstance(endpoint, str):
        return endpoint
    if isinstance(endpoint, Mapping):
        if "authentication" in endpoint:
            raise WorkflowSchemaError(
                "workflow catalog authentication is not allowed; configure credentials "
                "in deployment"
            )
        uri = endpoint.get("uri")
        return uri if isinstance(uri, str) else None
    return None


def _validate_endpoint(uri: str, policy: ExternalCatalogConfig) -> None:
    try:
        parsed = urlparse(uri)
    except ValueError as exc:
        raise WorkflowSchemaError("external catalog endpoint is malformed") from exc
    if parsed.scheme != "https" or not parsed.hostname:
        raise UnsupportedWorkflowFeature(
            "external catalogs require absolute HTTPS endpoints",
            details={"endpoint": uri},
        )
    if parsed.username or parsed.password:
        raise WorkflowSchemaError("external catalog endpoints cannot contain credentials")
    if parsed.hostname.lower() not in set(policy.allowed_hosts):
        raise UnsupportedWorkflowFeature(
            f"external catalog host is not allowed: {parsed.hostname}",
            details={"endpoint": uri, "allowed_hosts": policy.allowed_hosts},
        )


def _function_uri(endpoint: str, reference: CatalogFunctionReference) -> str:
    parsed = urlparse(endpoint)
    path = f"functions/{reference.name}/{reference.version}/function.yaml"
    if parsed.hostname == "github.com":
        segments = [segment for segment in parsed.path.split("/") if segment]
        if len(segments) < 2:
            raise WorkflowSchemaError("GitHub catalog endpoint must include owner and repository")
        owner, repository = segments[:2]
        branch = "main"
        if "tree" in segments:
            index = segments.index("tree")
            if index + 1 < len(segments):
                branch = "/".join(segments[index + 1 :])
        return f"https://raw.githubusercontent.com/{owner}/{repository}/refs/heads/{branch}/{path}"
    if parsed.hostname == "gitlab.com":
        base = endpoint.rstrip("/")
        if "/-/tree/" in base:
            base, branch = base.split("/-/tree/", 1)
            return f"{base}/-/raw/{branch}/{path}"
        return f"{base}/-/raw/main/{path}"
    return urljoin(endpoint.rstrip("/") + "/", path)


def _validate_function_definition(
    definition: Mapping[str, Any],
    reference: CatalogFunctionReference,
    *,
    evaluation: Mapping[str, Any] | None = None,
    components: Mapping[str, Any] | None = None,
) -> tuple[str, Any]:
    arguments = definition.get("with")
    if isinstance(arguments, Mapping) and "$ref" in arguments:
        raise UnsupportedWorkflowFeature(
            "external catalog function references are disabled",
            details={"function": reference.value},
        )
    if "run" in definition:
        raise UnsupportedWorkflowFeature(
            "external catalog script functions are disabled",
            details={"function": reference.value},
        )
    call = definition.get("call")
    if call not in SUPPORTED_FUNCTION_PROTOCOLS:
        raise UnsupportedWorkflowFeature(
            "external catalog function must use a supported protocol call",
            details={"function": reference.value},
        )
    function_with = definition.get("with")
    if function_with is not None and not isinstance(function_with, Mapping):
        raise WorkflowSchemaError(f"external catalog function {reference.value} has invalid with")
    if _contains_sensitive_header(function_with):
        raise UnsupportedWorkflowFeature(
            "external catalog function authorization headers are disabled",
            details={"function": reference.value},
        )
    from .workflow import generate_default_workflow, validate_capabilities, validate_schema

    workflow = generate_default_workflow()
    if evaluation:
        workflow["evaluate"] = dict(evaluation)
    workflow["do"] = [{reference.name: dict(definition)}]
    try:
        validate_schema(workflow)
        workflow["do"] = [
            {
                reference.name: _resolve_function_definition(
                    definition, components, name=reference.name
                )
            }
        ]
        validate_capabilities(workflow)
    except WorkflowSchemaError:
        raise WorkflowSchemaError(
            "invalid external catalog task definition", details={"function": reference.value}
        ) from None
    except OwaError as exc:
        exc.details["function"] = reference.value
        location = exc.details.get("reference")
        prefix = f"/do/0/{reference.name}"
        if isinstance(location, str) and location.startswith(prefix):
            exc.details["reference"] = location.removeprefix(prefix) or "/"
        raise
    return str(call), function_with


def _resolve_function_definition(
    definition: Mapping[str, Any], components: Mapping[str, Any] | None, *, name: str = "function"
) -> dict[str, Any]:
    from .definitions import resolve_reusable_components

    # Caller functions/catalogs stay in their own scopes and trust boundaries.
    use = {
        key: value
        for key, value in (components or {}).items()
        if key in {"timeouts", "errors", "retries"}
    }
    workflow = resolve_reusable_components({"use": use, "do": [{name: dict(definition)}]})
    return cast(dict[str, Any], workflow["do"][0][name])


def _nested_values(value: Any) -> list[Any]:
    values = [value]
    if isinstance(value, Mapping):
        for item in value.values():
            values.extend(_nested_values(item))
    elif isinstance(value, list):
        for item in value:
            values.extend(_nested_values(item))
    return values


def _contains_key(value: Any, key: str) -> bool:
    if isinstance(value, Mapping):
        return key in value or any(_contains_key(item, key) for item in value.values())
    if isinstance(value, list):
        return any(_contains_key(item, key) for item in value)
    return False


def _validate_invocation_endpoints(
    value: Any, policy: ExternalCatalogConfig, *, key: str | None = None
) -> None:
    endpoint_keys = {"endpoint", "server", "uri", "url"}
    if isinstance(value, Mapping):
        for name, item in value.items():
            _validate_invocation_endpoints(item, policy, key=str(name))
    elif isinstance(value, list):
        for item in value:
            _validate_invocation_endpoints(item, policy, key=key)
    elif key in endpoint_keys and isinstance(value, str):
        _validate_endpoint(value, policy)


async def _validate_invocation_destinations(
    value: Any, policy: ExternalCatalogConfig, client: HttpClient, *, key: str | None = None
) -> None:
    endpoint_keys = {"endpoint", "server", "uri", "url"}
    if isinstance(value, Mapping):
        for name, item in value.items():
            await _validate_invocation_destinations(item, policy, client, key=str(name))
    elif isinstance(value, list):
        for item in value:
            await _validate_invocation_destinations(item, policy, client, key=key)
    elif key in endpoint_keys and isinstance(value, str):
        await _validate_network_destination(value, policy, client)


async def _resolve_external_destination(uri: str, policy: ExternalCatalogConfig) -> tuple[str, ...]:
    try:
        parsed = urlparse(uri)
        host = parsed.hostname
    except ValueError as exc:
        raise WorkflowSchemaError("external catalog destination is malformed") from exc
    try:
        return await resolve_public_addresses_async(uri, timeout=policy.timeout_seconds)
    except ToolError as exc:
        if "disallowed IP address" in str(exc):
            raise UnsupportedWorkflowFeature(
                "external catalog destination resolves to a disallowed IP address",
                details={"host": host},
            ) from exc
        raise ToolError(
            "external catalog destination could not be resolved",
            details={"host": host},
        ) from exc


async def _validate_network_destination(
    uri: str, policy: ExternalCatalogConfig, client: HttpClient
) -> None:
    try:
        parsed = urlparse(uri)
    except ValueError as exc:
        raise WorkflowSchemaError("external catalog destination is malformed") from exc
    host = parsed.hostname
    if host is None:
        return
    literal = _public_ip(host)
    if literal is not None:
        if not literal.is_global:
            raise UnsupportedWorkflowFeature(
                "external catalog destination resolves to a disallowed IP address",
                details={"host": host},
            )
        return
    # Mock transports do not perform DNS. Production clients re-check all
    # addresses immediately before the request to reject rebinding to a local
    # or otherwise non-public destination.
    if client.transport is not None:
        return
    try:
        addresses = await asyncio.to_thread(
            socket.getaddrinfo,
            host,
            parsed.port or 443,
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise ToolError(
            "external catalog destination could not be resolved",
            details={"host": host},
        ) from exc
    try:
        resolved = {
            ipaddress.ip_address(str(item[4][0])) for item in addresses if item[4] and item[4][0]
        }
    except (IndexError, TypeError, ValueError) as exc:
        raise ToolError(
            "external catalog destination could not be resolved",
            details={"host": host},
        ) from exc
    if not resolved or any(not address.is_global for address in resolved):
        raise UnsupportedWorkflowFeature(
            "external catalog destination resolves to a disallowed IP address",
            details={"host": host},
        )


def _public_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _contains_sensitive_header(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).lower() in {"authorization", "proxy-authorization", "cookie"}:
                return True
            if _contains_sensitive_header(item):
                return True
    elif isinstance(value, list):
        return any(_contains_sensitive_header(item) for item in value)
    return False


class _HostBoundAuthentication:
    def __init__(self, provider: AuthenticationProvider, host: str) -> None:
        self.provider = provider
        self.host = host.lower()

    def headers(self, endpoint: str) -> Mapping[str, str]:
        return self.provider.headers(endpoint) if urlparse(endpoint).hostname == self.host else {}


def _referenced_functions(workflow: Mapping[str, Any]) -> set[CatalogFunctionReference]:
    references: set[CatalogFunctionReference] = set()
    from .workflow import _task_scopes, _walk_tasks

    def tasks(items: Any) -> None:
        for _, definition in _walk_tasks(items, prefix="/do"):
            call = definition.get("call")
            reference = parse_catalog_function_reference(call) if isinstance(call, str) else None
            if reference is not None and reference.catalog != "default":
                references.add(reference)
            for _, nested in _task_scopes(definition):
                tasks(nested)

    tasks(workflow.get("do", []))
    for name, definition in workflow.get("use", {}).get("functions", {}).items():
        tasks([{name: definition}])
    return references


def _header(headers: Mapping[Any, Any], name: str) -> str | None:
    for key, value in headers.items():
        if str(key).lower() == name:
            return str(value)
    return None
