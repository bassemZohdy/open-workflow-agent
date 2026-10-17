"""Pure, finite AsyncAPI selection; no broker, credential or delivery I/O.

Private descriptors retain the original description and do not enable a transport.
Broker admission, network authority, durable inbox/acknowledgment and native
subscription execution are independent deployment/runtime gates.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, Literal, cast
from urllib.parse import urlparse

import yaml
from jsonschema import Draft7Validator

from .errors import OwaError, ToolError
from .expressions import validate_expression_data

VERSIONS = frozenset({"2.6.0", "3.0.0", "3.1.0"})
MAX_DOCUMENT_BYTES = 1_048_576
MAX_DOCUMENT_NODES = 10_000
MAX_DEPTH = 32
MAX_LOCAL_REFERENCES = 64
MAX_MESSAGES = 16
_PROFILE = "owa/asyncapi-operation/v1"
_ANNOTATIONS = {"title", "description", "default", "examples", "readOnly", "writeOnly"}
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
}
_VARIABLE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_.-]*)\}")
_HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "CONNECT", "TRACE"}
Mode = Literal["publish", "subscribe"]


def _failure(reason: str) -> ToolError:
    return ToolError("AsyncAPI description or selection is unavailable", details={"reason": reason})


def _finite(value: Any) -> None:
    try:
        validate_expression_data(value)
    except (OwaError, RecursionError):
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


def _json(value: Any, *, ordered: bool = False) -> str:
    _finite(value)
    try:
        encoded = json.dumps(
            value, sort_keys=not ordered, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
        size = len(encoded.encode("utf-8"))
    except (ValueError, UnicodeError, RecursionError):
        raise _failure("invalid_finite_value") from None
    if size > MAX_DOCUMENT_BYTES:
        raise _failure("document_bound")
    return encoded


def _unique(pairs: list[tuple[Any, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in result:
            raise _failure("duplicate_or_nonstring_key")
        result[key] = value
    return result


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


def _object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _failure("invalid_object")
    return value


def _text(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 4096
        or any(ord(c) < 32 for c in value)
    ):
        raise _failure("invalid_string")
    return value


def _uri(value: Any) -> str:
    value = _text(value)
    if any(ord(character) < 33 for character in value) or "\\" in value:
        raise _failure("invalid_document_uri")
    try:
        parsed = urlparse(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or (parsed.password or parsed.fragment)
            or (parsed.port is not None and not 1 <= parsed.port <= 65535)
        ):
            raise ValueError
    except ValueError:
        raise _failure("invalid_document_uri") from None
    return cast(str, value)


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
        value = _object(value)
        _finite(value)
        return value
    except (ValueError, UnicodeError, yaml.YAMLError, RecursionError):
        raise _failure("invalid_document") from None


@dataclass(frozen=True, slots=True)
class AsyncApiDescription:
    uri: str
    document_json: str
    digest: str

    @classmethod
    def capture(cls, uri: str, document: dict[str, Any]) -> AsyncApiDescription:
        _uri(uri)
        _object(document)
        _finite(document)
        if not isinstance(document.get("asyncapi"), str) or document["asyncapi"] not in VERSIONS:
            raise _failure("unsupported_version")
        info = _object(document.get("info"))
        _text(info.get("title"))
        _text(info.get("version"))
        # OWS selects the first eligible server. Preserve source object order.
        encoded = _json(document, ordered=True)
        return cls(uri, encoded, hashlib.sha256(encoded.encode()).hexdigest())

    @classmethod
    def parse(cls, uri: str, raw: bytes) -> AsyncApiDescription:
        return cls.capture(uri, _parse(raw))

    def document(self) -> dict[str, Any]:
        if not isinstance(self.document_json, str) or not isinstance(self.digest, str):
            raise _failure("description_changed")
        try:
            raw = self.document_json.encode("utf-8")
        except UnicodeError:
            raise _failure("description_changed") from None
        value = self.parse(self.uri, raw)
        if value != self:
            raise _failure("description_changed")
        return _parse(raw)


def _pointer(value: Any, pointer: str) -> Any:
    if pointer and not pointer.startswith("/"):
        raise _failure("invalid_pointer")
    for part in pointer.split("/")[1:] if pointer else []:
        if re.search(r"~(?![01])", part):
            raise _failure("invalid_pointer")
        part = part.replace("~1", "/").replace("~0", "~")
        try:
            if isinstance(value, list):
                if not re.fullmatch(r"0|[1-9][0-9]*", part):
                    raise ValueError
                value = value[int(part)]
            else:
                value = value[part]
        except (KeyError, TypeError, IndexError, ValueError):
            raise _failure("invalid_pointer") from None
    return value


class _References:
    def __init__(self, document: dict[str, Any]):
        self.document = document
        self.count = 0

    def object(self, value: Any, seen: tuple[str, ...] = ()) -> dict[str, Any]:
        value = _object(value)
        if "$ref" not in value:
            return cast(dict[str, Any], value)
        reference = value["$ref"]
        if not isinstance(reference, str) or not reference.startswith("#/"):
            raise _failure("external_reference_disabled")
        if set(value) != {"$ref"} or reference in seen:
            raise _failure("unsupported_reference")
        self.count += 1
        if self.count > MAX_LOCAL_REFERENCES:
            raise _failure("reference_bound")
        return self.object(_pointer(self.document, reference[1:]), (*seen, reference))

    def schema(self, value: Any, seen: tuple[str, ...] = (), depth: int = 0) -> Any:
        if type(value) is bool:
            return value
        if depth > MAX_DEPTH:
            raise _failure("schema_bound")
        value = _object(value)
        if "$ref" in value:
            reference = value["$ref"]
            if reference in seen:
                raise _failure("recursive_schema_unsupported")
            return self.schema(self.object(value), (*seen, reference), depth + 1)
        if set(value) - _SCHEMA_KEYS:
            raise _failure("unsupported_schema")
        result = dict(value)
        for key in ("items", "additionalProperties"):
            if key in result:
                result[key] = self.schema(result[key], seen, depth + 1)
        if "properties" in result:
            result["properties"] = {
                name: self.schema(item, seen, depth + 1)
                for name, item in _object(result["properties"]).items()
            }
        try:
            Draft7Validator.check_schema(result)
        except Exception:
            raise _failure("invalid_schema") from None
        return result


def _substitute(
    template: str, declarations: Any, supplied: dict[str, Any], refs: _References
) -> str:
    declarations = _object(declarations)
    names = set(_VARIABLE.findall(template))
    if set(supplied) - names or names - set(declarations):
        raise _failure("unknown_variable")
    values: dict[str, str] = {}
    for name in names:
        declaration = refs.object(declarations[name])
        value = _text(supplied.get(name, declaration.get("default")))
        if any(char in value for char in "{}@/?#\\") or any(char.isspace() for char in value):
            raise _failure("unsafe_variable")
        enum = declaration.get("enum")
        if enum is not None and (
            not isinstance(enum, list)
            or not enum
            or any(not isinstance(item, str) for item in enum)
            or value not in enum
        ):
            raise _failure("variable_enum")
        values[name] = value
    expanded = _VARIABLE.sub(lambda match: values[match[1]], template)
    if "{" in expanded or "}" in expanded:
        raise _failure("unsupported_template")
    return expanded


def _server(
    document: dict[str, Any],
    channel: dict[str, Any],
    configured: Any,
    protocol: Any,
    refs: _References,
) -> tuple[str, dict[str, Any]]:
    servers = _object(document.get("servers"))
    if not servers or len(servers) > 64:
        raise _failure("server_bound")
    eligible = list(servers)
    if channel.get("servers"):
        listed = channel["servers"]
        if not isinstance(listed, list) or len(listed) > 64:
            raise _failure("invalid_servers")
        if document["asyncapi"] == "2.6.0":
            eligible = [_text(item) for item in listed]
        else:
            eligible = []
            for item in listed:
                item = _object(item)
                reference = item.get("$ref")
                if (
                    set(item) != {"$ref"}
                    or not isinstance(reference, str)
                    or not re.fullmatch(r"#/servers/[^/]+", reference)
                ):
                    raise _failure("invalid_server_reference")
                eligible.append(reference[10:].replace("~1", "/").replace("~0", "~"))
        if len(set(eligible)) != len(eligible) or any(name not in servers for name in eligible):
            raise _failure("invalid_servers")
        # The specification's server list is unordered; OWS first-server choice
        # follows the original root declaration order amongst eligible names.
        eligible = [name for name in servers if name in eligible]
    variables: dict[str, Any] = {}
    if configured is not None:
        configured = _object(configured)
        if set(configured) - {"name", "variables"}:
            raise _failure("invalid_server_selector")
        name = _text(configured.get("name"))
        variables = _object(configured.get("variables", {}))
        if name not in eligible:
            raise _failure("server_not_on_channel")
    else:
        candidates = [
            name
            for name in eligible
            if protocol is None or refs.object(servers[name]).get("protocol") == protocol
        ]
        if not candidates:
            raise _failure("server_not_found")
        name = candidates[0]
    server = refs.object(servers[name])
    selected_protocol = server.get("protocol")
    if not isinstance(selected_protocol, str) or selected_protocol not in {
        "http",
        "https",
        "kafka",
        "kafka-secure",
    }:
        raise _failure("unsupported_protocol")
    declarations = server.get("variables", {})
    if document["asyncapi"] == "2.6.0":
        endpoint = _substitute(_text(server.get("url")), declarations, variables, refs)
        if "://" not in endpoint:
            endpoint = (
                (
                    "https"
                    if selected_protocol == "https"
                    else "kafka"
                    if selected_protocol == "kafka-secure"
                    else selected_protocol
                )
                + "://"
                + endpoint
            )
    else:
        host = _text(server.get("host"))
        path = server.get("pathname", "")
        if not isinstance(path, str) or path and not path.startswith("/"):
            raise _failure("invalid_server_path")
        endpoint = _substitute(host + path, declarations, variables, refs)
        endpoint = (
            (
                "https"
                if selected_protocol == "https"
                else "kafka"
                if selected_protocol == "kafka-secure"
                else selected_protocol
            )
            + "://"
            + endpoint
        )
    try:
        parsed = urlparse(endpoint)
        if any(character.isspace() for character in endpoint) or "\\" in endpoint:
            raise ValueError
        if (
            not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError
        if parsed.scheme not in {"http", "https", "kafka"} or (
            parsed.port is not None and not 1 <= parsed.port <= 65535
        ):
            raise ValueError
        schemes = {
            "http": {"http", "https"},
            "https": {"https"},
            "kafka": {"kafka"},
            "kafka-secure": {"kafka"},
        }
        if parsed.scheme not in schemes[selected_protocol]:
            raise ValueError
    except ValueError:
        raise _failure("invalid_server_endpoint") from None
    return name, {
        "protocol": selected_protocol,
        "endpoint": endpoint,
        "protocolVersion": server.get("protocolVersion"),
        "source": server,
    }


def _security(value: Any, refs: _References, version: str) -> list[Any]:
    if not isinstance(value, list) or len(value) > 8:
        raise _failure("security_bound")
    alternatives: list[Any] = []
    for item in value:
        schemes: list[dict[str, Any]]
        if version == "2.6.0":
            requirement = _object(item)
            if not requirement:
                alternatives.append([])
                continue
            schemes = []
            for name, scopes in requirement.items():
                if (
                    not isinstance(scopes, list)
                    or len(scopes) > 32
                    or any(not isinstance(scope, str) for scope in scopes)
                ):
                    raise _failure("invalid_security_scopes")
                components = _object(refs.document.get("components", {}))
                source = _object(components.get("securitySchemes", {})).get(name)
                schemes.append({"name": name, "scopes": scopes, "scheme": refs.object(source)})
        else:
            schemes = [{"scheme": refs.object(item)}]
        for entry in schemes:
            scheme = entry["scheme"]
            if not isinstance(scheme.get("type"), str) or scheme["type"] not in {
                "userPassword",
                "apiKey",
                "X509",
                "httpApiKey",
                "http",
                "oauth2",
                "scramSha256",
                "scramSha512",
            }:
                raise _failure("unsupported_security_scheme")
            if set(scheme) - {
                "type",
                "description",
                "name",
                "in",
                "scheme",
                "bearerFormat",
                "flows",
                "scopes",
            }:
                raise _failure("unsupported_security_fields")
            if scheme["type"] == "oauth2":
                flows = _object(scheme.get("flows"))
                if set(flows) != {"clientCredentials"}:
                    raise _failure("unsupported_oauth_flow")
                flow = _object(flows["clientCredentials"])
                if set(flow) - {"tokenUrl", "refreshUrl", "scopes", "availableScopes"}:
                    raise _failure("unsupported_oauth_flow")
                if urlparse(_uri(flow.get("tokenUrl"))).scheme != "https":
                    raise _failure("invalid_oauth_endpoint")
                if "refreshUrl" in flow and urlparse(_uri(flow["refreshUrl"])).scheme != "https":
                    raise _failure("invalid_oauth_endpoint")
                for key in ("scopes", "availableScopes"):
                    if key in flow and (
                        not isinstance(flow[key], dict)
                        or len(flow[key]) > 64
                        or any(
                            not isinstance(description, str) for description in flow[key].values()
                        )
                    ):
                        raise _failure("invalid_security_scopes")
            if scheme["type"] in {"apiKey", "httpApiKey"}:
                allowed_locations = (
                    {"user", "password"}
                    if scheme["type"] == "apiKey"
                    else {"header", "query", "cookie"}
                )
                if not isinstance(scheme.get("in"), str) or scheme["in"] not in allowed_locations:
                    raise _failure("invalid_security_location")
                if scheme["type"] == "httpApiKey":
                    _text(scheme.get("name"))
            if scheme["type"] == "http" and (
                not isinstance(scheme.get("scheme"), str)
                or scheme["scheme"] not in {"bearer", "basic"}
            ):
                raise _failure("unsupported_http_security_scheme")
        alternatives.append(schemes)
    return alternatives


def _bindings(value: Any, scope: str, refs: _References) -> dict[str, Any]:
    value = refs.object(value)
    if set(value) - {"http", "kafka"}:
        raise _failure("unsupported_binding")
    result: dict[str, Any] = {}
    for protocol, raw in value.items():
        binding = dict(refs.object(raw))
        version = "0.3.0" if protocol == "http" else "0.5.0"
        if binding.get("bindingVersion", version) != version:
            raise _failure("unsupported_binding_version")
        allowed = {"bindingVersion"}
        if protocol == "http":
            if scope in {"server", "channel"} and binding:
                raise _failure("unsupported_http_binding")
            allowed |= {"method", "query"} if scope == "operation" else {"headers", "statusCode"}
            if "method" in binding and (
                not isinstance(binding["method"], str) or binding["method"] not in _HTTP_METHODS
            ):
                raise _failure("invalid_http_method")
            if "statusCode" in binding and (
                type(binding["statusCode"]) is not int or not 100 <= binding["statusCode"] <= 599
            ):
                raise _failure("invalid_http_status")
            for name in ("query", "headers"):
                if name in binding:
                    schema = refs.schema(binding[name])
                    if (
                        not isinstance(schema, dict)
                        or schema.get("type") != "object"
                        or ("properties" not in schema)
                    ):
                        raise _failure("invalid_http_binding_schema")
                    binding[name] = schema
        else:
            allowed |= (
                {"topic", "partitions", "replicas", "topicConfiguration"}
                if scope == "channel"
                else (
                    {"groupId", "clientId"}
                    if scope == "operation"
                    else {"key"}
                    if scope == "message"
                    else set()
                )
            )
            for name in ("partitions", "replicas"):
                if name in binding and (type(binding[name]) is not int or binding[name] < 1):
                    raise _failure("invalid_topic_shape")
            if "topic" in binding and not re.fullmatch(
                r"[A-Za-z0-9_.-]{1,249}", _text(binding["topic"])
            ):
                raise _failure("invalid_topic")
            if "topicConfiguration" in binding:
                configuration = _object(binding["topicConfiguration"])
                if set(configuration) - {
                    "cleanup.policy",
                    "retention.ms",
                    "retention.bytes",
                    "delete.retention.ms",
                    "max.message.bytes",
                }:
                    raise _failure("unsupported_topic_configuration")
                for key, item in configuration.items():
                    if key == "cleanup.policy":
                        if (
                            not isinstance(item, list)
                            or not item
                            or any(policy not in {"compact", "delete"} for policy in item)
                        ):
                            raise _failure("invalid_topic_configuration")
                    elif type(item) is not int or item < -1:
                        raise _failure("invalid_topic_configuration")
            for name in ("groupId", "clientId", "key"):
                if name in binding:
                    binding[name] = refs.schema(binding[name])
        if set(binding) - allowed:
            raise _failure("unsupported_binding_fields")
        # This is a declared description, never an admin/client configuration.
        result[protocol] = dict(binding)
    return result


def _message(value: Any, refs: _References, version: str) -> dict[str, Any]:
    value = refs.object(value)
    if set(value) - {
        "name",
        "messageId",
        "title",
        "summary",
        "description",
        "contentType",
        "schemaFormat",
        "headers",
        "payload",
        "correlationId",
        "bindings",
        "examples",
    }:
        raise _failure("unsupported_message_feature")
    content_type = value.get(
        "contentType", refs.document.get("defaultContentType", "application/json")
    )
    if content_type != "application/json":
        raise _failure("unsupported_message_codec")
    payload = value.get("payload", True)
    format_name = value.get("schemaFormat")
    if version != "2.6.0" and isinstance(payload, dict) and "schemaFormat" in payload:
        if set(payload) != {"schemaFormat", "schema"}:
            raise _failure("unsupported_schema_format")
        format_name, payload = payload["schemaFormat"], payload["schema"]
    if format_name is not None and format_name not in {
        "application/schema+json;version=draft-07",
        "application/schema+yaml;version=draft-07",
        f"application/vnd.aai.asyncapi;version={version}",
        f"application/vnd.aai.asyncapi+json;version={version}",
        f"application/vnd.aai.asyncapi+yaml;version={version}",
    }:
        raise _failure("unsupported_schema_format")
    headers = refs.schema(value.get("headers", {"type": "object"}))
    if not isinstance(headers, dict) or headers.get("type") != "object":
        raise _failure("invalid_headers_schema")
    correlation = None
    if "correlationId" in value:
        correlation = refs.object(value["correlationId"]).get("location")
        if not isinstance(correlation, str) or not re.fullmatch(
            r"\$message\.(?:header|payload)(?:#(?:/[^\s]*)?)?", correlation
        ):
            raise _failure("unsupported_correlation")
        if "#" in correlation and re.search(r"~(?![01])", correlation.split("#", 1)[1]):
            raise _failure("invalid_pointer")
    return {
        "payload_schema": refs.schema(payload),
        "headers_schema": headers,
        "correlation": correlation,
        "bindings": _bindings(value.get("bindings", {}), "message", refs),
    }


@dataclass(frozen=True, slots=True)
class AsyncApiOperationSelection:
    document_uri: str
    document_json: str
    document_digest: str
    selector_json: str
    version: str
    action: str
    channel_reference: str
    address: str
    server_name: str
    server_json: str
    messages_json: str
    bindings_json: str
    security_json: str

    def as_value(self) -> dict[str, Any]:
        return {"profile": _PROFILE, **asdict(self)}

    @classmethod
    def from_value(cls, value: Any) -> AsyncApiOperationSelection:
        value = _object(value)
        if set(value) != {"profile", *cls.__dataclass_fields__} or value.get("profile") != _PROFILE:
            raise _failure("invalid_selection")
        if any(not isinstance(value[key], str) for key in cls.__dataclass_fields__):
            raise _failure("invalid_selection")
        try:
            if any(
                len(value[key].encode("utf-8")) > MAX_DOCUMENT_BYTES
                for key in cls.__dataclass_fields__
            ):
                raise _failure("selection_bound")
            selector = _object(json.loads(value["selector_json"], object_pairs_hook=_unique))
            selected = select_operation(
                AsyncApiDescription(
                    value["document_uri"], value["document_json"], value["document_digest"]
                ),
                **selector,
            )
            if selected.as_value() != value:
                raise _failure("selection_changed")
            return selected
        except (TypeError, ValueError, UnicodeError, RecursionError):
            raise _failure("invalid_selection") from None


def select_operation(
    description: AsyncApiDescription,
    *,
    operation: str | None = None,
    channel: str | None = None,
    mode: Mode,
    server: dict[str, Any] | None = None,
    protocol: str | None = None,
) -> AsyncApiOperationSelection:
    document = description.document()
    refs = _References(document)
    version = document["asyncapi"]
    raw_messages: Any
    action: str
    if not isinstance(mode, str) or mode not in {"publish", "subscribe"}:
        raise _failure("unsupported_action")
    if protocol is not None and not isinstance(protocol, str):
        raise _failure("invalid_protocol_selector")
    if version == "2.6.0":
        if operation is not None or channel is None:
            raise _failure("version_selector_mismatch")
        channel_id = _text(channel)
        selected_channel = refs.object(_object(document.get("channels")).get(channel_id))
        selected_operation = refs.object(selected_channel.get(mode))
        action = mode
        raw_messages = refs.object(selected_operation.get("message"))
        if "oneOf" in raw_messages:
            if set(raw_messages) != {"oneOf"}:
                raise _failure("unsupported_message_feature")
            raw_messages = raw_messages["oneOf"]
        else:
            raw_messages = [raw_messages]
        address = channel_id
    else:
        if channel is not None or operation is None:
            raise _failure("version_selector_mismatch")
        operation_name = _text(operation)
        selected_operation = refs.object(_object(document.get("operations")).get(operation_name))
        action = _text(selected_operation.get("action"))
        if action != ("send" if mode == "publish" else "receive"):
            raise _failure("operation_direction_mismatch")
        reference = _object(selected_operation.get("channel"))
        if (
            set(reference) != {"$ref"}
            or not isinstance(reference["$ref"], str)
            or not re.fullmatch(r"#/channels/[^/]+", reference["$ref"])
        ):
            raise _failure("invalid_channel_reference")
        channel_id = reference["$ref"][11:].replace("~1", "/").replace("~0", "~")
        selected_channel = refs.object(reference)
        available = _object(selected_channel.get("messages"))
        raw_messages = selected_operation.get("messages")
        if raw_messages is None:
            raw_messages = list(available.values())
        elif isinstance(raw_messages, list):
            for message in raw_messages:
                message = _object(message)
                prefix = reference["$ref"] + "/messages/"
                if (
                    set(message) != {"$ref"}
                    or not isinstance(message["$ref"], str)
                    or not (
                        message["$ref"].startswith(prefix)
                        and "/" not in message["$ref"][len(prefix) :]
                    )
                ):
                    raise _failure("message_not_on_channel")
        address = _text(selected_channel.get("address"))
    if selected_operation.get("traits") or "reply" in selected_operation:
        raise _failure("unsupported_operation_feature")
    allowed_operation = {
        "operationId",
        "title",
        "summary",
        "description",
        "action",
        "channel",
        "message",
        "messages",
        "bindings",
        "security",
        "tags",
        "externalDocs",
    }
    if set(selected_operation) - allowed_operation:
        raise _failure("unsupported_operation_feature")
    if not isinstance(raw_messages, list) or not 1 <= len(raw_messages) <= MAX_MESSAGES:
        raise _failure("message_bound")
    parameters = selected_channel.get("parameters", {})
    if "{" in address or "}" in address:
        address = _substitute(address, parameters, {}, refs)
    elif parameters:
        raise _failure("unused_channel_parameters")
    _text(address)
    if "?" in address or "#" in address or any(char.isspace() for char in address):
        raise _failure("invalid_channel_address")
    name, selected_server = _server(document, selected_channel, server, protocol, refs)
    bindings = {
        scope: _bindings(value.get("bindings", {}), scope, refs)
        for scope, value in (
            ("server", selected_server["source"]),
            ("channel", selected_channel),
            ("operation", selected_operation),
        )
    }
    effective_protocol = selected_server["protocol"]
    if effective_protocol in {"kafka", "kafka-secure"}:
        topic = bindings["channel"].get("kafka", {}).get("topic", address)
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,249}", topic) or topic in {".", ".."}:
            raise _failure("invalid_topic")
    security = {
        "server": _security(selected_server["source"].get("security", []), refs, version),
        "operation": _security(selected_operation.get("security", []), refs, version),
    }
    messages = [_message(value, refs, version) for value in raw_messages]
    selector = {
        "operation": operation,
        "channel": channel,
        "mode": mode,
        "server": server,
        "protocol": protocol,
    }
    return AsyncApiOperationSelection(
        description.uri,
        description.document_json,
        description.digest,
        _json(selector),
        version,
        action,
        "#/channels/" + channel_id.replace("~", "~0").replace("/", "~1"),
        address,
        name,
        _json(selected_server),
        _json(messages),
        _json(bindings),
        _json(security),
    )


@dataclass(frozen=True, slots=True)
class AsyncApiPreparedMessage:
    selection: AsyncApiOperationSelection
    message_index: int
    payload_json: str
    headers_json: str
    correlation_id: str | None


def prepare_message(
    selection: AsyncApiOperationSelection, message: Mapping[str, Any]
) -> AsyncApiPreparedMessage:
    selection = AsyncApiOperationSelection.from_value(selection.as_value())
    selector = json.loads(selection.selector_json)
    if (
        selector["mode"] != "publish"
        or not isinstance(message, dict)
        or set(message) - {"payload", "headers"}
    ):
        raise _failure("invalid_outbound_message")
    _finite(message)
    payload, headers = message.get("payload", {}), message.get("headers", {})
    if not isinstance(payload, dict) or not isinstance(headers, dict):
        raise _failure("invalid_outbound_message")
    return _prepare_message(selection, payload, headers)


def _prepare_message(
    selection: AsyncApiOperationSelection, payload: Any, headers: Any
) -> AsyncApiPreparedMessage:
    messages = json.loads(selection.messages_json)
    matches = [
        index
        for index, definition in enumerate(messages)
        if Draft7Validator(definition["payload_schema"]).is_valid(payload)
        and Draft7Validator(definition["headers_schema"]).is_valid(headers)
    ]
    if len(matches) != 1:
        raise _failure("message_schema_ambiguous_or_invalid")
    index = matches[0]
    location = messages[index]["correlation"]
    correlation = None
    if location is not None:
        source, _, pointer = location.partition("#")
        value = _pointer(headers if source == "$message.header" else payload, pointer)
        if not isinstance(value, str) or not value or len(value) > 1024:
            raise _failure("invalid_correlation_id")
        correlation = value
    return AsyncApiPreparedMessage(selection, index, _json(payload), _json(headers), correlation)


def validate_inbound_message(
    selection: AsyncApiOperationSelection, message: Mapping[str, Any]
) -> AsyncApiPreparedMessage:
    selection = AsyncApiOperationSelection.from_value(selection.as_value())
    if (
        json.loads(selection.selector_json)["mode"] != "subscribe"
        or not isinstance(message, dict)
        or (set(message) - {"payload", "headers", "correlationId"})
    ):
        raise _failure("invalid_inbound_message")
    _finite(message)
    payload, headers = message.get("payload", {}), message.get("headers", {})
    if not isinstance(payload, dict) or not isinstance(headers, dict):
        raise _failure("invalid_inbound_message")
    prepared = _prepare_message(selection, payload, headers)
    if "correlationId" in message and message["correlationId"] != prepared.correlation_id:
        raise _failure("correlation_id_disagrees")
    return prepared


@dataclass(frozen=True, slots=True)
class AsyncApiPreparedSubscription:
    selection: AsyncApiOperationSelection
    subscription_json: str
    consumption_kind: Literal["amount", "while", "until"]
    condition_position: Literal["count", "after", "before"]
    # This raw duration is an admission input, never a new recovery clock.
    duration_json: str


def prepare_subscription(
    selection: AsyncApiOperationSelection, subscription: Mapping[str, Any]
) -> AsyncApiPreparedSubscription:
    selection = AsyncApiOperationSelection.from_value(selection.as_value())
    if (
        json.loads(selection.selector_json)["mode"] != "subscribe"
        or not isinstance(subscription, dict)
        or (set(subscription) - {"filter", "consume", "foreach"})
    ):
        raise _failure("invalid_subscription")
    _finite(subscription)
    consume = _object(subscription.get("consume"))
    kinds = set(consume) & {"amount", "while", "until"}
    if len(kinds) != 1 or set(consume) - {"amount", "while", "until", "for"}:
        raise _failure("invalid_consumption_policy")
    kind = next(iter(kinds))
    if kind == "amount" and (type(consume[kind]) is not int or consume[kind] < 1):
        raise _failure("invalid_consumption_amount")
    for expression in (subscription.get("filter"), consume.get("while"), consume.get("until")):
        if expression is not None and (
            not isinstance(expression, str) or not re.fullmatch(r"\$\{[\s\S]+\}", expression)
        ):
            raise _failure("invalid_subscription_expression")
    if "foreach" in subscription:
        iterator = _object(subscription["foreach"])
        if set(iterator) - {"item", "at", "do", "output", "export"}:
            raise _failure("invalid_subscription_iterator")
        for name in ("item", "at"):
            if name in iterator and not re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_]*", _text(iterator[name])
            ):
                raise _failure("invalid_subscription_iterator")
        if not isinstance(iterator.get("do"), list) or not iterator["do"]:
            raise _failure("invalid_subscription_iterator")
    duration = consume.get("for")
    if duration is not None:
        from .workflow import _duration_seconds

        try:
            if not isinstance(duration, (str, dict)):
                raise ValueError
            if isinstance(duration, str) and not re.fullmatch(
                r"P(?=\d|T\d)(?:\d+(?:\.\d+)?D)?(?:T(?=\d)(?:\d+(?:\.\d+)?H)?(?:\d+(?:\.\d+)?M)?(?:\d+(?:\.\d+)?S)?)?",
                duration,
            ):
                raise ValueError
            if isinstance(duration, dict) and (
                not duration
                or set(duration) - {"days", "hours", "minutes", "seconds", "milliseconds"}
                or any(type(value) is not int or value < 0 for value in duration.values())
            ):
                raise ValueError
            seconds = _duration_seconds(duration)
            if seconds is None or seconds < 0:
                raise ValueError
        except (OwaError, ValueError, OverflowError):
            raise _failure("invalid_subscription_duration") from None
    return AsyncApiPreparedSubscription(
        selection,
        _json(dict(subscription)),
        cast(Literal["amount", "while", "until"], kind),
        "count" if kind == "amount" else "after" if kind == "while" else "before",
        _json(duration),
    )
