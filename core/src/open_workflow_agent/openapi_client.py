"""Finite OpenAPI operation selection over the common bounded HTTP transport.

This private adapter implements reviewed routing/parameter profiles, not general
OpenAPI conformance. Durable workflow admission is a separate common service.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urljoin, urlparse

import httpx
import yaml
from jsonschema import Draft202012Validator

from .errors import OwaError, ToolError, WorkflowExecutionError
from .expressions import validate_expression_data
from .security import (
    ApiKeySecurityProfile,
    BearerSecurityProfile,
    MtlsSecurityProfile,
    OAuth2ClientCredentialsSecurityProfile,
    ProfileAuthentication,
)

if TYPE_CHECKING:
    from .protocols import AuthenticationProvider, HttpClient

VERSIONS = frozenset({"3.0.3", "3.2.0"})
MAX_DOCUMENT_BYTES = 1_048_576
MAX_DOCUMENT_NODES = 10_000
MAX_DEPTH = 32
MAX_LOCAL_REFERENCES = 32
_PROFILE = "owa/openapi-operation/v1"
_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})
_HEADER = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_TRANSPORT_HEADERS = frozenset(
    {
        "host",
        "content-length",
        "transfer-encoding",
        "connection",
        "keep-alive",
        "proxy-connection",
        "te",
        "trailer",
        "upgrade",
        "http2-settings",
    }
)
_ANNOTATIONS = {
    "title",
    "description",
    "default",
    "deprecated",
    "readOnly",
    "writeOnly",
    "example",
    "examples",
}
_SCHEMA_KEYS = _ANNOTATIONS | {
    "type",
    "enum",
    "const",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
    "uniqueItems",
    "items",
    "properties",
    "required",
    "additionalProperties",
    "minProperties",
    "maxProperties",
    "nullable",
}


class _OpenApiOutcomeUncertain(WorkflowExecutionError):
    """A dispatched operation without a usable bounded correlated outcome."""


def _failure(reason: str) -> ToolError:
    return ToolError("OpenAPI operation is unavailable or invalid", details={"reason": reason})


def _uncertain(reason: str) -> _OpenApiOutcomeUncertain:
    return _OpenApiOutcomeUncertain(
        "OpenAPI operation outcome is uncertain", details={"reason": reason}
    )


def _finite(value: Any) -> None:
    try:
        validate_expression_data(value)
    except OwaError:
        raise _failure("invalid_finite_value") from None
    remaining = MAX_DOCUMENT_NODES
    stack = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        remaining -= 1
        if remaining < 0 or depth > MAX_DEPTH:
            raise _failure("document_bound")
        if isinstance(item, dict):
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)


def _json(value: Any) -> str:
    _finite(value)
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    if len(encoded.encode()) > MAX_DOCUMENT_BYTES:
        raise _failure("document_bound")
    return encoded


def _unique(pairs: list[tuple[Any, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for name, item in pairs:
        if not isinstance(name, str) or name in value:
            raise _failure("duplicate_or_nonstring_key")
        value[name] = item
    return value


class _Loader(yaml.SafeLoader):
    pass


def _yaml_mapping(loader: _Loader, node: yaml.MappingNode) -> dict[str, Any]:
    return _unique(
        [
            (loader.construct_object(key), loader.construct_object(value))
            for key, value in node.value
        ]
    )


_Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _yaml_mapping)


def _parse(raw: bytes) -> dict[str, Any]:
    if not raw or len(raw) > MAX_DOCUMENT_BYTES:
        raise _failure("document_bound")
    try:
        text = raw.decode("utf-8")
        if text.lstrip().startswith(("{", "[")):
            value = json.loads(text, object_pairs_hook=_unique)
        else:
            for index, token in enumerate(yaml.scan(text)):
                if index > MAX_DOCUMENT_NODES * 8 or isinstance(
                    token, (yaml.AliasToken, yaml.AnchorToken, yaml.TagToken)
                ):
                    raise _failure("unsupported_yaml")
            value = yaml.load(text, Loader=_Loader)
        if not isinstance(value, dict):
            raise _failure("invalid_document")
        _finite(value)
        return value
    except (ValueError, UnicodeError, yaml.YAMLError, RecursionError):
        raise _failure("invalid_document") from None


def _reject_dot_path(value: str) -> None:
    # HTTPX removes literal dot segments when constructing its request URL.
    # Admission must never grant one route and dispatch a normalized other.
    try:
        path = urlparse(value).path
    except ValueError:
        raise _failure("invalid_endpoint") from None
    if any(segment in {".", ".."} for segment in path.split("/")):
        raise _failure("normalization_changing_path")


def _uri(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 4096 or any(ord(char) < 33 for char in value):
        raise _failure("invalid_endpoint")
    _reject_dot_path(value)
    try:
        parsed = urlparse(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise ValueError
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError
    except ValueError:
        raise _failure("invalid_endpoint") from None
    return value


@dataclass(frozen=True, slots=True)
class OpenApiDescription:
    uri: str
    document_json: str
    digest: str

    @classmethod
    def capture(cls, uri: str, value: Mapping[str, Any]) -> OpenApiDescription:
        encoded = _json(dict(value))
        document = _parse(encoded.encode())
        if document.get("openapi") not in VERSIONS or not isinstance(document.get("paths"), dict):
            raise _failure("unsupported_version_or_paths")
        info = document.get("info")
        if not isinstance(info, dict) or not all(
            isinstance(info.get(name), str) and info[name] for name in ("title", "version")
        ):
            raise _failure("invalid_document")
        return cls(_uri(uri), encoded, hashlib.sha256(encoded.encode()).hexdigest())

    def document(self) -> dict[str, Any]:
        value = _parse(self.document_json.encode())
        captured = self.capture(self.uri, value)
        if captured != self:
            raise _failure("description_changed")
        return value


class _PublicAuthentication:
    def headers(self, endpoint: str) -> dict[str, str]:
        return {}


async def fetch_validate_description(
    http: HttpClient,
    resource: Any,
    *,
    timeout: float,
    authentication: AuthenticationProvider | None = None,
) -> OpenApiDescription:
    from .protocols import HttpClient
    from .resources import resource_uri

    if not math.isfinite(timeout) or timeout <= 0:
        raise _failure("invalid_timeout")
    if (
        type(http.verify_tls) not in {bool, str}
        or http.verify_tls is False
        or (
            http.transport is not None
            and not isinstance(http.transport, (httpx.MockTransport, httpx.ASGITransport))
        )
    ):
        raise _failure("unsupported_description_transport")
    try:
        endpoint = _uri(resource_uri(resource))
    except OwaError:
        raise _failure("invalid_description_resource") from None
    public = HttpClient(
        timeout=min(http.timeout, timeout),
        max_response_bytes=min(http.max_response_bytes, MAX_DOCUMENT_BYTES),
        verify_tls=True,
        ca_bundle=http.verify_tls if isinstance(http.verify_tls, str) else None,
        follow_redirects=False,
        allowed_hosts=http.allowed_hosts,
        transport=http.transport,
        destination_resolver=http.destination_resolver,
    )
    try:
        response = await public.request(
            "GET",
            endpoint,
            headers={"Accept": "application/json, application/yaml"},
            request_timeout=timeout,
            follow_redirects=False,
            _authentication=authentication or _PublicAuthentication(),
            _return_response=True,
        )
        if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() not in {
            "application/json",
            "application/yaml",
            "text/yaml",
            "application/x-yaml",
        }:
            raise _failure("invalid_document_content_type")
        return OpenApiDescription.capture(endpoint, _parse(response.content))
    except ToolError:
        raise _failure("description_fetch_failed") from None


class _References:
    def __init__(self, document: dict[str, Any]):
        self.document = document
        self.count = 0

    def object(self, value: Any, seen: tuple[str, ...] = ()) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise _failure("invalid_reference_target")
        reference = value.get("$ref")
        if reference is None:
            return value
        if not isinstance(reference, str) or not reference.startswith("#/"):
            raise _failure("external_reference_disabled")
        if set(value) - {"$ref", "summary", "description"} or reference in seen:
            raise _failure("unsupported_reference")
        self.count += 1
        if self.count > MAX_LOCAL_REFERENCES:
            raise _failure("reference_bound")
        target: Any = self.document
        try:
            for part in reference[2:].split("/"):
                part = part.replace("~1", "/").replace("~0", "~")
                target = target[int(part)] if isinstance(target, list) else target[part]
        except (ValueError, KeyError, IndexError, TypeError):
            raise _failure("invalid_reference_target") from None
        return self.object(target, (*seen, reference))

    def schema(self, value: Any, version: str, seen: tuple[str, ...] = (), depth: int = 0) -> Any:
        if isinstance(value, bool) and version == "3.2.0":
            return value
        if depth > MAX_DEPTH or not isinstance(value, dict):
            raise _failure("unsupported_schema")
        reference = value.get("$ref")
        if reference is not None:
            if reference in seen:
                raise _failure("recursive_schema_unsupported")
            return self.schema(self.object(value), version, (*seen, reference), depth + 1)
        if set(value) - _SCHEMA_KEYS:
            raise _failure("unsupported_schema")
        if version == "3.0.3" and ("const" in value or isinstance(value.get("type"), list)):
            raise _failure("unsupported_schema")
        result = dict(value)
        if "nullable" in result:
            nullable = result.pop("nullable")
            if (
                version != "3.0.3"
                or type(nullable) is not bool
                or not isinstance(result.get("type"), str)
            ):
                raise _failure("unsupported_schema")
            if nullable:
                result["type"] = [result["type"], "null"]
        if version == "3.0.3":
            for side in ("Minimum", "Maximum"):
                key, bound = "exclusive" + side, side.lower()
                if key in result:
                    if type(result[key]) is not bool:
                        raise _failure("unsupported_schema")
                    exclusive = result.pop(key)
                    if exclusive:
                        if bound not in result:
                            raise _failure("unsupported_schema")
                        result[key] = result.pop(bound)
        for name in ("items", "additionalProperties"):
            if name in result and isinstance(result[name], dict):
                result[name] = self.schema(result[name], version, seen, depth + 1)
        if "properties" in result:
            if not isinstance(result["properties"], dict):
                raise _failure("unsupported_schema")
            result["properties"] = {
                name: self.schema(schema, version, seen, depth + 1)
                for name, schema in result["properties"].items()
            }
        try:
            Draft202012Validator.check_schema(result)
        except Exception:
            raise _failure("invalid_schema") from None
        return result


def _server(
    document: dict[str, Any], path: dict[str, Any], operation: dict[str, Any], base: str
) -> str:
    servers = operation.get("servers", path.get("servers", document.get("servers", [{"url": "/"}])))
    if not isinstance(servers, list) or not servers or not isinstance(servers[0], dict):
        raise _failure("invalid_server")
    selected = servers[0]
    url = selected.get("url")
    variables = selected.get("variables", {})
    if not isinstance(url, str) or not isinstance(variables, dict) or len(variables) > 32:
        raise _failure("invalid_server")
    for variable in set(re.findall(r"\{([^{}]+)\}", url)):
        definition = variables.get(variable)
        if not isinstance(definition, dict) or not isinstance(definition.get("default"), str):
            raise _failure("invalid_server_variable")
        value = definition["default"]
        if "enum" in definition and (
            not isinstance(definition["enum"], list) or value not in definition["enum"]
        ):
            raise _failure("invalid_server_variable")
        if any(char in value for char in "{}?#") or any(ord(char) < 33 for char in value):
            raise _failure("invalid_server_variable")
        url = url.replace("{" + variable + "}", value)
    if "{" in url or "}" in url:
        raise _failure("invalid_server_variable")
    _reject_dot_path(url)
    endpoint = _uri(urljoin(base, url))
    if urlparse(endpoint).query:
        raise _failure("invalid_server")
    return endpoint.rstrip("/")


def _parameters(
    refs: _References, path: dict[str, Any], operation: dict[str, Any], version: str
) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for owner in (path, operation):
        values = owner.get("parameters", [])
        if not isinstance(values, list) or len(values) > 64:
            raise _failure("invalid_parameters")
        unique: set[tuple[str, str]] = set()
        for raw in values:
            value = refs.object(raw)
            name, location = value.get("name"), value.get("in")
            if (
                not isinstance(name, str)
                or not name
                or location not in {"path", "query", "header", "cookie"}
            ):
                raise _failure("unsupported_parameter")
            key = (name, location)
            if key in unique:
                raise _failure("duplicate_parameter")
            unique.add(key)
            if (
                any(value.get(flag) for flag in ("allowReserved", "allowEmptyValue"))
                or "content" in value
            ):
                raise _failure("unsupported_parameter")
            style = value.get("style", "simple" if location in {"path", "header"} else "form")
            explode = value.get("explode", style == "form")
            required = value.get("required", False)
            if (
                style != ("simple" if location in {"path", "header"} else "form")
                or type(explode) is not bool
                or type(required) is not bool
            ):
                raise _failure("unsupported_parameter_style")
            if location == "path" and required is not True:
                raise _failure("invalid_path_parameter")
            if location == "header" and (
                not _HEADER.fullmatch(name)
                or name.lower() in _TRANSPORT_HEADERS
                or name.lower()
                in {"authorization", "content-type", "accept", "cookie", "proxy-authorization"}
            ):
                raise _failure("unsupported_header_parameter")
            if location == "cookie" and not _HEADER.fullmatch(name):
                raise _failure("unsupported_cookie_parameter")
            schema = refs.schema(value.get("schema", {}), version)
            merged[key] = {
                "name": name,
                "in": location,
                "required": required,
                "style": style,
                "explode": explode,
                "schema": schema,
            }
    result = list(merged.values())
    names = [value["name"] for value in result]
    headers = [name.lower() for name, location in merged if location == "header"]
    if len(set(names)) != len(names) or len(set(headers)) != len(headers):
        raise _failure("ambiguous_parameter_name")
    return result


def _security(
    refs: _References, document: dict[str, Any], operation: dict[str, Any]
) -> dict[str, Any]:
    requirements = operation.get("security", document.get("security", []))
    if not isinstance(requirements, list) or len(requirements) > 8:
        raise _failure("unsupported_security")
    components = document.get("components", {})
    if not isinstance(components, dict) or not isinstance(
        components.get("securitySchemes", {}), dict
    ):
        raise _failure("unsupported_security")
    schemes = components.get("securitySchemes", {})
    selected: dict[str, Any] = {}
    for requirement in requirements:
        if not isinstance(requirement, dict) or len(requirement) > 1:
            raise _failure("unsupported_security")
        for name, scopes in requirement.items():
            if (
                not isinstance(scopes, list)
                or any(not isinstance(scope, str) for scope in scopes)
                or name not in schemes
            ):
                raise _failure("unsupported_security")
            scheme = refs.object(schemes[name])
            kind = scheme.get("type")
            if kind != "oauth2" and scopes:
                raise _failure("unsupported_security")
            if kind == "http" and str(scheme.get("scheme", "")).lower() == "bearer":
                pass
            elif (
                kind == "apiKey"
                and scheme.get("in") == "header"
                and isinstance(scheme.get("name"), str)
                and _HEADER.fullmatch(scheme["name"])
            ):
                pass
            elif kind == "mutualTLS" and document["openapi"] == "3.2.0":
                pass
            elif (
                kind == "oauth2"
                and isinstance(scheme.get("flows"), dict)
                and set(scheme["flows"]) == {"clientCredentials"}
            ):
                flow = scheme["flows"]["clientCredentials"]
                if not isinstance(flow, dict):
                    raise _failure("unsupported_security")
                _uri(flow.get("tokenUrl"))
                if not isinstance(flow.get("scopes"), dict) or any(
                    scope not in flow["scopes"] for scope in scopes
                ):
                    raise _failure("unsupported_security")
            else:
                raise _failure("unsupported_security")
            selected[name] = scheme
    return {"requirements": requirements, "schemes": selected}


def _responses(refs: _References, operation: dict[str, Any], version: str) -> dict[str, Any]:
    values = operation.get("responses")
    if not isinstance(values, dict) or not values or len(values) > 32:
        raise _failure("invalid_response_definition")
    result = {}
    for status, raw in values.items():
        if status != "default" and re.fullmatch(r"[1-5](?:[0-9]{2}|XX)", status) is None:
            raise _failure("invalid_response_definition")
        response = refs.object(raw)
        content = response.get("content", {})
        if (
            not isinstance(response.get("description"), str)
            or not isinstance(content, dict)
            or len(content) > 8
        ):
            raise _failure("invalid_response_definition")
        media = {}
        for name, definition in content.items():
            if (
                not isinstance(definition, dict)
                or "encoding" in definition
                or "itemSchema" in definition
            ):
                raise _failure("unsupported_response_codec")
            if name not in {"application/json", "text/plain", "application/octet-stream"}:
                raise _failure("unsupported_response_codec")
            schema = refs.schema(definition["schema"], version) if "schema" in definition else None
            if schema is not None and name != "application/json":
                raise _failure("unsupported_response_codec")
            media[name] = schema
        result[status] = media
    return result


@dataclass(frozen=True, slots=True)
class OpenApiOperationSelection:
    description: OpenApiDescription
    operation_id: str
    version: str
    method: str
    endpoint: str
    path: str
    parameters_json: str
    security_json: str
    credential_profile: str | None = None
    remaining_timeout: float | None = field(default=None, compare=False, repr=False)

    def as_value(self) -> dict[str, Any]:
        return {
            "profile": _PROFILE,
            "document_uri": self.description.uri,
            "document_json": self.description.document_json,
            "document_digest": self.description.digest,
            "operation_id": self.operation_id,
            "version": self.version,
            "method": self.method,
            "endpoint": self.endpoint,
            "path": self.path,
            "parameters_json": self.parameters_json,
            "security_json": self.security_json,
            "credential_profile": self.credential_profile,
        }

    @classmethod
    def from_value(cls, value: Any) -> OpenApiOperationSelection:
        fields = {
            "profile",
            "document_uri",
            "document_json",
            "document_digest",
            "operation_id",
            "version",
            "method",
            "endpoint",
            "path",
            "parameters_json",
            "security_json",
            "credential_profile",
        }
        if not isinstance(value, dict) or set(value) != fields or value["profile"] != _PROFILE:
            raise _failure("invalid_selection")
        if any(not isinstance(value[name], str) for name in fields - {"credential_profile"}) or (
            value["credential_profile"] is not None
            and not isinstance(value["credential_profile"], str)
        ):
            raise _failure("invalid_selection")
        description = OpenApiDescription(
            value["document_uri"], value["document_json"], value["document_digest"]
        )
        selected = select_operation(
            description, value["operation_id"], credential_profile=value["credential_profile"]
        )
        if selected.as_value() != value:
            raise _failure("selection_changed")
        return selected


def select_operation(
    description: OpenApiDescription, operation_id: str, *, credential_profile: str | None = None
) -> OpenApiOperationSelection:
    document = description.document()
    if not isinstance(operation_id, str) or not operation_id or len(operation_id) > 256:
        raise _failure("invalid_operation_id")
    refs = _References(document)
    operations: dict[str, tuple[str, str, dict[str, Any], dict[str, Any]]] = {}
    for path, raw in document["paths"].items():
        if not isinstance(path, str) or not path.startswith("/") or "?" in path or "#" in path:
            raise _failure("invalid_path")
        item = refs.object(raw)
        for method, operation in item.items():
            if method not in _METHODS:
                continue
            if not isinstance(operation, dict):
                raise _failure("invalid_operation")
            name = operation.get("operationId")
            if name is not None:
                if not isinstance(name, str) or not name or name in operations:
                    raise _failure("duplicate_operation_id")
                operations[name] = (path, method, item, operation)
    if operation_id not in operations:
        raise _failure("operation_not_found")
    path, method, item, operation = operations[operation_id]
    if "requestBody" in operation:
        raise _failure("request_body_mapping_unqualified")
    base = description.uri
    if "$self" in document:
        if document["openapi"] != "3.2.0" or not isinstance(document["$self"], str):
            raise _failure("unsupported_base_uri")
        _reject_dot_path(document["$self"])
        base = _uri(urljoin(base, document["$self"]))
    server = _server(document, item, operation, base)
    parameters = _parameters(refs, item, operation, document["openapi"])
    path_names = set(re.findall(r"\{([^{}]+)\}", path))
    if any(char in re.sub(r"\{[^{}]+\}", "", path) for char in "{}"):
        raise _failure("invalid_path")
    if path_names != {value["name"] for value in parameters if value["in"] == "path"}:
        raise _failure("invalid_path_parameter")
    security = _security(refs, document, operation)
    _responses(refs, operation, document["openapi"])
    return OpenApiOperationSelection(
        description,
        operation_id,
        document["openapi"],
        method.upper(),
        _uri(server + path),
        path,
        _json(parameters),
        _json(security),
        credential_profile,
    )


@dataclass(frozen=True, slots=True)
class OpenApiRequest:
    method: str
    endpoint: str
    query: tuple[tuple[str, str], ...]
    headers: tuple[tuple[str, str], ...]


def _scalar(value: Any) -> str:
    if value is None:
        return ""
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) in {str, int, float}:
        return str(value)
    raise _failure("unsupported_parameter_value")


def _parts(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_scalar(item) for item in value]
    if isinstance(value, dict):
        return [part for name, item in value.items() for part in (name, _scalar(item))]
    return [_scalar(value)]


def prepare_request(selection: OpenApiOperationSelection, parameters: Any) -> OpenApiRequest:
    verified = OpenApiOperationSelection.from_value(selection.as_value())
    parameters = {} if parameters is None else parameters
    if not isinstance(parameters, dict):
        raise _failure("invalid_parameters")
    _finite(parameters)
    definitions = json.loads(verified.parameters_json)
    if set(parameters) - {value["name"] for value in definitions}:
        raise _failure("unknown_parameter")
    endpoint = verified.endpoint
    query: list[tuple[str, str]] = []
    headers: list[tuple[str, str]] = []
    cookies: list[str] = []
    for definition in definitions:
        name = definition["name"]
        if name not in parameters:
            if definition["required"]:
                raise _failure("required_parameter_missing")
            continue
        value = parameters[name]
        try:
            Draft202012Validator(definition["schema"]).validate(value)
        except Exception:
            raise _failure("parameter_schema_failed") from None
        location, explode = definition["in"], definition["explode"]
        parts = _parts(value)
        if location == "path":
            if isinstance(value, dict) and explode:
                encoded = ",".join(
                    quote(key, safe="") + "=" + quote(_scalar(item), safe="")
                    for key, item in value.items()
                )
            else:
                encoded = ",".join(quote(part, safe="") for part in parts)
            endpoint = endpoint.replace("{" + name + "}", encoded)
        elif location == "query":
            if isinstance(value, list) and explode:
                query.extend((name, item) for item in parts)
            elif isinstance(value, dict) and explode:
                query.extend((key, _scalar(item)) for key, item in value.items())
            else:
                query.append((name, ",".join(parts)))
        elif location == "header":
            if isinstance(value, dict) and explode:
                parts = [name + "=" + _scalar(item) for name, item in value.items()]
            encoded = ",".join(parts)
            if any(ord(char) < 32 or ord(char) > 126 for char in encoded):
                raise _failure("invalid_header_value")
            headers.append((name, encoded))
        else:
            if isinstance(value, (list, dict)):
                raise _failure("cookie_collection_unqualified")
            cookies.append(name + "=" + quote(_scalar(value), safe=""))
    if cookies:
        headers.append(("Cookie", "; ".join(cookies)))
    return OpenApiRequest(verified.method, _uri(endpoint), tuple(query), tuple(headers))


def _authorize(
    selection: OpenApiOperationSelection,
    endpoint: str,
    authentication: AuthenticationProvider | None,
    grants: Mapping[str, tuple[str, ...]] | None,
    bound_endpoint: str | None,
) -> frozenset[str]:
    if not isinstance(authentication, (ProfileAuthentication, type(None))):
        raise _failure("unsupported_authentication_binding")
    if selection.credential_profile != getattr(authentication, "profile_name", None):
        raise _failure("credential_binding_changed")
    policy = json.loads(selection.security_json)
    requirements = policy["requirements"]
    if authentication is None:
        if requirements and {} not in requirements:
            raise _failure("credential_required")
        return frozenset()
    if urlparse(endpoint).scheme != "https":
        raise _failure("credential_endpoint_requires_https")
    profile = authentication.security.profile(authentication.profile_name)
    if isinstance(profile, OAuth2ClientCredentialsSecurityProfile):
        if endpoint not in profile.allowed_resource_endpoints:
            raise _failure("credential_endpoint_not_granted")
    elif endpoint != bound_endpoint and endpoint not in (grants or {}).get(
        authentication.profile_name, ()
    ):
        raise _failure("credential_endpoint_not_granted")
    satisfiable = not requirements
    for requirement in requirements:
        if not requirement:
            satisfiable = True
        for name, scopes in requirement.items():
            scheme = policy["schemes"][name]
            kind = scheme["type"]
            if (
                isinstance(profile, BearerSecurityProfile)
                and kind == "http"
                and scheme["scheme"].lower() == "bearer"
                and not scopes
            ):
                satisfiable = True
            elif (
                isinstance(profile, ApiKeySecurityProfile)
                and kind == "apiKey"
                and scheme["name"].lower() == profile.header.lower()
                and not scopes
            ):
                satisfiable = True
            elif isinstance(profile, MtlsSecurityProfile) and kind == "mutualTLS" and not scopes:
                satisfiable = True
            elif (
                isinstance(profile, OAuth2ClientCredentialsSecurityProfile)
                and kind == "oauth2"
                and scheme["flows"]["clientCredentials"]["tokenUrl"] == profile.token_url
                and set(scopes) <= set(profile.scopes)
            ):
                satisfiable = True
    if not satisfiable:
        raise _failure("security_requirement_unsatisfied")
    return (
        frozenset({profile.header.lower()})
        if isinstance(profile, ApiKeySecurityProfile)
        else frozenset()
    )


def validate_operation_authority(
    selection: OpenApiOperationSelection,
    parameters: Any,
    *,
    authentication: AuthenticationProvider | None,
    credential_endpoints: Mapping[str, tuple[str, ...]] | None,
    bound_endpoint: str | None = None,
) -> None:
    """Check the selected actual route/security without resolving credentials."""
    verified = OpenApiOperationSelection.from_value(selection.as_value())
    request = prepare_request(verified, parameters)
    if bound_endpoint is not None and request.endpoint != bound_endpoint:
        raise _failure("configured_endpoint_changed")
    _authorize(verified, request.endpoint, authentication, credential_endpoints, bound_endpoint)


async def call_openapi(
    http: HttpClient,
    payload: Mapping[str, Any],
    *,
    selection: OpenApiOperationSelection,
    timeout: float,
    operation_id: str | None = None,
    authentication: AuthenticationProvider | None = None,
    credential_endpoints: Mapping[str, tuple[str, ...]] | None = None,
    bound_endpoint: str | None = None,
    _on_dispatch: Callable[[], None] | None = None,
) -> Any:
    from .protocols import OUTPUT_MODES, _format_response

    verified = OpenApiOperationSelection.from_value(selection.as_value())
    if http.verify_tls is False:
        raise _failure("insecure_transport")
    if authentication is None and (
        http.client_cert is not None
        or http.authentication is not None
        or type(http.verify_tls) not in {bool, str}
    ):
        raise _failure("undeclared_transport_identity")
    if payload.get("operationId") != verified.operation_id:
        raise _failure("operation_binding_changed")
    request = prepare_request(verified, payload.get("parameters"))
    if bound_endpoint is not None and bound_endpoint != request.endpoint:
        raise _failure("configured_endpoint_changed")
    sensitive = _authorize(
        verified, request.endpoint, authentication, credential_endpoints, bound_endpoint
    )
    output, redirect = payload.get("output", "content"), payload.get("redirect", False)
    if output not in OUTPUT_MODES or type(redirect) is not bool:
        raise _failure("invalid_projection")
    timeout = min(timeout, http.timeout)
    if selection.remaining_timeout is not None:
        timeout = min(timeout, selection.remaining_timeout)
    if not math.isfinite(timeout) or timeout <= 0:
        raise _failure("timeout")
    dispatched = False

    def sent() -> None:
        nonlocal dispatched
        dispatched = True
        if _on_dispatch is not None:
            _on_dispatch()

    try:
        async with asyncio.timeout(timeout):
            response = await http.request(
                request.method,
                request.endpoint,
                headers=dict(request.headers),
                params=list(request.query),
                operation_id=operation_id,
                request_timeout=timeout,
                follow_redirects=False,
                _allow_redirect_status=redirect,
                _authentication=authentication or _PublicAuthentication(),
                _return_response=True,
                _on_dispatch=sent,
            )
        document = verified.description.document()
        refs = _References(document)
        declared_operation = refs.object(document["paths"][verified.path])[verified.method.lower()]
        responses = _responses(refs, declared_operation, verified.version)
        media = responses.get(
            str(response.status_code),
            responses.get(str(response.status_code // 100) + "XX", responses.get("default")),
        )
        if media:
            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if content_type not in media:
                raise _uncertain("undeclared_response_media")
            if media[content_type] is not None:
                try:
                    body = json.loads(response.content, object_pairs_hook=_unique)
                    _finite(body)
                    Draft202012Validator(media[content_type]).validate(body)
                except Exception:
                    raise _uncertain("response_schema_failed") from None
        return _format_response(response, output, _sensitive_headers=sensitive)
    except asyncio.CancelledError:
        raise
    except ToolError as error:
        if dispatched and "status" not in error.details:
            raise _uncertain("transport_or_response_failure") from None
        raise
    except _OpenApiOutcomeUncertain:
        raise
    except Exception:
        if dispatched:
            raise _uncertain("transport_or_response_failure") from None
        raise _failure("transport_setup_failed") from None
